"""Unified end-to-end remediation pipeline routes.

Two endpoints live here:

* ``POST /pipeline/analyze`` is the read-only audit preview.  It uploads a
  document, parses it, runs the full analyzer + planner suite, and returns a
  JSON report.  By default it does **not** mutate the tree — that change is
  intentional and addresses the "decisions don't matter" UX bug from the
  audit feedback.  Pass ``?execute=true`` for the legacy behavior.

* ``POST /pipeline/remediate`` is the act-on-decisions endpoint.  Caller
  uploads the original document plus a JSON list of *approved* violation
  ids.  We parse, analyze, plan, then execute **only** the plans whose
  violation id is in the approved set.  The mutated tree is then written
  back to disk via the format-specific writer and streamed to the caller.

The remediated artifact is persisted to a per-job folder under
``settings.materialized_root`` so the caller can re-fetch it via
``GET /pipeline/files/{job_id}/{filename}`` if they don't want to read the
streamed response immediately.
"""

from __future__ import annotations

import json
import logging
import secrets
import shutil
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import require_user_id, require_user_id_or_api_key
from app.config import get_settings
from app.security.signing import sign_file_url, verify_file_signature
from app.security.uploads import stream_to_tempfile
from app.security.url_fetch import (
    SsrfError,
    UrlFetchError,
    discover_site_urls,
    fetch_url_html,
)
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    ImageNode,
    Severity,
    TableNode,
    iter_reading_order,
)
from app.parsers import parse_to_tree
from app.persistence import audit_log as _audit
from app.persistence.db import get_repo
from app.services.fix_guidance import guidance_for
from app.services.remediation_engine import RemediationEngine
from app.services.scan_fixes import derive_scan_fixes
from app.services.scan_history import build_change_report, save_scan
from app.services.remediation_planner import plan_remediations, RemediationPolicy
from app.services.remediators.base import ExecutionStatus
from app.services.remediators.registry import execute_plans
from app.writers import write_remediated
from app.api.credits import (
    DOC_FORMAT_COSTS,
    InsufficientCreditsError,
    spend_credits_for_user,
    spend_credits_once_for_user,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/pipeline")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PipelineSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documentId: str
    sourceFormat: str
    title: Optional[str] = None
    language: Optional[str] = None
    pageCount: int = 0
    # How many of those pages the parser actually READ. Equal to pageCount
    # except when the per-upload page cap truncated the analysis, in which
    # case ANALYSIS_TRUNCATED is also raised as a finding. Surfaced separately
    # so the UI can say "analyzed 400 of 512 pages" rather than imply a score
    # covers the whole file.
    pagesAnalyzed: Optional[int] = None
    nodeCount: int = 0
    imageCount: int = 0
    tableCount: int = 0


class PipelineFix(BaseModel):
    """How to fix ONE finding, for surfaces we can't remediate ourselves.

    ``source="writer"`` means our remediation engine actually produced this
    change on a throwaway copy (a real diff). ``source="guidance"`` means it is
    a hand-written pattern — an example, not something we verified — and carries
    no ``before``. See app/services/fix_guidance.py + scan_fixes.py.
    """

    model_config = ConfigDict(extra="forbid")

    source: str                       # "writer" | "guidance"
    kind: str                         # "element" | "structural" | "css" | "advice"
    before: Optional[str] = None
    after: Optional[str] = None
    action: Optional[str] = None
    note: Optional[str] = None
    requiresHumanVerification: bool = True


class PipelineViolation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    ruleId: str
    severity: str
    description: str
    nodeId: str
    page: Optional[int] = None
    standards: Dict[str, List[str]] = Field(default_factory=dict)
    evidence: Dict[str, Any] = Field(default_factory=dict)
    recommendedActions: List[str] = Field(default_factory=list)
    # Only populated by the URL/site scan (a live page we can't remediate).
    # Defaults to None so /analyze and every existing caller is unaffected.
    fix: Optional[PipelineFix] = None


class PipelineExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actionCode: str
    targetNodeId: str
    status: str
    notes: str


class PipelineScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initialIssues: int
    fixedAutomatically: int
    pendingManual: int
    score: float = Field(ge=0.0, le=100.0)
    grade: str


class ScanChangeReport(BaseModel):
    """What changed since this URL was last scanned by this user.

    Matching is by content-derived FINGERPRINT (see services/scan_history.py) —
    a best-effort match, not per-issue lineage, because a page that rewrites its
    copy reads as "old fixed, new appeared".
    """

    model_config = ConfigDict(extra="forbid")

    previousScanAt: Optional[str] = None
    previousIssueCount: int = 0
    previousScore: float = 0.0
    previousGrade: str = ""
    newIssues: int = 0
    resolvedIssues: int = 0
    unchangedIssues: int = 0


class PipelineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: PipelineSummary
    violations: List[PipelineViolation]
    executions: List[PipelineExecutionResult]
    score: PipelineScore
    aiProvider: str
    # Only set by the URL scan, and only on a re-scan of the same URL.
    changes: Optional[ScanChangeReport] = None


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post("/analyze", response_model=PipelineResponse)
async def analyze(
    request: Request,
    file: UploadFile = File(...),
    execute: bool = False,
    # Free, read-only scanning — accepts a session JWT (UI) OR a developer API
    # key. Remediation (which spends credits) stays JWT-only on purpose, so an
    # API key can never trigger billing.
    user_id: str = Depends(require_user_id_or_api_key),
) -> PipelineResponse:
    """Analyze a document and return findings.

    ``execute=False`` (the default) means the tree is **not** mutated — the
    response describes what *could* be auto-applied if the caller approved
    each finding.  Pass ``execute=true`` to also run the executors and bake
    every deterministic fix into the in-memory tree (legacy behavior).
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".docx", ".pptx", ".html", ".htm"}:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix or '(none)'}")

    settings = get_settings()
    upload_result = await stream_to_tempfile(
        file,
        max_bytes=settings.max_upload_bytes,
        expected_suffix=suffix,
    )
    tmp_path = upload_result.path

    # Parsing + analysis are CPU-bound (and remediation can make blocking AI
    # calls). Run them in the threadpool so two concurrent large documents
    # don't freeze the event loop — including /healthz — for everyone else.
    try:
        result = await run_in_threadpool(parse_to_tree, str(tmp_path))
    except Exception as exc:
        logger.exception("pipeline parse failed: %s", exc)
        raise HTTPException(status_code=422, detail="Failed to parse document. Ensure it is a valid, uncorrupted PDF, DOCX, PPTX, or HTML file.")
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

    tree = result.tree
    # /analyze is FREE (no credits, and it accepts developer API keys), yet
    # ?execute=true runs the real executors. Pin them to the offline heuristic
    # provider, exactly like the URL scan: requires_ai=False is not a gate
    # (IMPROVE_LINK_TEXT still calls the inference client), so an unpinned
    # execute=true billed the paid provider per link and per language guess.
    from app.services.remediators.registry import RemediationDispatcher, get_offline_executors

    engine = RemediationEngine(dispatcher=RemediationDispatcher(get_offline_executors()))
    violations = await run_in_threadpool(engine.detect_violations, tree)
    actions = engine.plan_actions(violations)
    executions = (await run_in_threadpool(engine.execute, tree)) if execute else []

    summary = PipelineSummary(
        documentId=result.document_id,
        sourceFormat=result.format,
        title=tree.root.metadata.properties.get("title"),
        language=tree.root.metadata.language,
        pageCount=int(result.raw_metadata.get("page_count") or result.raw_metadata.get("slide_count") or 0),
        pagesAnalyzed=_pages_analyzed(tree),
        nodeCount=_count_nodes(tree),
        imageCount=_count_nodes_of(tree, ImageNode),
        tableCount=_count_nodes_of(tree, TableNode),
    )

    from app.models.accessibility import FLAG_DEFINITIONS, REMEDIATION_ACTIONS_BY_FLAG

    api_violations: List[PipelineViolation] = []
    for v in violations:
        flag_code = AccessibilityFlagCode(v.rule_id)
        definition = FLAG_DEFINITIONS[flag_code]

        api_violations.append(
            PipelineViolation(
                id=v.violation_id,
                ruleId=v.rule_id,
                severity=v.severity,
                description=v.description,
                nodeId=v.location.node_id,
                page=v.evidence.get("page"),
                standards={
                    "wcag_2_1": list(definition.standards.wcag_2_1),
                    "section_508": list(definition.standards.section_508),
                    "pdf_ua": list(definition.standards.pdf_ua),
                },
                evidence=v.evidence,
                recommendedActions=[a.action_code.value for a in REMEDIATION_ACTIONS_BY_FLAG.get(flag_code, [])],
            )
        )

    api_executions = []
    for e in executions:
        notes = e.notes
        # Be honest: a success the writer can't persist for this format is an
        # in-memory-only change the downloaded file won't reflect.
        if e.status.value == "success" and not _action_persists(e.action_code.value, result.format):
            suffix = (
                f" [Not auto-applied to the {result.format.upper()} file — "
                "this fix requires manual remediation in the source document.]"
            )
            notes = (notes or "") + suffix
        api_executions.append(
            PipelineExecutionResult(
                actionCode=e.action_code.value,
                targetNodeId=e.target_node_id,
                status=e.status.value,
                notes=notes,
            )
        )

    score = _build_score(violations=violations, executions=executions, source_format=result.format)

    # Persist the SERVER-computed score so a certificate can be bound to a real
    # measurement (never to client-supplied numbers).
    _persist_analysis_result(user_id, summary, score, file.filename)

    # Provider name for transparency / UI badge.
    provider_name = "heuristic"
    try:
        from app.ai.semantic_inference import build_default_provider

        provider_name = build_default_provider().name
    except Exception:
        pass

    # Audit log: record the analyze.  Doc id only — never filename.
    try:
        ctx = _audit.context_from_request(request)
        _audit.record_event(
            event="analyze",
            request_id=ctx.get("request_id"),
            actor_email=ctx.get("actor_email"),
            actor_sub=ctx.get("actor_sub"),
            ip=ctx.get("ip"),
            doc_id=summary.documentId,
            details={
                "sourceFormat": summary.sourceFormat,
                "violationCount": len(api_violations),
                "executionCount": len(api_executions),
                "score": score.score,
                "grade": score.grade,
                "aiProvider": provider_name,
                "execute": bool(execute),
            },
        )
    except Exception:
        # Never let audit failure take out a real response.
        pass

    return PipelineResponse(
        summary=summary,
        violations=api_violations,
        executions=api_executions,
        score=score,
        aiProvider=provider_name,
    )


class AnalyzeUrlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)


@router.post("/analyze-url", response_model=PipelineResponse)
async def analyze_url(
    request: Request,
    payload: AnalyzeUrlRequest,
    user_id: str = Depends(require_user_id_or_api_key),
) -> PipelineResponse:
    """Free, read-only accessibility scan of a PUBLIC web page given its URL.

    The page is fetched through the SSRF-hardened :func:`fetch_url_html` (public
    addresses only, no redirects to internal hosts, size/time-capped) and run
    through the same HTML analyzers as an upload. Analyze-only by nature: a live
    page cannot be remediated here (we can't write back to someone's site), so
    nothing is executed, persisted, or charged — the response shape matches
    /analyze so the UI can render it identically.
    """
    import tempfile

    try:
        html_bytes, final_url = await run_in_threadpool(fetch_url_html, payload.url)
    except SsrfError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except UrlFetchError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    fd, tmp_name = tempfile.mkstemp(suffix=".html")
    tmp_path = Path(tmp_name)
    try:
        import os as _os

        _os.close(fd)
        tmp_path.write_bytes(html_bytes)
        try:
            result = await run_in_threadpool(parse_to_tree, str(tmp_path))
        except Exception as exc:
            logger.exception("url scan parse failed: %s", exc)
            raise HTTPException(status_code=422, detail="Could not parse that page's HTML.")

        tree = result.tree
        engine = RemediationEngine()
        violations = await run_in_threadpool(engine.detect_violations, tree)

        # SNAPSHOT the page AS FOUND before anything can mutate the tree.
        # derive_scan_fixes below runs the real executors on THIS SAME tree
        # object to produce diffs, and some of them write the very fields we
        # report (SET_DOCUMENT_TITLE writes properties["title"],
        # SET_DOCUMENT_LANGUAGE writes metadata.language). Reading them
        # afterwards would show the user the FIXED page — a title their page
        # doesn't have — and would fingerprint a page that doesn't exist.
        page_title = (tree.root.metadata.properties or {}).get("title")
        page_language = tree.root.metadata.language
        page_node_count = _count_nodes(tree)
        page_image_count = _count_nodes_of(tree, ImageNode)
        page_table_count = _count_nodes_of(tree, TableNode)
        # Fingerprints must describe the page AS SCANNED, not as remediated.
        scan_fingerprints = await run_in_threadpool(fingerprint_violations, violations, tree)

        # "Fix it yourself": run our real remediation engine against a throwaway
        # copy so each finding can carry the exact diff it produced. Analyze-only
        # and AI-free (see scan_fixes) — nothing is charged, persisted, or sent
        # back to the scanned site. Failures degrade to guidance-only.
        fixes_by_node = await run_in_threadpool(derive_scan_fixes, tmp_path, tree)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

    from app.models.accessibility import FLAG_DEFINITIONS

    summary = PipelineSummary(
        documentId=result.document_id,
        sourceFormat=result.format,
        title=page_title,
        language=page_language,
        pageCount=0,
        nodeCount=page_node_count,
        imageCount=page_image_count,
        tableCount=page_table_count,
    )
    api_violations: List[PipelineViolation] = []
    for v in violations:
        flag_code = AccessibilityFlagCode(v.rule_id)
        definition = FLAG_DEFINITIONS[flag_code]
        api_violations.append(
            PipelineViolation(
                id=v.violation_id,
                ruleId=v.rule_id,
                severity=v.severity,
                description=v.description,
                nodeId=v.location.node_id,
                page=v.evidence.get("page"),
                standards={
                    "wcag_2_1": list(definition.standards.wcag_2_1),
                    "section_508": list(definition.standards.section_508),
                    "pdf_ua": list(definition.standards.pdf_ua),
                },
                evidence=v.evidence,
                # A live page isn't remediated here, so we don't advertise auto-fix
                # actions — the UI tells the user to upload the file to fix them.
                recommendedActions=[],
                # ...but we DO hand over exactly what to change: a real diff from
                # our engine when it produced one, else static guidance.
                fix=_fix_for_violation(v, fixes_by_node),
            )
        )
    score = _build_scan_score(violations)

    # "What changed since last time" — the reason to come back and re-scan.
    # Best-effort: any failure just omits the report.
    changes = None
    try:
        # Uses the fingerprints captured BEFORE derive_scan_fixes touched the
        # tree, so the diff describes the page the user actually has.
        report = await run_in_threadpool(
            diff_against_previous, user_id, final_url, scan_fingerprints
        )
        changes = ScanChangeReport(**report) if report else None
        await run_in_threadpool(save_scan, user_id, final_url, scan_fingerprints, score)
    except Exception as exc:
        logger.warning("scan history failed (scan continues): %s", exc)

    provider_name = "heuristic"
    try:
        from app.ai.semantic_inference import build_default_provider

        provider_name = build_default_provider().name
    except Exception:
        pass

    # Audit: record the scan with the HOST ONLY — never the full URL (it can
    # carry query-string PII), matching the project's logging-privacy rule.
    try:
        from urllib.parse import urlparse as _urlparse

        ctx = _audit.context_from_request(request)
        _audit.record_event(
            event="analyze_url",
            request_id=ctx.get("request_id"),
            actor_email=ctx.get("actor_email"),
            actor_sub=ctx.get("actor_sub"),
            ip=ctx.get("ip"),
            doc_id=summary.documentId,
            details={
                "host": _urlparse(final_url).hostname or "",
                "violationCount": len(api_violations),
                "score": score.score,
                "grade": score.grade,
            },
        )
    except Exception:
        pass

    return PipelineResponse(
        summary=summary,
        violations=api_violations,
        executions=[],
        score=score,
        aiProvider=provider_name,
        changes=changes,
    )


# ---------------------------------------------------------------------------
# Whole-site scan (free, read-only) — the agency/gov version of the URL scan
# ---------------------------------------------------------------------------

# Bounded by construction: a free endpoint must never become an unbounded
# outbound crawler. Pages are also capped by a total wall-clock budget.
SITE_SCAN_MAX_PAGES = 25
SITE_SCAN_DEFAULT_PAGES = 10
SITE_SCAN_BUDGET_SECONDS = 90.0


def _fix_for_violation(v, fixes_by_node: Dict[str, Dict[str, Any]]) -> Optional[PipelineFix]:
    """Best fix for one finding: a real writer diff if we produced one, else guidance.

    A writer diff is only ever present when the writer's own ``applied`` list
    confirmed the change, so we never show a fix that didn't happen. Matching is
    by (node, ACTION) and only against actions THIS rule maps to — otherwise
    every document-level finding would display the same unrelated diff, since
    they all share ``target_id = root.id``.
    """
    from app.models.accessibility import REMEDIATION_ACTIONS_BY_FLAG

    raw = None
    try:
        flag = AccessibilityFlagCode(v.rule_id)
        for action in REMEDIATION_ACTIONS_BY_FLAG.get(flag, []):
            candidate = fixes_by_node.get(f"{v.location.node_id}|{action.action_code.value}")
            if candidate:
                raw = candidate
                break
    except Exception:
        raw = None
    if raw is None:
        raw = guidance_for(v.rule_id, v.evidence)
    if not raw:
        return None
    try:
        return PipelineFix(**raw)
    except Exception:  # a malformed guidance entry must never break a scan
        return None


class SiteScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)
    maxPages: int = Field(default=SITE_SCAN_DEFAULT_PAGES, ge=1, le=SITE_SCAN_MAX_PAGES)


class SitePageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    title: Optional[str] = None
    status: str  # "scanned" | "failed"
    note: Optional[str] = None
    issueCount: int = 0
    errorCount: int = 0
    warningCount: int = 0
    score: float = 0.0
    grade: str = ""


class SiteIssueRollup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ruleId: str
    severity: str
    totalCount: int
    pageCount: int


class SiteScanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seedUrl: str
    pagesDiscovered: int
    pagesScanned: int
    pagesFailed: int
    totalIssues: int
    score: float
    grade: str
    issues: List[SiteIssueRollup]
    pages: List[SitePageResult]


def _scan_page_sync(url: str):
    """Fetch + parse + analyze ONE page. Returns (violations, title).

    Blocking by design (called via run_in_threadpool). Raises SsrfError /
    UrlFetchError from the guarded fetcher, or ValueError on a parse failure.
    """
    import os as _os
    import tempfile

    html_bytes, _final = fetch_url_html(url)
    fd, tmp_name = tempfile.mkstemp(suffix=".html")
    tmp_path = Path(tmp_name)
    try:
        _os.close(fd)
        tmp_path.write_bytes(html_bytes)
        try:
            result = parse_to_tree(str(tmp_path))
        except Exception as exc:
            raise ValueError("Could not parse that page's HTML.") from exc
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
    tree = result.tree
    violations = RemediationEngine().detect_violations(tree)
    return violations, tree.root.metadata.properties.get("title")


@router.post("/scan-site", response_model=SiteScanResponse)
async def scan_site(
    request: Request,
    payload: SiteScanRequest,
    user_id: str = Depends(require_user_id_or_api_key),
) -> SiteScanResponse:
    """Free, read-only accessibility scan of a whole PUBLIC site.

    Discovers pages from the site's ``sitemap.xml`` (same-origin only; a missing
    sitemap simply scans the one page), scans up to ``maxPages`` through the same
    SSRF-hardened fetcher as the single-page scan, and returns a per-page
    breakdown plus a site-wide issue rollup. Analyze-only: nothing is executed,
    persisted, or charged.
    """
    try:
        urls = await run_in_threadpool(discover_site_urls, payload.url, payload.maxPages)
    except SsrfError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except UrlFetchError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not urls:
        raise HTTPException(status_code=422, detail="No pages could be discovered for that site.")

    deadline = time.monotonic() + SITE_SCAN_BUDGET_SECONDS
    pages: List[SitePageResult] = []
    rollup: Dict[str, Dict[str, Any]] = {}
    total_issues = 0
    scanned = failed = 0
    first_error: Optional[HTTPException] = None

    for url in urls:
        if time.monotonic() >= deadline:
            break  # wall-clock budget spent; report what we have
        try:
            violations, title = await run_in_threadpool(_scan_page_sync, url)
        except (SsrfError, UrlFetchError, ValueError) as exc:
            failed += 1
            if first_error is None and not pages:
                # If the very first (seed) page fails, surface it as the request
                # error instead of returning an empty "successful" report.
                first_error = HTTPException(
                    status_code=400 if isinstance(exc, SsrfError) else 422, detail=str(exc)
                )
            pages.append(SitePageResult(url=url, status="failed", note=str(exc)))
            continue
        except Exception as exc:  # never let one bad page kill the run
            logger.exception("site scan page failed: %s", exc)
            failed += 1
            pages.append(SitePageResult(url=url, status="failed", note="Could not scan this page."))
            continue

        scanned += 1
        errs = sum(1 for v in violations if v.severity == Severity.ERROR.value)
        warns = sum(1 for v in violations if v.severity == Severity.WARNING.value)
        total_issues += len(violations)
        page_score = _build_scan_score(violations)
        pages.append(
            SitePageResult(
                url=url,
                title=title,
                status="scanned",
                issueCount=len(violations),
                errorCount=errs,
                warningCount=warns,
                score=page_score.score,
                grade=page_score.grade,
            )
        )
        for v in violations:
            slot = rollup.setdefault(v.rule_id, {"severity": v.severity, "total": 0, "pages": set()})
            slot["total"] += 1
            slot["pages"].add(url)

    if scanned == 0 and first_error is not None:
        raise first_error

    # Site score = the average of the scanned pages' quality scores (honest:
    # nothing was remediated, so this is purely "how the site reads today").
    avg = sum(p.score for p in pages if p.status == "scanned") / max(scanned, 1)
    site_score = round(avg, 2) if scanned else 0.0
    issues = sorted(
        (
            SiteIssueRollup(
                ruleId=rid,
                severity=str(d["severity"]),
                totalCount=int(d["total"]),
                pageCount=len(d["pages"]),
            )
            for rid, d in rollup.items()
        ),
        key=lambda i: (0 if i.severity == "error" else 1, -i.totalCount),
    )

    try:
        from urllib.parse import urlparse as _urlparse

        ctx = _audit.context_from_request(request)
        _audit.record_event(
            event="scan_site",
            request_id=ctx.get("request_id"),
            actor_email=ctx.get("actor_email"),
            actor_sub=ctx.get("actor_sub"),
            ip=ctx.get("ip"),
            doc_id=None,
            details={
                "host": _urlparse(urls[0]).hostname or "",
                "pagesScanned": scanned,
                "pagesFailed": failed,
                "totalIssues": total_issues,
            },
        )
    except Exception:
        pass

    return SiteScanResponse(
        seedUrl=urls[0],
        pagesDiscovered=len(urls),
        pagesScanned=scanned,
        pagesFailed=failed,
        totalIssues=total_issues,
        score=site_score,
        grade=_grade(site_score) if scanned else "",
        issues=issues,
        pages=pages,
    )


def _charge_credits(user_id: str, doc_format: str, doc_id: str | None = None) -> int:
    """Charge the per-format credit cost for a remediation run.

    Returns the new balance.  Raises HTTPException(402) if the user is out
    of credits, HTTPException(401) if the user_id is missing/unknown.
    """
    if not user_id:
        raise HTTPException(status_code=401, detail="missing_account")
    fmt = (doc_format or "").lstrip(".").lower()
    cost = DOC_FORMAT_COSTS.get(fmt)
    if cost is None:
        # Unknown format - default to a small charge.
        cost = 5
    # Team members draw on (and overage-charge) the team owner's shared wallet.
    try:
        from app.api.teams import resolve_credit_user_id

        target_id = resolve_credit_user_id(user_id)
    except Exception:
        target_id = user_id
    # Subscribers with overage enabled get an automatic top-up instead of a 402.
    try:
        from app.api.stripe_billing import ensure_balance_for

        # actor_id: only the wallet owner or a team admin may trigger an
        # off-session overage charge on the owner's card — never a plain member.
        ensure_balance_for(target_id, cost, actor_id=user_id)
    except Exception:
        pass
    try:
        return spend_credits_for_user(
            user_id=target_id,
            amount=cost,
            description=f"remediate_{fmt}",
            related_doc_id=doc_id,
        )
    except InsufficientCreditsError:
        raise HTTPException(status_code=402, detail="Insufficient credits")


def _precheck_credits(user_id: str, doc_format: str) -> int:
    """Read-only affordability check BEFORE doing any work.

    Returns the per-format cost. Raises 401 (no account) or 402 (can't
    afford). Does NOT spend or trigger an overage charge — the real debit
    happens only after the remediated file is successfully produced, so a
    parse/write failure never charges the user (the cardinal billing bug:
    paying for a 422 on exactly the corrupt/exotic documents this tool
    targets). If the balance can't be read we proceed and let the real
    post-success spend be the source of truth.
    """
    if not user_id:
        raise HTTPException(status_code=401, detail="missing_account")
    fmt = (doc_format or "").lstrip(".").lower()
    cost = DOC_FORMAT_COSTS.get(fmt) or 5
    try:
        from app.api.teams import resolve_credit_user_id

        target_id = resolve_credit_user_id(user_id)
    except Exception:
        target_id = user_id
    try:
        from app.db.models import UserRow as _UserRow
        from app.db.session_sqlalchemy import session_scope as _scope
        from sqlalchemy import select as _select

        with _scope() as session:
            row = session.execute(
                _select(_UserRow).where(_UserRow.id == target_id)
            ).scalar_one_or_none()
            balance = int(row.credits_balance or 0) if row else 0
        if balance >= cost:
            return cost
        # Short on credits — only OK if overage auto-top-up is available.
        try:
            from app.api.stripe_billing import _overage_eligible

            if _overage_eligible(target_id, actor_id=user_id):
                return cost
        except Exception:
            pass
        raise HTTPException(status_code=402, detail="Insufficient credits")
    except HTTPException:
        raise
    except Exception:
        # Balance unreadable — don't block; the post-success spend will 402
        # if truly insufficient (and that path never delivered a file).
        return cost


def _cleanup_job_dir(job_dir: Path) -> None:
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except Exception:
        pass

@router.post("/remediate")
async def remediate(
    request: Request,
    file: UploadFile = File(...),
    approved_violations: str = Form(""),
    rejected_violations: str = Form(""),
    user_id: str = Depends(require_user_id),
):
    """Apply only user-approved fixes and stream back the remediated file.

    ``approved_violations`` is a JSON-encoded list of violation IDs the user
    approved on the audit screen.  Anything not in that list is left
    untouched in the tree.  ``rejected_violations`` is informational — we
    record it in the response payload so the caller can confirm we saw the
    set of items they want queued for manual review.
    """

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".docx", ".pptx", ".html", ".htm"}:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix or '(none)'}")

    try:
        approved_ids = set(json.loads(approved_violations) if approved_violations else [])
    except Exception:
        raise HTTPException(status_code=400, detail="approved_violations must be a JSON array of strings.")
    try:
        rejected_ids = set(json.loads(rejected_violations) if rejected_violations else [])
    except Exception:
        raise HTTPException(status_code=400, detail="rejected_violations must be a JSON array of strings.")

    # Affordability check ONLY (no spend yet). The real debit happens after
    # the remediated file is successfully written, so a parse/write failure
    # never charges the user. A broke user still gets a fast 402 here.
    fmt = suffix.lstrip(".")
    _precheck_credits(user_id=user_id, doc_format=fmt)

    settings = get_settings()
    upload_result = await stream_to_tempfile(
        file,
        max_bytes=settings.max_upload_bytes,
        expected_suffix=suffix,
    )

    job_id = uuid.uuid4().hex[:12]
    job_dir = settings.materialized_root / "pipeline" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(file.filename or "document").name
    source_path = job_dir / safe_name
    # Move the streamed temp file into place (avoids re-reading bytes).
    try:
        shutil.move(str(upload_result.path), str(source_path))
    except Exception:
        # Fall back to copy + unlink if cross-device.
        shutil.copyfile(str(upload_result.path), str(source_path))
        try:
            upload_result.path.unlink(missing_ok=True)
        except Exception:
            pass

    # CPU-bound parse/analyze/remediate runs in the threadpool (see analyze).
    # On ANY failure here the job dir is removed and NO credit is charged.
    try:
        result = await run_in_threadpool(parse_to_tree, str(source_path))
    except Exception as exc:
        logger.exception("remediate parse failed: %s", exc)
        _cleanup_job_dir(job_dir)
        raise HTTPException(
            status_code=422,
            detail="Failed to parse document. Ensure it is a valid, uncorrupted PDF, DOCX, PPTX, or HTML file.",
        )

    tree = result.tree
    engine = RemediationEngine()
    violations = await run_in_threadpool(engine.detect_violations, tree)
    # This endpoint APPLIES the fixes the user explicitly approved (approved_ids),
    # so the user's approval IS the human review — use an apply policy that allows
    # every recommended action to run. The default RemediationPolicy() is the
    # conservative *preview* policy (allow_ai_actions=False blocks alt-text
    # generation entirely, and require_human_review_for_all=True also filters out
    # the auto-apply fixes), which silently turned approved fixes into no-ops.
    apply_policy = RemediationPolicy(
        allow_ai_actions=True,
        require_human_review_for_all=False,
    )
    plans = plan_remediations(tree, apply_policy)

    # Filter plans down to only those whose violation id is approved.
    selected_plans = []
    for v in violations:
        if v.violation_id not in approved_ids:
            continue
        for plan in plans:
            if plan.target_node_id == v.location.node_id and plan.flag.code.value == v.rule_id:
                selected_plans.append(plan)
                break

    executions = (
        await run_in_threadpool(execute_plans, tree, selected_plans)
        if selected_plans
        else []
    )

    # Persist rejected violations into the manual_review queue so teammates
    # can pick them up via GET /manual-review.  This is best-effort: if the
    # repo blows up we log and keep going — we don't fail the remediate
    # request just because the review queue couldn't be written.
    manual_items_created = 0
    if rejected_ids:
        from app.models.accessibility import FLAG_DEFINITIONS as _FLAG_DEFS

        review_items: List[Dict[str, Any]] = []
        for v in violations:
            if v.violation_id not in rejected_ids:
                continue
            try:
                catalog_title = _FLAG_DEFS[AccessibilityFlagCode(v.rule_id)].message
            except Exception:
                catalog_title = v.rule_id
            page_value = v.evidence.get("page") if isinstance(v.evidence, dict) else None
            pages_list = [int(page_value)] if isinstance(page_value, int) else []
            review_items.append(
                {
                    # Keyed on the server-minted job id, never on
                    # result.document_id — that is the uploaded FILENAME, so an
                    # id built from it is one another tenant can reconstruct
                    # (and therefore overwrite).
                    "id": f"mr-{job_id}-{v.violation_id}",
                    "issueId": v.violation_id,
                    "targetNodeId": v.location.node_id,
                    "reason": "User rejected during /pipeline/remediate",
                    "notes": v.description,
                    "pages": pages_list,
                    "anchors": [],
                    "instructions": "Re-evaluate this finding manually.",
                    "suggestedFix": catalog_title,
                    "suggestedText": None,
                    "status": "pending",
                    "docId": result.document_id,
                }
            )
        try:
            REPO = get_repo()
            # owner_id is what decides whose queue this is. The doc id is only
            # a label (it comes from the filename), so without an owner anyone
            # could file items against anyone else's document.
            REPO.add_manual_review_items(result.document_id, review_items, owner_id=user_id)
            manual_items_created = len(review_items)
        except Exception as exc:
            logger.warning(
                "failed to persist manual review items for doc %s: %s",
                result.document_id,
                exc,
            )

    # Write the remediated artifact via the format-specific writer (CPU-bound —
    # PDF tagging re-serializes content streams — so threadpool it too).
    output_name = _suffix_filename(safe_name, "-remediated")
    output_path = job_dir / output_name
    try:
        write_result = await run_in_threadpool(
            write_remediated, source_path, tree, output_path, source_format=result.format
        )
    except Exception as exc:
        logger.exception("remediate write failed: %s", exc)
        _cleanup_job_dir(job_dir)
        raise HTTPException(
            status_code=422,
            detail="Failed to write the remediated file. The document may be encrypted or malformed.",
        )

    # Hard-failure guard: if the writer couldn't open the document it copies
    # the source through UNCHANGED (e.g. encrypted PDF). Returning that as a
    # success would charge the user for their own untouched file — treat it
    # as a 422 with no charge instead.
    _skipped = write_result.get("skipped") if isinstance(write_result, dict) else []
    _applied = write_result.get("applied") if isinstance(write_result, dict) else []
    _hard_fail = isinstance(_skipped, list) and any(
        isinstance(s, dict) and str(s.get("reason", "")).startswith(("failed_to_open", "copy_failed"))
        for s in _skipped
    )
    # The writer's content-loss gate: it built an output with LESS visible text
    # than the source and refused to ship it. Distinct message, because the
    # file is neither encrypted nor corrupt — we declined to risk it.
    _lost_content = isinstance(_skipped, list) and any(
        isinstance(s, dict) and str(s.get("reason", "")) == "output_would_lose_content"
        for s in _skipped
    )
    if _lost_content and not _applied:
        _cleanup_job_dir(job_dir)
        raise HTTPException(
            status_code=422,
            detail=(
                "We stopped before writing this file: the fixed version came out with less "
                "text than the original, and we will not ship a file that loses your content. "
                "You were not charged. Please report this document so we can look at it."
            ),
        )
    if _hard_fail and not _applied:
        _cleanup_job_dir(job_dir)
        raise HTTPException(
            status_code=422,
            detail="Could not remediate this file — it may be encrypted or corrupted. You were not charged.",
        )

    # Honesty: only charge when at least one APPROVED fix actually succeeded AND
    # persists into the file for this format. If every approved item was
    # manual-only / non-persistable (or nothing was approved), the download is
    # effectively the source — billing for that is the same overcharge the
    # hard-fail guard above prevents, via a different path. (We can't key off the
    # writer's ``applied`` list: writers re-assert existing structure — e.g. an
    # already-correct heading style — so it is non-empty even when no approved
    # fix landed.) We still return the file + summary + manual-review items; the
    # caller simply isn't billed. ``charged`` is surfaced for transparency.
    # For the writer-confirmed actions (FIX_CONTRAST, GENERATE_TABLE_CAPTION)
    # the writer's ``applied`` list is authoritative: it appends them ONLY on a
    # real edit, so an approved fix whose element didn't resolve, or whose
    # target already had the fix (writer no-op), must NOT be counted or charged.
    # See ``_count_persisted_fixes`` / ``_WRITER_CONFIRMED_ACTIONS``.
    persisted_fixes = _count_persisted_fixes(executions, _applied, fmt)

    # Reconcile the TAG_PDF_STRUCTURE note with what the tagger ACTUALLY did.
    # The executor runs before the writer and can only promise; the writer's
    # pdfua summary knows how many pages fell back to a single page-level /P
    # (unparseable stream, nested BT, op count that would not reconcile).
    # Those pages are tagged and valid but carry NO headings/lists/tables. The
    # disclosure lived only in writer.skipped, while the execution note the
    # user reads still promised the full structure. Append it to the note
    # so PipelineExecutionResult carries the truth, not just the manifest.
    try:
        _pdfua = write_result.get("pdfua") if isinstance(write_result, dict) else None
        _plo = int((_pdfua or {}).get("pagesPageLevelOnly") or 0)
        _tot = int((_pdfua or {}).get("pages") or 0)
        if _plo > 0:
            for _e in executions:
                if _e.action_code == ActionCode.TAG_PDF_STRUCTURE and _e.status == ExecutionStatus.SUCCESS:
                    _e.notes = (
                        f"{_e.notes.rstrip('.')}. NOTE: {_plo} of {_tot} page(s) could not be broken "
                        "into elements and were tagged as a single page-level block instead — their "
                        "headings, lists and tables were NOT identified. The document is tagged and "
                        "valid, but those pages need a source-application pass for full structure."
                    )
    except Exception:
        pass

    # TAG_PDF_STRUCTURE is writer-confirmed: the executor only records the
    # request, and the writer can still build no tree (no taggable page, a
    # tagger failure). Then the approved fix did not land — the note must not
    # promise a structure tree, and _count_persisted_fixes has already left it
    # uncounted and uncharged.
    if fmt == "pdf":
        _tag_confirmed = {
            a.get("target_id")
            for a in (_applied if isinstance(_applied, list) else [])
            if isinstance(a, dict) and a.get("action") == ActionCode.TAG_PDF_STRUCTURE.value
        }
        for _e in executions:
            if (
                _e.action_code == ActionCode.TAG_PDF_STRUCTURE
                and _e.status == ExecutionStatus.SUCCESS
                and _e.target_node_id not in _tag_confirmed
            ):
                _e.status = ExecutionStatus.SKIPPED
                _e.notes = (
                    "The structure tree could not be written into this file, so it is still "
                    "untagged. This fix was not applied and you were not charged for it."
                )

    # Not charged must mean not changed. With no persisted fix the run is free,
    # so it may deliver nothing: writers re-serialize and re-assert metadata
    # even when nothing was approved, and the PDF writer used to tag every file
    # — approved_violations=[] returned the 5-credit tagged PDF, uncharged and
    # byte-identical to the paid run. Hand back the uploaded bytes instead.
    if persisted_fixes == 0:
        try:
            shutil.copyfile(str(source_path), str(output_path))
        except Exception as exc:
            logger.exception("remediate: could not restore the source bytes: %s", exc)
            _cleanup_job_dir(job_dir)
            raise HTTPException(
                status_code=422,
                detail="Failed to write the remediated file. You were not charged.",
            )
        write_result = {
            "applied": [],
            "skipped": [s for s in (_skipped if isinstance(_skipped, list) else []) if isinstance(s, dict)]
            + [{"target_id": "document", "reason": "no_approved_fix_persisted: the original file is returned unchanged"}],
        }

    charged = False
    client_gone = False
    if persisted_fixes > 0:
        # If the client already left — closed the tab, or the CDN cut the
        # request at its timeout on a long document — the response carrying
        # downloadUrl will never arrive. Charging then would take a credit for
        # a file the user cannot reach. So: do NOT charge, but KEEP the
        # artifact and its manifest; GET /pipeline/jobs lists it with a fresh
        # signed URL, and the charge is taken on first download instead. The
        # user pays exactly once, and only for a file they can actually get.
        try:
            client_gone = await request.is_disconnected()
        except Exception:
            client_gone = False
        if client_gone:
            logger.warning(
                "remediate: client disconnected before charge (job %s, user %s) — "
                "artifact kept, credit deferred to first download",
                job_id, user_id,
            )
        else:
            # Charge AFTER the file exists. On the rare race where the wallet
            # was drained since the precheck, this 402s and we clean up
            # without delivering a file.
            try:
                _charge_credits(user_id=user_id, doc_format=fmt, doc_id=result.document_id)
            except HTTPException:
                _cleanup_job_dir(job_dir)
                raise
            charged = True

    # Owner email comes from the CF Access middleware (when enabled).  We
    # persist it on the job manifest so /pipeline/files can compare against
    # the requesting user later.  In dev mode it'll just be ``None``.
    owner_email = None
    try:
        user = getattr(request.state, "user", None)
        if isinstance(user, dict):
            owner_email = user.get("email")
    except Exception:
        owner_email = None

    signed_url = sign_file_url(job_id, output_name, ttl=settings.pipeline_artifact_ttl_seconds)

    response_meta = {
        "jobId": job_id,
        "filename": output_name,
        "downloadUrl": signed_url,
        "ownerEmail": owner_email,
        # The authenticated user, so /pipeline/jobs can list this job back to
        # them even when the response below is lost. ownerEmail alone was not
        # enough: it is only set behind Cloudflare Access.
        "userId": user_id,
        "sourceFormat": fmt,
        "documentId": result.document_id,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        # Set when the client disconnected before we could charge; the debit
        # is taken on first download so the user is never billed for a file
        # they could not reach.
        "chargePending": bool(client_gone and persisted_fixes > 0),
        "persistedFixes": int(persisted_fixes),
        "approved": sorted(approved_ids),
        "rejected": sorted(rejected_ids),
        "executions": [
            {
                "actionCode": e.action_code.value,
                "targetNodeId": e.target_node_id,
                "status": e.status.value,
                "notes": e.notes,
            }
            for e in executions
        ],
        "writer": write_result,
        "manualReviewItemsCreated": manual_items_created,
        "charged": charged,
    }

    # Drop a side-by-side metadata file so subsequent /pipeline/files calls
    # can echo the run summary AND enforce per-user authz on download.
    try:
        (job_dir / "meta.json").write_text(json.dumps(response_meta, indent=2), encoding="utf-8")
    except Exception:
        pass

    # Audit log: record the remediate.  Bucket the writer counts so the
    # admin screen has something useful without leaking content.
    try:
        applied_writer = write_result.get("applied") if isinstance(write_result, dict) else []
        skipped_writer = write_result.get("skipped") if isinstance(write_result, dict) else []
        applied_count = len(applied_writer) if isinstance(applied_writer, list) else 0
        skipped_count = len(skipped_writer) if isinstance(skipped_writer, list) else 0
        ctx = _audit.context_from_request(request)
        _audit.record_event(
            event="remediate",
            request_id=ctx.get("request_id"),
            actor_email=ctx.get("actor_email"),
            actor_sub=ctx.get("actor_sub"),
            ip=ctx.get("ip"),
            doc_id=result.document_id,
            job_id=job_id,
            details={
                "approvedCount": len(approved_ids),
                "rejectedCount": len(rejected_ids),
                "appliedCount": applied_count,
                "skippedCount": skipped_count,
                "manualReviewItemsCreated": manual_items_created,
                "sourceFormat": result.format,
            },
        )
    except Exception:
        pass

    return response_meta


def _collect_deferred_charge(job_id: str, meta_path: Path, meta: Dict[str, Any]) -> None:
    """Take a chargePending job's deferred credit exactly once, or raise.

    /remediate defers the debit when the client left before the response, so
    nobody pays for a file they never received; the credit is then owed on
    first delivery, by GET /pipeline/files OR POST /pipeline/batch-zip.

    Exactly once across requests and worker processes: the debit goes through
    ``spend_credits_once_for_user`` keyed on the job id, which records the key
    in the ledger in the same transaction as the debit. The manifest flag is
    only a fast path. It used to be the guard, and 10 concurrent downloads of
    one job all read chargePending=true before any wrote it back: 10 debits for
    one file. Failure order:
      * the debit fails (402): nothing is recorded, the flag stays set and the
        file is withheld — top up and download later; never free, never stuck;
      * the debit commits but the manifest write fails, or the worker dies:
        the ledger row already carries the key, so the next delivery finds it
        and does not charge again.
    """
    if not (meta or {}).get("chargePending"):
        return
    owner = str(meta.get("userId") or "")
    fmt = str(meta.get("sourceFormat") or "").lstrip(".").lower()
    if not owner or not fmt:
        # A pending charge nobody can be billed for: withhold, don't give away.
        raise HTTPException(status_code=404, detail="file_not_found")
    cost = DOC_FORMAT_COSTS.get(fmt) or 5
    # Team members draw on the team owner's shared wallet, as _charge_credits does.
    try:
        from app.api.teams import resolve_credit_user_id

        wallet_id = resolve_credit_user_id(owner)
    except Exception:
        wallet_id = owner
    key = f"pipeline-job:{job_id}"
    try:
        try:
            spend_credits_once_for_user(wallet_id, cost, f"remediate_{fmt}", idempotency_key=key)
        except InsufficientCreditsError:
            # Short: an overage subscriber gets the usual auto top-up, then one
            # retry. Tried AFTER the idempotent spend, so a job that is already
            # paid can never trigger a top-up purchase.
            try:
                from app.api.stripe_billing import ensure_balance_for

                # The payer of a deferred charge is the job's owner (the signed
                # download has no session), so the owner is the actor: a plain
                # team member's job can't trigger overage on the team owner's card.
                ensure_balance_for(wallet_id, cost, actor_id=owner)
            except Exception:
                pass
            spend_credits_once_for_user(wallet_id, cost, f"remediate_{fmt}", idempotency_key=key)
    except InsufficientCreditsError:
        raise HTTPException(status_code=402, detail="Insufficient credits")

    meta["chargePending"] = False
    meta["charged"] = True
    meta.setdefault("chargedAtDownload", datetime.now(timezone.utc).isoformat())
    tmp = meta_path.with_name(f"meta.json.{uuid.uuid4().hex}.tmp")
    try:
        # Atomic replace: a concurrent reader never sees a torn manifest.
        tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        tmp.replace(meta_path)
    except Exception:
        logger.exception("could not persist deferred-charge flag for job %s", job_id)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


class BatchZipJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jobId: str
    filename: str


class BatchZipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jobs: List[BatchZipJob] = Field(default_factory=list)


@router.post("/batch-zip")
async def batch_zip(
    request: Request,
    payload: BatchZipRequest,
    user_id: str = Depends(require_user_id),
):
    """Bundle several remediated files into a single ZIP download.

    Each job must be one the CALLER produced via /pipeline/remediate: its
    manifest's userId must equal the caller, the same scoping GET
    /pipeline/jobs (where these ids come from) applies. Another tenant's job,
    a job with no readable manifest, or a name that isn't the job's recorded
    artifact is treated as not found and skipped. This route used to check
    only ``ownerEmail`` — empty unless Cloudflare Access is on — so any
    signed-in account could zip another tenant's file, or its meta.json, with
    no signature.

    A chargePending job's deferred credit is taken here exactly as GET
    /pipeline/files takes it (``_collect_deferred_charge``); a job the wallet
    can't cover is left out. A ZIP with at least one file streams; otherwise
    402 when payment was the only obstacle, else 404.
    """
    import io
    import zipfile

    from fastapi.responses import StreamingResponse

    settings = get_settings()
    if not payload.jobs:
        raise HTTPException(status_code=400, detail="no_jobs")
    if len(payload.jobs) > 200:
        raise HTTPException(status_code=400, detail="too_many_jobs")

    requester_email = None
    user = getattr(request.state, "user", None)
    if isinstance(user, dict):
        requester_email = user.get("email")

    buf = io.BytesIO()
    added = 0
    unpaid = 0
    used_names: set = set()
    seen_jobs: set = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for job in payload.jobs:
            safe_id = "".join(c for c in job.jobId if c.isalnum() or c in "-_")[:64]
            safe_name = Path(job.filename).name
            if not safe_id or not safe_name:
                continue
            if (safe_id, safe_name) in seen_jobs:
                continue  # exact-duplicate job passed twice — bundle once
            seen_jobs.add((safe_id, safe_name))
            job_dir = settings.materialized_root / "pipeline" / safe_id
            meta_path = job_dir / "meta.json"
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue  # no manifest, no owner on record: fail closed
            if not isinstance(meta, dict) or str(meta.get("userId") or "") != user_id:
                continue
            owner_email = meta.get("ownerEmail")
            if owner_email and (
                not requester_email or requester_email.lower() != str(owner_email).lower()
            ):
                continue
            # Only the job's recorded artifact — never meta.json or the upload.
            if safe_name != str(meta.get("filename") or ""):
                continue
            try:
                data = (job_dir / safe_name).read_bytes()
            except Exception:
                continue
            # Bytes in hand first, then the deferred debit (if one is owed).
            try:
                _collect_deferred_charge(safe_id, meta_path, meta)
            except HTTPException as exc:
                if exc.status_code == 402:
                    unpaid += 1
                continue
            # De-duplicate names inside the archive (two "report-remediated.pdf").
            arc = safe_name
            n = 1
            while arc in used_names:
                arc = f"{Path(safe_name).stem} ({n}){Path(safe_name).suffix}"
                n += 1
            used_names.add(arc)
            zf.writestr(arc, data)
            added += 1

    if added == 0:
        if unpaid:
            raise HTTPException(status_code=402, detail="Insufficient credits")
        raise HTTPException(status_code=404, detail="no_files_available")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="508-remediated-batch.zip"'},
    )


class PipelineJobSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobId: str
    filename: str
    sourceFormat: str
    createdAt: Optional[str] = None
    downloadUrl: str
    charged: bool
    chargePending: bool
    persistedFixes: int = 0


class PipelineJobsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobs: List[PipelineJobSummary]


@router.get("/jobs", response_model=PipelineJobsResponse)
async def list_recent_jobs(
    request: Request,
    limit: int = 20,
    user_id: str = Depends(require_user_id),
) -> PipelineJobsResponse:
    """The caller's recent remediations, newest first, with fresh signed URLs.

    This exists because /remediate is synchronous and its response is the ONLY
    place the download URL used to live. Close the tab, or have the CDN cut a
    long request at its timeout, and the file was written, sitting on disk,
    and unreachable. Now it is one call away — and if the charge was deferred
    because the client had already left, that is visible here too.

    Owner-scoped by the userId recorded on each job manifest; jobs written
    before that field existed are not listed (they cannot be attributed).
    """
    settings = get_settings()
    root = settings.materialized_root / "pipeline"
    limit = max(1, min(int(limit or 20), 100))
    out: List[PipelineJobSummary] = []
    if not root.exists():
        return PipelineJobsResponse(jobs=out)
    candidates: List[Tuple[float, Path, dict]] = []
    for job_dir in root.iterdir():
        meta_path = job_dir / "meta.json"
        if not job_dir.is_dir() or not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str((meta or {}).get("userId") or "") != user_id:
            continue
        filename = str((meta or {}).get("filename") or "")
        if not filename or not (job_dir / filename).exists():
            continue  # artifact expired or was cleaned up
        try:
            mtime = meta_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        candidates.append((mtime, job_dir, meta))
    candidates.sort(key=lambda t: t[0], reverse=True)
    for _mtime, job_dir, meta in candidates[:limit]:
        job_id = job_dir.name
        filename = str(meta.get("filename"))
        out.append(
            PipelineJobSummary(
                jobId=job_id,
                filename=filename,
                sourceFormat=str(meta.get("sourceFormat") or Path(filename).suffix.lstrip(".") or ""),
                createdAt=meta.get("createdAt"),
                downloadUrl=sign_file_url(job_id, filename, ttl=settings.pipeline_artifact_ttl_seconds),
                charged=bool(meta.get("charged")),
                chargePending=bool(meta.get("chargePending")),
                persistedFixes=int(meta.get("persistedFixes") or 0),
            )
        )
    return PipelineJobsResponse(jobs=out)


@router.get("/files/{job_id}/{filename}")
async def download_remediated_file(
    job_id: str,
    filename: str,
    request: Request,
    exp: int | None = None,
    sig: str | None = None,
):
    """Download an artifact previously produced by /pipeline/remediate.

    Requires both the HMAC signature and (when CF Access is enabled) that
    ``request.state.user.email`` matches the owner email recorded on the
    job's ``meta.json``.
    """

    settings = get_settings()
    safe_id = "".join(c for c in job_id if c.isalnum() or c in "-_")[:64]
    safe_name = Path(filename).name
    if not safe_id or not safe_name:
        raise HTTPException(status_code=400, detail="invalid_job_or_filename")

    # Signature check.
    ok, reason = verify_file_signature(safe_id, safe_name, exp or 0, sig or "")
    if not ok:
        # 410 for expired URLs feels truer than 403 — same behavior as our
        # share-link sweep above.
        status = 410 if reason == "url_expired" else 403
        raise HTTPException(status_code=status, detail=reason)

    job_dir = settings.materialized_root / "pipeline" / safe_id
    target = job_dir / safe_name
    if not target.exists():
        raise HTTPException(status_code=404, detail="file_not_found")

    # Per-resource authz: when an owner email was recorded, ensure the
    # requester (as identified by CF Access) matches it.  Skipped when the
    # job didn't capture an owner (dev mode).
    meta_path = job_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        owner_email = (meta or {}).get("ownerEmail")
        if owner_email:
            user = getattr(request.state, "user", None) or {}
            requester_email = user.get("email") if isinstance(user, dict) else None
            if not requester_email or requester_email.lower() != str(owner_email).lower():
                raise HTTPException(status_code=403, detail="not_owner")

        # Deferred debit: the client disconnected before /remediate could
        # charge, so the credit is taken here, on the first successful
        # download — exactly once even when downloads race, because the
        # ledger (not this manifest) is the guard; see _collect_deferred_charge.
        # If the wallet is now empty the file is withheld with a 402 — the
        # user still has not paid for anything they did not receive.
        if isinstance(meta, dict):
            _collect_deferred_charge(safe_id, meta_path, meta)

    # Audit log: record the download.
    try:
        ctx = _audit.context_from_request(request)
        _audit.record_event(
            event="download",
            request_id=ctx.get("request_id"),
            actor_email=ctx.get("actor_email"),
            actor_sub=ctx.get("actor_sub"),
            ip=ctx.get("ip"),
            job_id=safe_id,
            details={"mediaType": _media_type_for(safe_name)},
        )
    except Exception:
        pass

    return FileResponse(
        path=str(target),
        filename=safe_name,
        media_type=_media_type_for(safe_name),
    )


def _suffix_filename(filename: str, suffix: str) -> str:
    p = Path(filename)
    return f"{p.stem}{suffix}{p.suffix}"


def _media_type_for(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".pdf"):
        return "application/pdf"
    if lower.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if lower.endswith(".pptx"):
        return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    if lower.endswith((".html", ".htm")):
        return "text/html; charset=utf-8"
    return "application/octet-stream"


def _pages_analyzed(tree) -> Optional[int]:
    """Pages the parser actually read, or None when it read them all."""
    props = getattr(getattr(tree.root, "metadata", None), "properties", None) or {}
    if not props.get("pages_truncated"):
        return None
    try:
        return int(props.get("pages_processed") or 0) or None
    except (TypeError, ValueError):
        return None


def _count_nodes(tree: AccessibilityTree) -> int:
    return sum(1 for _ in iter_reading_order(tree.root))


def _count_nodes_of(tree: AccessibilityTree, kind) -> int:
    return sum(1 for node in iter_reading_order(tree.root) if isinstance(node, kind))


# Which remediation actions the format's writer ACTUALLY persists to the output
# file. Actions not listed mutate only the in-memory tree (no writer support
# yet) so they must NOT be counted as "fixed" — counting an in-memory-only
# success as a real fix is how the score inflated past what the file achieves.
# PDF output is currently untagged, so only document metadata truly persists.
_PERSISTED_ACTIONS: Dict[str, set] = {
    "docx": {
        "SET_DOCUMENT_TITLE",
        "SET_DOCUMENT_LANGUAGE",
        "NORMALIZE_HEADING_LEVEL",
        # Paragraphs only *styled* as headings are promoted to real Heading
        # styles (w:pStyle) so they enter the navigation outline — verified by
        # smoke_promote_heading (re-parse sees a real HeadingNode).
        "PROMOTE_HEADING",
        "GENERATE_ALT_TEXT",
        "REMOVE_DECORATIVE_ALT_TEXT",
        "ADD_TABLE_HEADERS",
        "IMPROVE_LINK_TEXT",
        # Typed fake-list runs ("- item" paragraphs) are converted into real
        # Word lists: w:numPr per paragraph + numbering.xml definitions, with
        # the literal markers stripped — verified by smoke_fake_lists.
        "FIX_LIST_STRUCTURE",
        # Unlabeled content controls with a confident nearby label get a
        # w:alias (accessible name) — verified by smoke_form_field_labels.
        "FILL_FORM_FIELD_LABELS",
        # Low-contrast runs are recoloured to the nearest AA-passing shade via an
        # explicit w:color — verified by smoke_docx_contrast (re-parse clears the
        # flag). Counted only when the writer confirms it (see the FIX_CONTRAST
        # reconciliation against the applied list below).
        "FIX_CONTRAST",
        # Caption-less data tables get an AI-generated caption inserted as a
        # Caption-styled <w:p> above the <w:tbl> — verified by
        # smoke_docx_table_caption (re-parse reads it back and the flag clears).
        # Grounded in the table's own headers/rows. Reconciled against the
        # writer's applied list (it is in _WRITER_CONFIRMED_ACTIONS).
        "GENERATE_TABLE_CAPTION",
    },
    "pptx": {
        "SET_DOCUMENT_TITLE",
        "SET_DOCUMENT_LANGUAGE",
        # Untitled slides get a real title placeholder (cloned from the layout)
        # carrying derived text — verified by smoke_slide_title (re-parse sees a
        # titled slide and the flag clears).
        "SET_SLIDE_TITLE",
        "GENERATE_ALT_TEXT",
        "REMOVE_DECORATIVE_ALT_TEXT",
        "IMPROVE_LINK_TEXT",
        # Promote sets <a:tblPr firstRow="1">; synthesize inserts a real <a:tr>.
        "ADD_TABLE_HEADERS",
        # Typed "- item" lines in text boxes gain real a:buChar/a:buAutoNum
        # bullets with markers stripped — verified by smoke_fake_lists.
        "FIX_LIST_STRUCTURE",
        # Low-contrast runs are recoloured to the nearest AA-passing shade via an
        # explicit a:srgbClr — verified by smoke_pptx_contrast. Counted only when
        # the writer confirms it (FIX_CONTRAST applied-list reconciliation).
        "FIX_CONTRAST",
    },
    "pdf": {
        "SET_DOCUMENT_TITLE",
        "SET_DOCUMENT_LANGUAGE",
        # Images are now tagged as /Figure with /Alt in the structure tree
        # (plus /Alt on the XObject), so alt text genuinely persists for PDF.
        "GENERATE_ALT_TEXT",
        "REMOVE_DECORATIVE_ALT_TEXT",
        # The writer reconstructs a full structure tree (headings, lists,
        # tables, figures, artifacts + MarkInfo/ParentTree/XMP) on an untagged
        # PDF when — and only when — this fix is approved. Writer-confirmed
        # (see _WRITER_CONFIRMED_ACTIONS) — verified by smoke_pdf_structure/
        # -artifacts/-ruling and smoke_remediate_only_approved.
        "TAG_PDF_STRUCTURE",
        # Invisible OCR text layer on scanned pages. The executor only
        # succeeds when an OCR provider is actually available, so counting
        # it is honest — verified by smoke_ocr_layer.
        "ADD_OCR_TEXT_LAYER",
        # Unlabeled AcroForm fields with a descriptive /T get a /TU (the
        # accessible name AT announces) — verified by smoke_pdf_form_labels
        # (re-read shows /TU set and the unlabeled count drop). Confident-only:
        # auto-generated names (Text1, Check Box 3) stay manual.
        "FILL_FORM_FIELD_LABELS",
    },
    "html": {
        # html_writer does attribute/text DOM edits that each round-trip into
        # the output bytes — verified by smoke_html (re-parse sees the fix and
        # the original flag clears).
        "SET_DOCUMENT_TITLE",       # writes/creates <head><title>
        "SET_DOCUMENT_LANGUAGE",    # writes <html lang="...">
        "GENERATE_ALT_TEXT",        # writes <img alt="...">
        "NORMALIZE_HEADING_LEVEL",  # renames the heading tag (h3 -> h2)
        "IMPROVE_LINK_TEXT",        # rewrites pure-text link content
        "ADD_TABLE_HEADERS",        # promotes row-0 <td> -> <th scope=col> / inserts a header row
        # Unlabeled controls with a confident nearby label (orphan <label>,
        # "Name: [input]" text, or a table label cell) get an aria-label —
        # verified by smoke_html_form_labels (re-parse drops the unlabeled count
        # by exactly the derivable count). Ambiguous controls stay manual.
        "FILL_FORM_FIELD_LABELS",
        # Runs of typed "- item" / "1. item" <p> are converted into a real
        # <ul>/<ol> (markers stripped) — verified by smoke_html_fake_lists
        # (re-parse sees a real ListNode and the flag clears).
        "FIX_LIST_STRUCTURE",
        # Recolours low-contrast text to the nearest AA-passing shade as an
        # inline style — verified by smoke_fix_contrast (re-parse sees the new
        # colour and LOW_CONTRAST_TEXT clears). Only counted for HTML; DOCX/PPTX
        # writers don't apply the marker yet, so they are intentionally absent.
        "FIX_CONTRAST",
        # Caption-less data tables get an AI-generated <caption> inserted as the
        # table's first child — verified by smoke_table_caption_writer (re-parse
        # reads the <caption> back and TABLE_CAPTION_MISSING clears). Grounded in
        # the table's own headers/rows; routes to review like alt text.
        "GENERATE_TABLE_CAPTION",
        # WCAG 1.3.5: fields whose purpose is unambiguous get the standard
        # autocomplete token; WCAG 2.4.3: positive tabindex values are reset to
        # 0. Both are written by iterators the parser counted with, so the count
        # claimed equals the count written — verified by smoke_html_semantics.
        "SET_INPUT_AUTOCOMPLETE",
        "FIX_POSITIVE_TABINDEX",
    },
}


def _action_persists(action_code: str, source_format: str) -> bool:
    return action_code in _PERSISTED_ACTIONS.get((source_format or "").lower(), set())


# Actions whose writer appends to ``applied`` ONLY when it actually edited the
# bytes — there is no "re-assert existing structure" path for them. For these
# the writer's applied list is AUTHORITATIVE: an approved action whose target
# element didn't resolve, or whose target already had the fix (so the writer
# silently no-ops), must NOT be counted as fixed or charged. Every other
# persisted action can legitimately appear in ``applied`` as a re-assertion of
# pre-existing structure, so we can't key those off the applied list.
#   - FIX_CONTRAST: only appended on a real recolour of a resolved element.
#   - GENERATE_TABLE_CAPTION: only appended when a <caption> was actually
#     inserted (guarded by ``find("caption") is None`` + a resolved table).
#   - TAG_PDF_STRUCTURE: the executor only records the request; the PDF writer
#     marks its struct_tree entry with the action (keyed to the document root)
#     only when a structure tree was really written. No taggable page, or a
#     tagger failure, means no tree — and nothing counted or charged.
_WRITER_CONFIRMED_ACTIONS = {"FIX_CONTRAST", "GENERATE_TABLE_CAPTION", "TAG_PDF_STRUCTURE"}


def _count_persisted_fixes(executions, applied, source_format: str) -> int:
    """Count successful executions that genuinely persist into the output file.

    Gates on executor-success ∩ :func:`_action_persists`, and for the
    writer-confirmed actions additionally intersects against the writer's
    ``applied`` list (keyed by action + target) so a silent writer no-op is
    never counted or charged. This is the single honesty gate used by the
    charge path (and unit-tested by smoke_table_caption_writer).
    """
    applied_by_action: Dict[str, set] = {}
    for a in (applied or []):
        if isinstance(a, dict):
            applied_by_action.setdefault(a.get("action"), set()).add(a.get("target_id"))
    count = 0
    for e in executions:
        if getattr(e.status, "value", e.status) != "success":
            continue
        code = e.action_code.value
        if not _action_persists(code, source_format):
            continue
        if code in _WRITER_CONFIRMED_ACTIONS and e.target_node_id not in applied_by_action.get(code, set()):
            continue
        count += 1
    return count


def _build_scan_score(violations) -> PipelineScore:
    """Page-QUALITY score for a read-only URL scan (nothing is remediated).

    ``_build_score`` measures remediation PROGRESS (% of issues auto-fixed), so
    with zero executions it returns 0/'F' for any page with a single issue — a
    dishonest headline for a scan. This instead grades the page AS FOUND:
    start at 100 and deduct by severity, so one minor issue stays near the top
    and a badly-broken page sinks. Nothing is fixed, so fixedAutomatically=0 and
    every issue is pendingManual.
    """
    n = len(violations)
    if n == 0:
        return PipelineScore(initialIssues=0, fixedAutomatically=0, pendingManual=0, score=100.0, grade="A+")
    errors = sum(1 for v in violations if v.severity == Severity.ERROR.value)
    warnings = sum(1 for v in violations if v.severity == Severity.WARNING.value)
    penalty = 6.0 * errors + 2.5 * warnings  # errors hurt more than warnings
    score = max(0.0, 100.0 - penalty)
    return PipelineScore(
        initialIssues=n,
        fixedAutomatically=0,
        pendingManual=n,
        score=round(score, 2),
        grade=_grade(score),
    )


def _build_score(*, violations, executions, source_format: str = "") -> PipelineScore:
    initial = len(violations)
    if initial == 0:
        return PipelineScore(initialIssues=0, fixedAutomatically=0, pendingManual=0, score=100.0, grade="A+")

    # Only count a success as "fixed" if the writer actually persists that
    # action for this format; otherwise it's an in-memory-only change that the
    # downloaded file does not reflect, so it's really a pending-manual item.
    fixed = sum(
        1 for e in executions
        if e.status.value == "success" and _action_persists(e.action_code.value, source_format)
    )
    non_persisted = sum(
        1 for e in executions
        if e.status.value == "success" and not _action_persists(e.action_code.value, source_format)
    )
    pending = sum(1 for e in executions if e.status.value == "skipped") + non_persisted

    error_weight = sum(2 for v in violations if v.severity == Severity.ERROR.value)
    warning_weight = sum(1 for v in violations if v.severity == Severity.WARNING.value)
    total_weight = max(error_weight + warning_weight, 1)
    fixed_weight = 2 * fixed
    score = max(0.0, 100.0 * (fixed_weight / (total_weight * 2 + 0.01)))
    score = min(100.0, score + 10.0 * (fixed / max(initial, 1)))
    grade = _grade(score)
    return PipelineScore(
        initialIssues=initial,
        fixedAutomatically=fixed,
        pendingManual=pending,
        score=round(score, 2),
        grade=grade,
    )


def _grade(score: float) -> str:
    if score >= 95:
        return "A+"
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _persist_analysis_result(user_id: str, summary, score, filename) -> None:
    """Best-effort: persist the server-computed score keyed by (user, document).

    A certificate is later bound to this row so its numbers are the server's own
    measurement, not values supplied by the caller. Never raises.
    """
    document_id = getattr(summary, "documentId", None)
    if not user_id or not document_id:
        return
    try:
        from app.db.models import AnalysisResultRow
        from app.db.session_sqlalchemy import session_scope

        rid = f"{user_id}::{document_id}"
        now = datetime.utcnow()
        with session_scope() as session:
            row = session.get(AnalysisResultRow, rid)
            if row is None:
                row = AnalysisResultRow(
                    id=rid, user_id=user_id, document_id=str(document_id), created_at=now
                )
                session.add(row)
            row.filename = str(filename or document_id)[:400]
            row.source_format = str(getattr(summary, "sourceFormat", "") or "")[:16]
            row.initial_issues = int(score.initialIssues)
            row.fixed_automatically = int(score.fixedAutomatically)
            row.pending_manual = int(score.pendingManual)
            row.score = int(round(score.score))
            row.grade = str(score.grade or "")[:8]
            row.updated_at = now
            session.flush()
    except Exception:
        logger.debug("persist analysis result failed", exc_info=True)
