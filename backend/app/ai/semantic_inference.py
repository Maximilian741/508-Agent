"""Pluggable semantic inference layer.

This module exposes a simple :class:`SemanticInferenceClient` that the rest of
the backend can use for AI-assisted features (alt-text generation, link-text
rewriting, document title/language guessing, etc.).

It supports three deployment modes:

* **Heuristic** (default): no network, no API key required. Returns reasonable,
  context-derived outputs so the deterministic pipeline never blocks waiting on
  an LLM.
* **Anthropic Claude**: enabled when ``ANTHROPIC_API_KEY`` is set in the
  environment.  Falls back to heuristic on any error.
* **OpenAI**: enabled when ``OPENAI_API_KEY`` is set; same fallback semantics.

Adding a new provider is a matter of subclassing :class:`SemanticInferenceProvider`
and returning it from :func:`build_default_provider`.

The module intentionally has no hard dependency on the ``anthropic``/``openai``
SDKs.  Where available, the client uses :mod:`urllib` and the public REST APIs
so the layer can run in lean container images.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InferenceResult:
    text: str
    confidence: float
    provider: str
    raw: Optional[Dict[str, Any]] = None


class SemanticInferenceProvider(ABC):
    """Abstract semantic inference provider."""

    name: str = "abstract"

    @abstractmethod
    def alt_text(self, payload: Dict[str, Any]) -> InferenceResult:
        raise NotImplementedError

    @abstractmethod
    def link_text(self, payload: Dict[str, Any]) -> InferenceResult:
        raise NotImplementedError

    @abstractmethod
    def document_title(self, payload: Dict[str, Any]) -> InferenceResult:
        raise NotImplementedError

    @abstractmethod
    def document_language(self, payload: Dict[str, Any]) -> InferenceResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Heuristic provider
# ---------------------------------------------------------------------------


_LANGUAGE_PATTERNS = [
    (re.compile(r"\b(the|and|of|to|in|is|for|with|on)\b", re.IGNORECASE), "en"),
    (re.compile(r"\b(le|la|les|de|et|une|des|pour)\b", re.IGNORECASE), "fr"),
    (re.compile(r"\b(el|la|los|de|y|para|una|que)\b", re.IGNORECASE), "es"),
    (re.compile(r"\b(der|die|das|und|von|zu|ein|nicht)\b", re.IGNORECASE), "de"),
    (re.compile(r"\b(il|la|le|di|e|per|un|una)\b", re.IGNORECASE), "it"),
]


def _slugify(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value or "").strip()
    if not cleaned:
        return ""
    if len(cleaned) > 80:
        cleaned = cleaned[:77].rstrip() + "…"
    return cleaned


class HeuristicProvider(SemanticInferenceProvider):
    """Offline provider that derives outputs from local context."""

    name = "heuristic"

    def alt_text(self, payload: Dict[str, Any]) -> InferenceResult:
        label = _slugify(str(payload.get("label") or "Image"))
        location = str(payload.get("location") or "document")
        # Prefer an explicit page or slide number; trust the caller's choice
        # of key.  If only one is set, derive the location string from it; if
        # both are set, the page wins (PDFs/DOCX), else the slide wins.
        page = payload.get("page")
        slide = payload.get("slide")
        if page:
            location = f"page {page}"
        elif slide:
            location = f"slide {slide}"
        nearby_text = _slugify(str(payload.get("context") or payload.get("caption") or ""))
        if nearby_text:
            text = f"{label} — {nearby_text}"
        else:
            text = f"{label} shown in {location}."
        return InferenceResult(text=text, confidence=0.4, provider=self.name)

    def link_text(self, payload: Dict[str, Any]) -> InferenceResult:
        target = str(payload.get("target") or "").strip()
        original = str(payload.get("text") or "").strip()
        if target:
            try:
                parsed = urlparse(target)
                host = parsed.netloc or parsed.path
                host = re.sub(r"^www\.", "", host)
                title = host.split("/")[0] if host else target
                if title:
                    fallback = f"Visit {title}" if not original else f"Read more about {title}"
                    return InferenceResult(text=fallback, confidence=0.45, provider=self.name)
            except Exception:
                pass
        if original:
            return InferenceResult(
                text=f"Read more about {original}", confidence=0.3, provider=self.name
            )
        return InferenceResult(text="Open linked resource", confidence=0.2, provider=self.name)

    def document_title(self, payload: Dict[str, Any]) -> InferenceResult:
        filename = str(payload.get("filename") or payload.get("doc_id") or "Document")
        stem = re.sub(r"\.[^.]+$", "", filename)
        cleaned = re.sub(r"[_\-]+", " ", stem).strip()
        cleaned = re.sub(r"\s+", " ", cleaned).title() or "Untitled Document"
        first_heading = str(payload.get("firstHeading") or "").strip()
        if first_heading and 2 <= len(first_heading) <= 120:
            return InferenceResult(text=first_heading, confidence=0.55, provider=self.name)
        return InferenceResult(text=cleaned, confidence=0.35, provider=self.name)

    def document_language(self, payload: Dict[str, Any]) -> InferenceResult:
        sample = str(payload.get("sample") or "")
        if not sample.strip():
            return InferenceResult(text="en", confidence=0.2, provider=self.name)
        scores: Dict[str, int] = {}
        for pattern, code in _LANGUAGE_PATTERNS:
            scores[code] = scores.get(code, 0) + len(pattern.findall(sample))
        if not any(scores.values()):
            return InferenceResult(text="en", confidence=0.25, provider=self.name)
        best = max(scores.items(), key=lambda kv: kv[1])
        confidence = min(0.85, 0.3 + 0.05 * best[1])
        return InferenceResult(text=best[0], confidence=confidence, provider=self.name)


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------


_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class ClaudeProvider(SemanticInferenceProvider):
    """Talks to the Anthropic Messages API.  Falls back to heuristic on error."""

    name = "claude"

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        timeout: float = 12.0,
        fallback: Optional[SemanticInferenceProvider] = None,
    ) -> None:
        self.api_key = api_key
        self.model = model or os.getenv("ANTHROPIC_MODEL", _ANTHROPIC_DEFAULT_MODEL)
        self.timeout = timeout
        self.fallback = fallback or HeuristicProvider()

    # -- Public API ------------------------------------------------------

    def alt_text(self, payload: Dict[str, Any]) -> InferenceResult:
        prompt = _alt_text_prompt(payload)
        image_b64 = payload.get("image_b64") or payload.get("imageBase64")
        image_mime = payload.get("image_mime") or payload.get("imageMime") or "image/png"
        if image_b64:
            return self._respond_multimodal(
                prompt,
                image_b64=str(image_b64),
                image_mime=str(image_mime),
                default_payload=payload,
                fallback=self.fallback.alt_text,
                confidence=0.82,
            )
        return self._respond(prompt, default_payload=payload, fallback=self.fallback.alt_text)

    def link_text(self, payload: Dict[str, Any]) -> InferenceResult:
        prompt = _link_text_prompt(payload)
        return self._respond(prompt, default_payload=payload, fallback=self.fallback.link_text)

    def document_title(self, payload: Dict[str, Any]) -> InferenceResult:
        prompt = _title_prompt(payload)
        return self._respond(prompt, default_payload=payload, fallback=self.fallback.document_title)

    def document_language(self, payload: Dict[str, Any]) -> InferenceResult:
        prompt = _language_prompt(payload)
        result = self._respond(prompt, default_payload=payload, fallback=self.fallback.document_language)
        # Coerce to ISO-639-1 short code when we can.
        text = result.text.strip().lower()
        match = re.search(r"\b([a-z]{2})\b", text)
        if match:
            return InferenceResult(text=match.group(1), confidence=result.confidence, provider=result.provider, raw=result.raw)
        return result

    # -- Internals -------------------------------------------------------

    def _respond(
        self,
        prompt: str,
        *,
        default_payload: Dict[str, Any],
        fallback: Any,
    ) -> InferenceResult:
        try:
            text, raw = self._call_messages(prompt)
            text = text.strip()
            if not text:
                raise RuntimeError("empty response")
            return InferenceResult(text=text, confidence=0.75, provider=self.name, raw=raw)
        except Exception as exc:  # pragma: no cover - network paths
            logger.warning("ClaudeProvider falling back to heuristic: %s", exc)
            return fallback(default_payload)

    def _respond_multimodal(
        self,
        prompt: str,
        *,
        image_b64: str,
        image_mime: str,
        default_payload: Dict[str, Any],
        fallback: Any,
        confidence: float,
    ) -> InferenceResult:
        try:
            text, raw = self._call_messages_multimodal(prompt, image_b64, image_mime)
            text = text.strip()
            if not text:
                raise RuntimeError("empty response")
            return InferenceResult(text=text, confidence=confidence, provider=self.name, raw=raw)
        except Exception as exc:  # pragma: no cover - network paths
            logger.warning("ClaudeProvider multimodal fallback: %s", exc)
            return fallback(default_payload)

    def _call_messages(self, prompt: str) -> tuple[str, Dict[str, Any]]:
        body = {
            "model": self.model,
            "max_tokens": 200,
            "messages": [
                {"role": "user", "content": prompt},
            ],
        }
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            _ANTHROPIC_URL,
            data=data,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        text = ""
        for block in payload.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text += str(block.get("text") or "")
        return text, payload

    def _call_messages_multimodal(
        self, prompt: str, image_b64: str, image_mime: str
    ) -> tuple[str, Dict[str, Any]]:
        body = {
            "model": self.model,
            "max_tokens": 200,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": image_mime,
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                },
            ],
        }
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            _ANTHROPIC_URL,
            data=data,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        text = ""
        for block in payload.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text += str(block.get("text") or "")
        return text, payload


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------


_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_OPENAI_DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIProvider(SemanticInferenceProvider):
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        timeout: float = 12.0,
        fallback: Optional[SemanticInferenceProvider] = None,
    ) -> None:
        self.api_key = api_key
        self.model = model or os.getenv("OPENAI_MODEL", _OPENAI_DEFAULT_MODEL)
        self.timeout = timeout
        self.fallback = fallback or HeuristicProvider()

    def alt_text(self, payload: Dict[str, Any]) -> InferenceResult:
        image_b64 = payload.get("image_b64") or payload.get("imageBase64")
        image_mime = payload.get("image_mime") or payload.get("imageMime") or "image/png"
        if image_b64:
            return self._respond_multimodal(
                _alt_text_prompt(payload),
                image_b64=str(image_b64),
                image_mime=str(image_mime),
                default_payload=payload,
                fallback=self.fallback.alt_text,
            )
        return self._respond(_alt_text_prompt(payload), payload, self.fallback.alt_text)

    def link_text(self, payload: Dict[str, Any]) -> InferenceResult:
        return self._respond(_link_text_prompt(payload), payload, self.fallback.link_text)

    def document_title(self, payload: Dict[str, Any]) -> InferenceResult:
        return self._respond(_title_prompt(payload), payload, self.fallback.document_title)

    def document_language(self, payload: Dict[str, Any]) -> InferenceResult:
        result = self._respond(_language_prompt(payload), payload, self.fallback.document_language)
        match = re.search(r"\b([a-z]{2})\b", result.text.lower())
        if match:
            return InferenceResult(text=match.group(1), confidence=result.confidence, provider=result.provider, raw=result.raw)
        return result

    def _respond_multimodal(
        self,
        prompt: str,
        *,
        image_b64: str,
        image_mime: str,
        default_payload: Dict[str, Any],
        fallback: Any,
    ) -> InferenceResult:
        try:
            body = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Write concise, objective alt text in one sentence. No speculation.",
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{image_mime};base64,{image_b64}",
                                },
                            },
                        ],
                    },
                ],
                "temperature": 0.2,
                "max_tokens": 200,
            }
            data = json.dumps(body).encode("utf-8")
            request = urllib.request.Request(
                _OPENAI_URL,
                data=data,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            text = (
                payload.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            text = str(text or "").strip()
            if not text:
                raise RuntimeError("empty response")
            return InferenceResult(text=text, confidence=0.78, provider=self.name, raw=payload)
        except Exception as exc:  # pragma: no cover
            logger.warning("OpenAIProvider multimodal fallback: %s", exc)
            return fallback(default_payload)

    def _respond(
        self,
        prompt: str,
        default_payload: Dict[str, Any],
        fallback: Any,
    ) -> InferenceResult:
        try:
            body = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "You output only the requested string. No prefix, no quotes, no explanations."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 200,
            }
            data = json.dumps(body).encode("utf-8")
            request = urllib.request.Request(
                _OPENAI_URL,
                data=data,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            text = (
                payload.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            text = str(text or "").strip()
            if not text:
                raise RuntimeError("empty response")
            return InferenceResult(text=text, confidence=0.7, provider=self.name, raw=payload)
        except Exception as exc:  # pragma: no cover - network paths
            logger.warning("OpenAIProvider falling back to heuristic: %s", exc)
            return fallback(default_payload)


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------


def _alt_text_prompt(payload: Dict[str, Any]) -> str:
    label = payload.get("label") or "image"
    context = payload.get("context") or payload.get("caption") or ""
    location = payload.get("location") or ""
    return (
        "Write concise, objective alt text in one sentence (max 120 chars) "
        "for the following image.  Do not start with 'image of' or 'picture of'.\n"
        f"Image label: {label}\n"
        f"Caption / surrounding text: {context or '(none)'}\n"
        f"Location: {location or '(unknown)'}\n"
        "Return only the alt text."
    )


def _link_text_prompt(payload: Dict[str, Any]) -> str:
    original = payload.get("text") or ""
    target = payload.get("target") or ""
    return (
        "Rewrite the following hyperlink label so it is descriptive and reads well "
        "out of context.  Avoid 'click here', 'read more', or generic phrases. "
        "Return only the new label as a short phrase.\n"
        f"Original label: {original}\n"
        f"Target URL: {target}"
    )


def _title_prompt(payload: Dict[str, Any]) -> str:
    filename = payload.get("filename") or payload.get("doc_id") or "Document"
    first_heading = payload.get("firstHeading") or ""
    sample = payload.get("sample") or ""
    return (
        "Suggest a short, descriptive document title (max 80 chars). "
        "Return only the title.\n"
        f"Filename: {filename}\n"
        f"First heading: {first_heading or '(none)'}\n"
        f"Excerpt: {sample[:600]}"
    )


def _language_prompt(payload: Dict[str, Any]) -> str:
    sample = payload.get("sample") or ""
    return (
        "Identify the primary language of the following text. Reply with only the ISO-639-1 "
        "two-letter code (for example 'en', 'fr').\n\n"
        f"{sample[:1500]}"
    )


# ---------------------------------------------------------------------------
# Public client + factory
# ---------------------------------------------------------------------------


_MODEL_PRICES_USD_PER_M_TOKENS: Dict[str, tuple[float, float]] = {
    # (input price per million, output price per million). Values are
    # rough catalog rates as of mid-2025; update when providers shift.
    # Anthropic.
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-3-5-sonnet-20241022": (3.0, 15.0),
    "claude-3-5-sonnet-20240620": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-3-5-haiku-20241022": (1.0, 5.0),
    "claude-3-haiku-20240307": (0.25, 1.25),
    "claude-opus-4-6": (15.0, 75.0),
    # OpenAI.
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "gpt-4.1-mini": (0.40, 1.60),
}

# Conservative fallback for unknown models so a typo or new release does
# not silently bypass the cap. Picked to roughly match Sonnet pricing.
_UNKNOWN_MODEL_PRICE_USD_PER_M_TOKENS = (3.0, 15.0)


def _estimate_call_cost_usd(raw: Optional[Dict[str, Any]]) -> float:
    """Best-effort cost estimate from a provider response.

    Reads token usage out of the raw response and applies the per-model
    price table. Returns 0 for heuristic results, missing usage, or
    anything we cannot parse - we never want to over-charge.
    """
    if not isinstance(raw, dict):
        return 0.0
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        return 0.0
    # Anthropic uses input_tokens / output_tokens, OpenAI uses
    # prompt_tokens / completion_tokens. Accept either.
    input_tokens = (
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or 0
    )
    output_tokens = (
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or 0
    )
    try:
        input_tokens = int(input_tokens)
        output_tokens = int(output_tokens)
    except (TypeError, ValueError):
        return 0.0
    if input_tokens <= 0 and output_tokens <= 0:
        return 0.0
    model = str(raw.get("model") or "").strip()
    in_rate, out_rate = _MODEL_PRICES_USD_PER_M_TOKENS.get(
        model, _UNKNOWN_MODEL_PRICE_USD_PER_M_TOKENS
    )
    return (input_tokens / 1_000_000.0) * in_rate + (output_tokens / 1_000_000.0) * out_rate


class SemanticInferenceClient:
    """Facade used by the rest of the backend.

    Methods accept loosely-typed dicts so callers do not need to import the
    provider abstraction.  ``infer()`` is kept for backwards compatibility with
    the previous stub.

    The client caches results per (kind, cache_key) so repeated calls during a
    single request - for example, the planner and the executor each asking for
    alt text for the same image - only hit the provider once.

    Per-job cost cap: pass ``max_cost_usd > 0`` (or rely on the
    ``MAX_AI_COST_PER_JOB_USD`` setting) to cap AI spend per client
    instance. When the running tally exceeds the cap, the client switches
    to a HeuristicProvider for the remaining calls so a runaway image-
    heavy document cannot blow your margin.
    """

    _MAX_CACHE = 256

    def __init__(
        self,
        provider: Optional[SemanticInferenceProvider] = None,
        max_cost_usd: Optional[float] = None,
    ) -> None:
        self.provider: SemanticInferenceProvider = provider or build_default_provider()
        self._cache: Dict[tuple[str, str], InferenceResult] = {}
        # Resolve the cap. Explicit arg wins; otherwise fall back to the
        # app config; otherwise no cap. We import lazily to avoid a hard
        # dependency on app.config (helpful for unit tests).
        if max_cost_usd is None:
            try:
                from app.config import get_settings  # local import to avoid cycles
                max_cost_usd = float(get_settings().max_ai_cost_per_job_usd)
            except Exception:
                max_cost_usd = 0.0
        self.max_cost_usd: float = max_cost_usd or 0.0
        self.cost_so_far_usd: float = 0.0
        self.cost_capped: bool = False
        self._heuristic_fallback: SemanticInferenceProvider = HeuristicProvider()

    @property
    def provider_name(self) -> str:
        return self.provider.name

    @staticmethod
    def _cache_key(kind: str, payload: Dict[str, Any]) -> Optional[str]:
        # We deliberately exclude the image bytes so we don't blow up the cache.
        bits: List[str] = [kind]
        for key in ("label", "location", "page", "context", "caption", "text", "target", "filename", "firstHeading", "sample"):
            value = payload.get(key)
            if value is None:
                continue
            bits.append(f"{key}={str(value)[:200]}")
        if not bits:
            return None
        return "|".join(bits)

    def infer(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Backwards-compatible router used by older callers.

        Dispatches based on ``payload['kind']`` (one of ``alt_text``,
        ``link_text``, ``document_title``, ``document_language``).
        """

        kind = str(payload.get("kind") or "alt_text")
        result = self._dispatch(kind, payload)
        return {
            "kind": kind,
            "text": result.text,
            "confidence": result.confidence,
            "provider": result.provider,
        }

    def _dispatch(self, kind: str, payload: Dict[str, Any]) -> InferenceResult:
        cache_key = self._cache_key(kind, payload)
        if cache_key and cache_key in self._cache:
            return self._cache[cache_key]

        # Pick the active provider. If we already blew the cap on a prior
        # call within this job, route everything to heuristic for the
        # rest of the request so the user still gets a result.
        active = self.provider
        if (
            self.max_cost_usd > 0
            and self.cost_so_far_usd >= self.max_cost_usd
        ):
            self.cost_capped = True
            active = self._heuristic_fallback

        if kind == "link_text":
            result = active.link_text(payload)
        elif kind == "document_title":
            result = active.document_title(payload)
        elif kind == "document_language":
            result = active.document_language(payload)
        else:
            result = active.alt_text(payload)

        # Tally cost from the provider's raw response. Heuristic results
        # have no usage and contribute zero, so the loop is safe to run
        # unconditionally.
        try:
            self.cost_so_far_usd += _estimate_call_cost_usd(result.raw)
        except Exception as exc:  # never fail user request because of accounting
            logger.warning("ai cost accounting failed: %s", exc)

        if cache_key:
            if len(self._cache) >= self._MAX_CACHE:
                # Drop the oldest insertion to keep memory bounded.
                self._cache.pop(next(iter(self._cache)))
            self._cache[cache_key] = result
        return result

    # Convenience helpers -------------------------------------------------

    def suggest_alt_text(self, **payload: Any) -> InferenceResult:
        return self._dispatch("alt_text", payload)

    def suggest_link_text(self, **payload: Any) -> InferenceResult:
        return self._dispatch("link_text", payload)

    def suggest_document_title(self, **payload: Any) -> InferenceResult:
        return self._dispatch("document_title", payload)

    def detect_document_language(self, **payload: Any) -> InferenceResult:
        return self._dispatch("document_language", payload)


def build_default_provider() -> SemanticInferenceProvider:
    """Pick a provider based on environment variables.

    Order of precedence:
        1. ``SEMANTIC_PROVIDER`` env var (``heuristic`` | ``claude`` | ``openai``)
        2. ``ANTHROPIC_API_KEY`` present → :class:`ClaudeProvider`
        3. ``OPENAI_API_KEY`` present → :class:`OpenAIProvider`
        4. Otherwise → :class:`HeuristicProvider`.
    """

    forced = (os.getenv("SEMANTIC_PROVIDER") or "").strip().lower()
    anthropic_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()

    if forced == "heuristic":
        return HeuristicProvider()
    if forced == "claude":
        if anthropic_key:
            return ClaudeProvider(api_key=anthropic_key)
        logger.warning("SEMANTIC_PROVIDER=claude but ANTHROPIC_API_KEY missing; using heuristic")
        return HeuristicProvider()
    if forced == "openai":
        if openai_key:
            return OpenAIProvider(api_key=openai_key)
        logger.warning("SEMANTIC_PROVIDER=openai but OPENAI_API_KEY missing; using heuristic")
        return HeuristicProvider()

    if anthropic_key:
        return ClaudeProvider(api_key=anthropic_key)
    if openai_key:
        return OpenAIProvider(api_key=openai_key)
    return HeuristicProvider()


__all__ = [
    "SemanticInferenceClient",
    "SemanticInferenceProvider",
    "HeuristicProvider",
    "ClaudeProvider",
    "OpenAIProvider",
    "InferenceResult",
    "build_default_provider",
]
