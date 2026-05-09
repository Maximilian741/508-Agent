from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
UTC = timezone.utc
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib import request as urlrequest

from pypdf import PdfReader


MAX_ALT_CHARS = 140


@dataclass
class AltDecision:
    action: str  # approve | escalate | reject
    approved_text: Optional[str]
    confidence: float
    rationale: str
    model: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "action": self.action,
            "approvedText": self.approved_text,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "model": self.model,
        }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def is_alt_text_manual_item(item: Dict[str, object]) -> bool:
    issue_id = str(item.get("issueId") or "").lower()
    reason = str(item.get("reason") or "").lower()
    return "missing_alt_text" in issue_id or "issue-alt" in issue_id or "alt text" in reason


def _snippet(text: str, max_len: int = 800) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3].rstrip() + "..."


def build_alt_context(item: Dict[str, object], pdf_path: Path) -> Dict[str, object]:
    anchors = item.get("anchors")
    page_num = None
    mcid = None
    if isinstance(anchors, list):
        for entry in anchors:
            if isinstance(entry, dict):
                if isinstance(entry.get("page"), int):
                    page_num = int(entry["page"])
                if isinstance(entry.get("mcid"), int):
                    mcid = int(entry["mcid"])
                if page_num is not None:
                    break
    if page_num is None:
        anchor = item.get("anchor")
        if isinstance(anchor, dict) and isinstance(anchor.get("page"), int):
            page_num = int(anchor["page"])
            if isinstance(anchor.get("mcid"), int):
                mcid = int(anchor["mcid"])

    out: Dict[str, object] = {
        "docType": "pdf",
        "page": page_num,
        "mcid": mcid,
        "issueId": item.get("issueId"),
        "reason": item.get("reason"),
        "notes": item.get("notes"),
        "untrustedInput": True,
    }
    if page_num is None:
        out["buildStatus"] = "missing_anchor"
        return out
    if not pdf_path.exists():
        out["buildStatus"] = "missing_source"
        return out

    try:
        reader = PdfReader(str(pdf_path), strict=False)
        if page_num <= 0 or page_num > len(reader.pages):
            out["buildStatus"] = "page_out_of_range"
            return out
        page = reader.pages[page_num - 1]
        page_text = page.extract_text() or ""
        out["pageTextSnippet"] = _snippet(page_text, 1200)
        out["buildStatus"] = "ok"
        return out
    except Exception as exc:
        out["buildStatus"] = f"error:{exc.__class__.__name__}"
        return out


def validate_alt_text(candidate: str) -> Tuple[bool, str]:
    text = re.sub(r"\s+", " ", (candidate or "")).strip()
    if not text:
        return False, "empty"
    if len(text) < 4:
        return False, "too_short"
    if len(text) > MAX_ALT_CHARS:
        return False, "too_long"
    lowered = text.lower()
    banned_prefixes = [
        "image of",
        "picture of",
        "photo of",
        "graphic of",
    ]
    if any(lowered.startswith(prefix) for prefix in banned_prefixes):
        return False, "generic_prefix"
    if lowered in {"decorative", "n/a", "none", "no alt"}:
        return False, "generic_value"
    return True, "pass"


def _heuristic_fallback(context: Dict[str, object]) -> AltDecision:
    snippet = str(context.get("pageTextSnippet") or "").strip()
    if not snippet:
        return AltDecision(
            action="escalate",
            approved_text=None,
            confidence=0.0,
            rationale="Insufficient context to propose alt text.",
            model="heuristic-fallback",
        )
    sentence = snippet.split(".")[0].strip()
    candidate = sentence[:MAX_ALT_CHARS].strip()
    ok, reason = validate_alt_text(candidate)
    if not ok:
        return AltDecision(
            action="escalate",
            approved_text=None,
            confidence=0.0,
            rationale=f"Heuristic candidate failed validation: {reason}.",
            model="heuristic-fallback",
        )
    return AltDecision(
        action="approve",
        approved_text=candidate,
        confidence=0.55,
        rationale="Heuristic fallback from nearby page text.",
        model="heuristic-fallback",
    )


def _openai_propose(context: Dict[str, object]) -> AltDecision:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"
    if not api_key:
        return _heuristic_fallback(context)

    schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["approve", "escalate", "reject"]},
            "approvedText": {"type": ["string", "null"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "rationale": {"type": "string"},
        },
        "required": ["action", "approvedText", "confidence", "rationale"],
        "additionalProperties": False,
    }
    prompt = (
        "You are assisting accessibility remediation. Return only JSON matching schema. "
        "For missing alt text: propose concise, objective alt text if context is sufficient; otherwise escalate. "
        "Avoid generic prefixes like 'image of'."
    )
    body = {
        "model": model,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": prompt}]},
            {"role": "user", "content": [{"type": "input_text", "text": json.dumps(context)}]},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "alt_decision",
                "schema": schema,
                "strict": True,
            }
        },
    }
    req = urlrequest.Request(
        "https://api.openai.com/v1/responses",
        method="POST",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urlrequest.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        output_text = str(payload.get("output_text") or "").strip()
        parsed = json.loads(output_text) if output_text else {}
        action = str(parsed.get("action") or "escalate").strip().lower()
        approved = parsed.get("approvedText")
        approved_text = str(approved).strip() if isinstance(approved, str) else None
        confidence = float(parsed.get("confidence") or 0.0)
        rationale = str(parsed.get("rationale") or "No rationale provided.")
        return AltDecision(
            action=action if action in {"approve", "escalate", "reject"} else "escalate",
            approved_text=approved_text,
            confidence=max(0.0, min(1.0, confidence)),
            rationale=rationale,
            model=model,
        )
    except Exception as exc:
        fallback = _heuristic_fallback(context)
        fallback.rationale = f"{fallback.rationale} OpenAI unavailable: {exc.__class__.__name__}."
        return fallback


def propose_alt_text(item: Dict[str, object], context: Dict[str, object]) -> Dict[str, object]:
    decision = _openai_propose(context)
    approved_text = decision.approved_text or ""
    validator_ok, validator_status = validate_alt_text(approved_text) if approved_text else (False, "empty")
    out = {
        "aiDecision": decision.as_dict(),
        "aiConfidence": float(decision.confidence),
        "aiStatus": "proposed",
        "validatorStatus": validator_status if decision.action == "approve" else "not_applicable",
        "aiModel": decision.model,
        "aiUpdatedAt": _now_iso(),
    }
    # Preserve deterministic safety posture.
    if decision.action == "approve" and not validator_ok:
        out["aiStatus"] = "escalated"
    if str(context.get("buildStatus") or "") != "ok":
        out["aiStatus"] = "escalated"
    return out


def apply_decision(item: Dict[str, object], *, min_confidence: float = 0.8) -> Dict[str, object]:
    decision = item.get("aiDecision", {}) if isinstance(item.get("aiDecision"), dict) else {}
    action = str(decision.get("action") or "").lower()
    approved_text = str(decision.get("approvedText") or "").strip()
    confidence = float(item.get("aiConfidence") or decision.get("confidence") or 0.0)
    validator_status = str(item.get("validatorStatus") or "unknown")
    if action != "approve":
        item["aiStatus"] = "escalated"
        return item
    if confidence < float(min_confidence):
        item["aiStatus"] = "escalated"
        item["validatorStatus"] = "low_confidence"
        return item
    ok, status = validate_alt_text(approved_text)
    item["validatorStatus"] = status
    if not ok:
        item["aiStatus"] = "escalated"
        return item
    if validator_status not in {"pass", "not_applicable", "unknown"} and validator_status != status:
        item["aiStatus"] = "escalated"
        return item
    item["approvedText"] = approved_text
    item["status"] = "approved"
    item["aiStatus"] = "applied"
    item["aiUpdatedAt"] = _now_iso()
    return item
