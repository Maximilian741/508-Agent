"""Pluggable semantic inference layer.

This module exposes a simple :class:`SemanticInferenceClient` that the rest of
the backend can use for AI-assisted features (alt-text generation, link-text
rewriting, document title/language guessing, etc.).

It supports three deployment modes:

* **Heuristic** (default): no network, no API key required. Answers only from
  words the document already carries for that purpose (a figure's caption, a
  link address's words, the text's own function words) and otherwise ABSTAINS
  with a reason, so nothing it cannot stand behind reaches a customer's file.
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

    @abstractmethod
    def table_caption(self, payload: Dict[str, Any]) -> InferenceResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Heuristic provider
# ---------------------------------------------------------------------------


# Latin-script language detection lives in app.ai.offline_rules
# (detect_latin_language): distinctive function words only, a margin over the
# runner-up, and ABSTAIN below it. The old five-pattern table here shared
# "de"/"la" between French and Spanish and broke ties by dict order, which
# wrote lang="fr" into Spanish pages and charged for it.


# Unicode block -> BCP-47 tag, for scripts that identify a language on their
# own. Han is deliberately absent from the single-language list: CJK ideographs
# are shared by zh/ja/ko, so bare Han is only "zh" when NO kana/hangul appear.
_SCRIPT_RANGES = [
    ((0x3040, 0x30FF), "ja"),  # Hiragana + Katakana
    ((0xAC00, 0xD7AF), "ko"),  # Hangul syllables
    ((0x1100, 0x11FF), "ko"),  # Hangul Jamo
    ((0x0600, 0x06FF), "ar"),  # Arabic
    ((0x0590, 0x05FF), "he"),  # Hebrew
    ((0x0400, 0x04FF), "ru"),  # Cyrillic (ru is the dominant case; still a guess)
    ((0x0370, 0x03FF), "el"),  # Greek
    ((0x0E00, 0x0E7F), "th"),  # Thai
    ((0x0900, 0x097F), "hi"),  # Devanagari
]
_HAN_RANGE = (0x4E00, 0x9FFF)


def _dominant_script_language(sample: str):
    """``(bcp47, share)`` when one non-Latin script clearly dominates the
    LETTERS of ``sample``, else None. Digits/punctuation are ignored so a
    table of numbers cannot vote."""
    counts: Dict[str, int] = {}
    latin = 0
    han = 0
    letters = 0
    for ch in sample:
        if not ch.isalpha():
            continue
        letters += 1
        cp = ord(ch)
        if cp < 0x0250:
            latin += 1
            continue
        if _HAN_RANGE[0] <= cp <= _HAN_RANGE[1]:
            han += 1
            continue
        for (lo, hi), code in _SCRIPT_RANGES:
            if lo <= cp <= hi:
                counts[code] = counts.get(code, 0) + 1
                break
    if letters == 0:
        return None
    # Kana/hangul present -> that language owns any Han too.
    if counts.get("ja"):
        counts["ja"] += han
        han = 0
    elif counts.get("ko"):
        counts["ko"] += han
        han = 0
    if han and not counts:
        counts["zh"] = han
    if not counts:
        return None
    code, n = max(counts.items(), key=lambda kv: kv[1])
    share = n / letters
    # Require a clear majority of LETTERS; below that we do not know.
    return (code, share) if share >= 0.6 else None


def _abstain(provider: str, reason: str) -> InferenceResult:
    """An honest "no answer": empty text, zero confidence, and the reason in
    ``raw['refusal']`` so the executor can tell the customer why."""
    return InferenceResult(text="", confidence=0.0, provider=provider, raw={"refusal": reason})


def refusal_reason(result: Optional[InferenceResult]) -> Optional[str]:
    """The plain-English reason a provider abstained, if it gave one."""
    raw = getattr(result, "raw", None)
    if isinstance(raw, dict):
        reason = raw.get("refusal")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()
    return None


class HeuristicProvider(SemanticInferenceProvider):
    """Offline provider: writes only what the document itself already says.

    It has no eyes and no world knowledge, so every method either derives its
    answer from words a person already wrote for that exact purpose (a
    figure's caption, the words in a link's address, the document's own
    function words) or ABSTAINS with a reason. The rules and their catalog of
    good and bad cases live in :mod:`app.ai.offline_rules`.
    """

    name = "heuristic"

    def alt_text(self, payload: Dict[str, Any]) -> InferenceResult:
        from app.ai.offline_rules import alt_from_caption

        # Only the parser's caption, with where it came from. The old version
        # also took "context" (whatever text sat above the picture) and
        # prefixed an internal node id, which is how "Image html-img-1 — Home
        # About Contact Login" ended up in customers' files.
        verdict = alt_from_caption(payload.get("caption"), payload.get("caption_source"))
        if not verdict.ok:
            return _abstain(self.name, verdict.reason)
        return InferenceResult(text=verdict.text or "", confidence=0.6, provider=self.name)

    def link_text(self, payload: Dict[str, Any]) -> InferenceResult:
        from app.ai.offline_rules import link_text_from_target

        verdict = link_text_from_target(payload.get("text"), payload.get("target"))
        if not verdict.ok:
            return _abstain(self.name, verdict.reason)
        return InferenceResult(text=verdict.text or "", confidence=0.55, provider=self.name)

    def document_title(self, payload: Dict[str, Any]) -> InferenceResult:
        from app.ai.offline_rules import title_from_filename, title_from_heading

        first_heading = payload.get("firstHeading")
        if first_heading:
            verdict = title_from_heading(first_heading)
            if verdict.ok:
                return InferenceResult(text=verdict.text or "", confidence=0.55, provider=self.name)
        verdict = title_from_filename(payload.get("filename"))
        if verdict.ok:
            return InferenceResult(text=verdict.text or "", confidence=0.45, provider=self.name)
        return _abstain(self.name, verdict.reason)

    def document_language(self, payload: Dict[str, Any]) -> InferenceResult:
        """Guess the language of ``sample``, or ABSTAIN (empty text).

        Two sources of evidence, in order of reliability:

        1. Script. Unicode block ranges are unambiguous: a page of Hiragana is
           Japanese, of Hangul is Korean, of Arabic letters is Arabic. This is
           checked FIRST because the word-list test below only knows Latin
           languages, and it used to answer "en at 0.25" for anything it did
           not recognize — which is how an all-Japanese PDF got /Lang en
           written into it and credited as a fix.
        2. Latin function words, for the handful of Latin languages we list.

        When neither source finds anything, return an EMPTY text at zero
        confidence rather than a default. Abstaining is a real answer; the
        executor turns it into "needs a human", which is the truth.
        """
        sample = str(payload.get("sample") or "")
        if not sample.strip():
            return InferenceResult(text="", confidence=0.0, provider=self.name)

        script = _dominant_script_language(sample)
        if script is not None:
            code, share = script
            # Share of letters in that script -> confidence; a mixed page
            # (e.g. English with a few CJK names) will not clear the bar.
            return InferenceResult(text=code, confidence=min(0.9, 0.5 + 0.4 * share), provider=self.name)

        from app.ai.offline_rules import detect_latin_language

        code, confidence, detail = detect_latin_language(sample)
        if not code:
            return _abstain(self.name, f"the text does not clearly read as one language ({detail})")
        return InferenceResult(text=code, confidence=confidence, provider=self.name)

    def table_caption(self, payload: Dict[str, Any]) -> InferenceResult:
        # A caption says what a table is ABOUT, in the author's words. The
        # column names are already announced by the header row, so a list of
        # them ("Table: Region, Q1, Q2") is not a caption, and a first data
        # row ("Table: 2023, 410, 12%") or "Data table" is worse. Abstain.
        return _abstain(
            self.name,
            "A table caption has to say what the table is about in the author's words; "
            "we don't make one up from the column names.",
        )


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

    def table_caption(self, payload: Dict[str, Any]) -> InferenceResult:
        prompt = _table_caption_prompt(payload)
        return self._respond(prompt, default_payload=payload, fallback=self.fallback.table_caption)

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

    def table_caption(self, payload: Dict[str, Any]) -> InferenceResult:
        return self._respond(_table_caption_prompt(payload), payload, self.fallback.table_caption)

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


def _table_caption_prompt(payload: Dict[str, Any]) -> str:
    headers = ", ".join(str(h) for h in (payload.get("headers") or []) if str(h).strip())
    sample = str(payload.get("sample") or "")
    return (
        "Write a short, descriptive caption (a title) for an HTML data table, so a "
        "screen-reader user knows what the table contains before reading it. Base it "
        "ONLY on the column headers and sample rows below — do not invent facts or "
        "numbers. Return ONLY the caption as a concise noun phrase (no trailing period, "
        "no 'This table').\n"
        f"Column headers: {headers or '(none)'}\n"
        f"Sample rows: {sample[:500] or '(none)'}"
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
        for key in ("node_id", "label", "location", "page", "context", "caption", "caption_source", "text", "target", "filename", "firstHeading", "sample", "headers"):
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
        elif kind == "table_caption":
            result = active.table_caption(payload)
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

    def suggest_table_caption(self, **payload: Any) -> InferenceResult:
        return self._dispatch("table_caption", payload)

    def suggest_document_title(self, **payload: Any) -> InferenceResult:
        return self._dispatch("document_title", payload)

    def detect_document_language(self, **payload: Any) -> InferenceResult:
        return self._dispatch("document_language", payload)


def vision_provider_configured() -> bool:
    """True when the resolved default provider can LOOK at an image.

    Cheap, no network, no client construction — mirrors the precedence in
    :func:`build_default_provider`. Parsers use it to decide whether inlining
    image bytes into the tree could ever pay off: under the heuristic
    provider nothing reads them, and a 24 MB scan-like PDF was costing +94 MB
    RSS per /analyze request (four concurrent: +250 MB) for bytes that were
    then discarded.
    """
    forced = (os.getenv("SEMANTIC_PROVIDER") or "").strip().lower()
    if forced == "heuristic":
        return False
    anthropic_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if forced == "claude":
        return bool(anthropic_key)
    if forced == "openai":
        return bool(openai_key)
    return bool(anthropic_key or openai_key)


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
    "vision_provider_configured",
    "SemanticInferenceProvider",
    "HeuristicProvider",
    "ClaudeProvider",
    "OpenAIProvider",
    "InferenceResult",
    "build_default_provider",
]
