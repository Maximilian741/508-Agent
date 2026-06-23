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
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import require_user_id, require_user_id_or_api_key
from app.config import get_settings
from app.security.signing import sign_file_url, verify_file_signature
from app.security.uploads import stream_to_tempfile
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ImageNode,
    Severity,
    TableNode,
    iter_reading_order,
)
from app.parsers import parse_to_tree
from app.persistence import audit_log as _audit
from app.persistence.db import get_repo
from app.services.remediation_engine import RemediationEngine
from app.services.remediation_planner import plan_remediations, RemediationPolicy
from app.services.remediators.registry import execute_plans
from app.writers import write_remediated
from app.api.credits import (
    DOC_FORMAT_COSTS,
    InsufficientCreditsError,
    spend_credits_for_user,
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
    nodeCount: int = 0
    imageCount: int = 0
    tableCount: int = 0


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


class PipelineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: PipelineSummary
    violations: List[PipelineViolation]
    executions: List[PipelineExecutionResult]
    score: PipelineScore
    aiProvider: str


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
    engine = RemediationEngine()
    violations = await run_in_threadpool(engine.detect_violations, tree)
    actions = engine.plan_actions(violations)
    executions = (await run_in_threadpool(engine.execute, tree)) if execute else []

    summary = PipelineSummary(
        documentId=result.document_id,
        sourceFormat=result.format,
        title=tree.root.metadata.properties.get("title"),
        language=tree.root.metadata.language,
        pageCount=int(result.raw_metadata.get("page_count") or result.raw_metadata.get("slide_count") or 0),
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

        ensure_balance_for(target_id, cost)
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

            if _overage_eligible(target_id):
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
                    "id": f"mr-{result.document_id}-{v.violation_id}",
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
            REPO.add_manual_review_items(result.document_id, review_items)
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
    # FIX_CONTRAST is the one persisted action with no "re-assert existing
    # structure" path: the writer appends it to ``applied`` ONLY when it
    # actually recoloured a resolved element. So for it (unlike the structural
    # fixes) the writer's applied list is authoritative — an approved recolour
    # whose element didn't resolve must NOT be counted or charged.
    _applied_contrast_targets = {
        a.get("target_id")
        for a in (_applied or [])
        if isinstance(a, dict) and a.get("action") == "FIX_CONTRAST"
    }
    persisted_fixes = 0
    for e in executions:
        if getattr(e.status, "value", e.status) != "success":
            continue
        code = e.action_code.value
        if not _action_persists(code, fmt):
            continue
        if code == "FIX_CONTRAST" and e.target_node_id not in _applied_contrast_targets:
            continue
        persisted_fixes += 1
    charged = False
    if persisted_fixes > 0:
        # Charge AFTER the file exists. On the rare race where the wallet was
        # drained since the precheck, this 402s and we clean up without
        # delivering a file.
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

    Each job must be one the caller produced via /pipeline/remediate. We
    validate that the job directory + file exist and — when CF Access
    recorded an owner on the job's meta.json — that it matches the requester,
    mirroring the per-file download authz. Jobs that don't validate are
    silently skipped; a ZIP with at least one file streams, else 404.
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
            target = job_dir / safe_name
            if not target.exists():
                continue
            meta_path = job_dir / "meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
                owner_email = (meta or {}).get("ownerEmail")
                if owner_email and (
                    not requester_email or requester_email.lower() != str(owner_email).lower()
                ):
                    continue
            # De-duplicate names inside the archive (two "report-remediated.pdf").
            arc = safe_name
            n = 1
            while arc in used_names:
                arc = f"{Path(safe_name).stem} ({n}){Path(safe_name).suffix}"
                n += 1
            used_names.add(arc)
            try:
                zf.write(str(target), arcname=arc)
                added += 1
            except Exception:
                continue

    if added == 0:
        raise HTTPException(status_code=404, detail="no_files_available")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="508-remediated-batch.zip"'},
    )


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
        # tables, figures, artifacts + MarkInfo/ParentTree/XMP) on every
        # untagged PDF — verified by smoke_pdf_structure/-artifacts/-ruling.
        "TAG_PDF_STRUCTURE",
        # Invisible OCR text layer on scanned pages. The executor only
        # succeeds when an OCR provider is actually available, so counting
        # it is honest — verified by smoke_ocr_layer.
        "ADD_OCR_TEXT_LAYER",
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
        # Recolours low-contrast text to the nearest AA-passing shade as an
        # inline style — verified by smoke_fix_contrast (re-parse sees the new
        # colour and LOW_CONTRAST_TEXT clears). Only counted for HTML; DOCX/PPTX
        # writers don't apply the marker yet, so they are intentionally absent.
        "FIX_CONTRAST",
    },
}


def _action_persists(action_code: str, source_format: str) -> bool:
    return action_code in _PERSISTED_ACTIONS.get((source_format or "").lower(), set())


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
