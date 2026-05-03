"""Health check + diagnostic routes.

``/healthz`` is the cheap liveness ping used by the dev launcher and the
frontend's Live/Offline indicator.

``/diagnostics`` is a deeper system probe — lists registered analyzers and
executors, exercises the configured AI provider with a single trivial prompt,
and reports per-step latency. Used by the Settings → Diagnostics screen so
operators can verify the install is wired up correctly.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@router.get("/diagnostics")
async def diagnostics() -> Dict[str, Any]:
    """Run a deep system probe and return per-check status + timing.

    The shape of the response is stable: the frontend renders each entry in
    ``checks`` as a row, and ``ai`` as a dedicated panel.
    """

    payload: Dict[str, Any] = {
        "status": "ok",
        "checks": [],
        "ai": {},
        "totals": {},
    }

    overall_start = time.perf_counter()

    # 1) Analyzers registered.
    t0 = time.perf_counter()
    try:
        from app.analyzers.registry import get_default_analyzers

        analyzers = [a.name for a in get_default_analyzers()]
        payload["checks"].append(
            {
                "name": "analyzers",
                "ok": True,
                "ms": _ms_since(t0),
                "detail": f"{len(analyzers)} analyzer(s) registered",
                "items": analyzers,
            }
        )
    except Exception as exc:
        payload["status"] = "degraded"
        payload["checks"].append(
            {"name": "analyzers", "ok": False, "ms": _ms_since(t0), "detail": str(exc)}
        )

    # 2) Executors registered.
    t0 = time.perf_counter()
    try:
        from app.services.remediators.registry import get_default_executors

        executors = [e.__class__.__name__ for e in get_default_executors()]
        payload["checks"].append(
            {
                "name": "executors",
                "ok": True,
                "ms": _ms_since(t0),
                "detail": f"{len(executors)} executor(s) registered",
                "items": executors,
            }
        )
    except Exception as exc:
        payload["status"] = "degraded"
        payload["checks"].append(
            {"name": "executors", "ok": False, "ms": _ms_since(t0), "detail": str(exc)}
        )

    # 3) Parsers registered.
    t0 = time.perf_counter()
    try:
        from app.parsers import DOCXParser, PDFParser, PPTXParser  # noqa: F401

        payload["checks"].append(
            {
                "name": "parsers",
                "ok": True,
                "ms": _ms_since(t0),
                "detail": "PDF / DOCX / PPTX parsers importable",
                "items": ["pdf", "docx", "pptx"],
            }
        )
    except Exception as exc:
        payload["status"] = "degraded"
        payload["checks"].append(
            {"name": "parsers", "ok": False, "ms": _ms_since(t0), "detail": str(exc)}
        )

    # 4) AI provider probe — exercise alt_text with a trivial payload.
    t0 = time.perf_counter()
    try:
        from app.ai.semantic_inference import build_default_provider

        provider = build_default_provider()
        result = provider.alt_text(
            {
                "label": "Diagnostic image",
                "location": "diagnostic page 1",
                "context": "A simple synthetic image used by /diagnostics.",
            }
        )
        payload["ai"] = {
            "provider": provider.name,
            "ok": True,
            "ms": _ms_since(t0),
            "sampleOutput": (result.text or "")[:240],
            "confidence": round(float(result.confidence), 3),
        }
    except Exception as exc:
        payload["status"] = "degraded"
        payload["ai"] = {
            "provider": "unknown",
            "ok": False,
            "ms": _ms_since(t0),
            "error": str(exc),
        }

    payload["totals"]["ms"] = _ms_since(overall_start)
    return payload


def _ms_since(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)
