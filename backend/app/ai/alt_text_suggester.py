from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path
from typing import Dict, List


def _openai_alt_text(image_b64: str, mime_type: str = "image/png") -> str | None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    model = os.getenv("OPENAI_ALT_MODEL", "gpt-4.1-mini")
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "Write concise, objective alt text in one sentence. No speculation.",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Generate accessibility alt text for this image."},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
                    },
                ],
            },
        ],
        "temperature": 0.2,
        "max_tokens": 80,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            text = (
                payload.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            text = str(text).strip()
            return text or None
    except Exception:
        return None


def _fallback_alt_text(context: Dict[str, object]) -> str:
    label = str(context.get("label") or "Image")
    location = str(context.get("location") or "document")
    return f"{label} shown in {location}."


def _safe_image_bytes(path: Path) -> bytes | None:
    try:
        content = path.read_bytes()
    except Exception:
        return None
    if len(content) > 2_000_000:
        return None
    return content


def build_alt_text_suggestions(
    doc_id: str,
    doc_type: str,
    src_path: Path,
    issues: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    suggestions: List[Dict[str, object]] = []
    for issue in issues:
        if str(issue.get("ruleId", "")).lower() != "missing_alt_text":
            continue
        evidence = issue.get("evidence", {}) if isinstance(issue.get("evidence"), dict) else {}
        anchors = evidence.get("anchors", []) if isinstance(evidence.get("anchors"), list) else []
        if not anchors:
            anchors = [{"page": p, "kind": "image"} for p in (evidence.get("pages", []) if isinstance(evidence.get("pages"), list) else [])]
        if not anchors:
            anchors = [{"kind": "image"}]
        anchors = anchors[:20]
        src_bytes = _safe_image_bytes(src_path)
        image_b64 = base64.b64encode(src_bytes).decode("utf-8") if src_bytes else None
        for idx, anchor in enumerate(anchors, start=1):
            if isinstance(anchor, dict):
                location = (
                    f"page {anchor.get('page')}"
                    if anchor.get("page") is not None
                    else f"slide {anchor.get('slide')}"
                    if anchor.get("slide") is not None
                    else "document"
                )
            else:
                location = "document"
            text = None
            if image_b64 and doc_type in {"pdf", "docx", "pptx"}:
                text = _openai_alt_text(image_b64)
            if not text:
                text = _fallback_alt_text({"label": f"Image {idx}", "location": location})
            suggestions.append(
                {
                    "id": f"ai-alt-{doc_id}-{idx}",
                    "issueId": str(issue.get("id", "missing_alt_text")),
                    "targetNodeId": "doc-1",
                    "reason": "AI alt text suggestion",
                    "notes": "Review and approve suggested alt text.",
                    "instructions": "Approve, edit, or reject this suggestion before final remediation.",
                    "suggestedFix": "Set meaningful alt text.",
                    "confidence": 0.55 if image_b64 else 0.35,
                    "requiresHuman": True,
                    "anchor": anchor if isinstance(anchor, dict) else None,
                    "suggestedText": text,
                    "aiSuggested": True,
                    "status": "pending",
                }
            )
    return suggestions

