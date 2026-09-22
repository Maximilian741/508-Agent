"""Health check + diagnostic routes.

``/healthz`` is the cheap liveness ping used by the dev launcher and the
frontend's Live/Offline indicator.

``/diagnostics`` is a deeper system probe — lists registered analyzers and
executors, exercises the configured AI provider with a single trivial prompt,
and reports per-step latency. Used by the Settings → Diagnostics screen so
operators can verify the install is wired up correctly.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter()
_log = logging.getLogger(__name__)


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/healthz")
async def healthz() -> dict:
    """Liveness probe. Also advertises the operator-set upload limits (with
    an account, and the smaller one for a signed-out scan) so the UI can state
    them up front instead of letting a user discover one via a 413 after
    waiting through a whole upload."""
    from app.api.pipeline import _anonymous_upload_cap_bytes
    from app.config import get_settings

    settings = get_settings()
    return {
        "ok": True,
        "maxUploadMb": int(settings.max_upload_mb),
        "anonymousMaxUploadMb": max(1, _anonymous_upload_cap_bytes(settings) // (1024 * 1024)),
    }


@router.get("/readyz")
async def readyz() -> dict:
    """Readiness probe: confirms the database is reachable (503 if not).

    Use this for orchestration readiness gates; /healthz is liveness only.
    """
    from fastapi import HTTPException
    from sqlalchemy import text

    from app.db.session_sqlalchemy import ENGINE

    try:
        with ENGINE.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("readiness check failed: %s", exc)
        raise HTTPException(status_code=503, detail="not_ready")
    return {"ready": True}


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

    # 4) Real pipeline self-test — build a tiny DOCX in memory with a KNOWN
    #    defect, run the actual parse -> analyze -> detect path, and confirm
    #    the engine flags it. This proves the whole pipeline genuinely works
    #    on THIS running server, not just that modules import. Deterministic
    #    (no AI), so it's cheap to call.
    t0 = time.perf_counter()
    try:
        import os as _os
        import tempfile as _tf

        from docx import Document as _Docx

        from app.analyzers.registry import run_analyzers as _run
        from app.parsers import parse_to_tree as _parse
        from app.services.remediation_engine import RemediationEngine as _Engine

        _tmp = _tf.mkdtemp(prefix="508_selftest_")
        _path = _os.path.join(_tmp, "selftest.docx")
        _d = _Docx()  # deliberately NO title
        _d.add_paragraph("A tiny self-test document with a deliberate defect.")
        _d.save(_path)
        _res = _parse(_path)
        _run(_res.tree)
        _viol = _Engine().detect_violations(_res.tree)
        _codes = {v.rule_id for v in _viol}
        _ok = "DOCUMENT_TITLE_MISSING" in _codes
        try:
            _os.remove(_path)
            _os.rmdir(_tmp)
        except Exception:
            pass
        payload["checks"].append(
            {
                "name": "pipeline",
                "ok": _ok,
                "ms": _ms_since(t0),
                "detail": (
                    "End-to-end self-test passed (parsed a document and detected its issue)"
                    if _ok
                    else "Self-test ran but did not detect the expected issue"
                ),
            }
        )
        if not _ok:
            payload["status"] = "degraded"
    except Exception as exc:
        payload["status"] = "degraded"
        payload["checks"].append(
            {"name": "pipeline", "ok": False, "ms": _ms_since(t0), "detail": str(exc)}
        )

    # 5) Deployment configuration flags (booleans only — no secrets). Lets the
    #    in-app System Check tell the owner what's wired up.
    try:
        from app.config import get_settings as _gs
        from app.services.ocr import get_ocr_provider as _gop

        _s = _gs()
        _stripe_cfg = bool(_os.environ.get("STRIPE_SECRET_KEY"))
        _smtp_cfg = bool(_os.environ.get("SMTP_HOST"))
        payload["deployment"] = {
            "environment": _s.environment,
            "appVersion": getattr(_s, "app_version", ""),
            "ocrEnabled": bool(getattr(_s, "ocr_enabled", False)),
            "ocrAvailable": _gop() is not None,
            "stripeConfigured": _stripe_cfg,
            "emailConfigured": _smtp_cfg,
        }
    except Exception as exc:  # never fail diagnostics on the config read
        payload["deployment"] = {"error": str(exc)}

    # 6) AI provider probe — exercise alt_text with a trivial payload.
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
