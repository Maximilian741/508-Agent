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
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ImageNode,
    Severity,
    TableNode,
    iter_reading_order,
)
from app.parsers import parse_to_tree
from app.persistence.db import get_repo
from app.services.remediation_engine import RemediationEngine
from app.services.remediation_planner import plan_remediations, RemediationPolicy
from app.services.remediators.registry import execute_plans
from app.writers import write_remediated

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
async def analyze(file: UploadFile = File(...), execute: bool = False) -> PipelineResponse:
    """Analyze a document and return findings.

    ``execute=False`` (the default) means the tree is **not** mutated — the
    response describes what *could* be auto-applied if the caller approved
    each finding.  Pass ``execute=true`` to also run the executors and bake
    every deterministic fix into the in-memory tree (legacy behavior).
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".docx", ".pptx"}:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix or '(none)'}")

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)

    try:
        result = parse_to_tree(str(tmp_path))
    except Exception as exc:
        logger.exception("pipeline parse failed: %s", exc)
        raise HTTPException(status_code=422, detail=f"Failed to parse document: {exc.__class__.__name__}: {exc}")
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

    tree = result.tree
    engine = RemediationEngine()
    violations = engine.detect_violations(tree)
    actions = engine.plan_actions(violations)
    executions = engine.execute(tree) if execute else []

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

    api_executions = [
        PipelineExecutionResult(
            actionCode=e.action_code.value,
            targetNodeId=e.target_node_id,
            status=e.status.value,
            notes=e.notes,
        )
        for e in executions
    ]

    score = _build_score(violations=violations, executions=executions)

    # Provider name for transparency / UI badge.
    provider_name = "heuristic"
    try:
        from app.ai.semantic_inference import build_default_provider

        provider_name = build_default_provider().name
    except Exception:
        pass

    return PipelineResponse(
        summary=summary,
        violations=api_violations,
        executions=api_executions,
        score=score,
        aiProvider=provider_name,
    )


@router.post("/remediate")
async def remediate(
    file: UploadFile = File(...),
    approved_violations: str = Form(""),
    rejected_violations: str = Form(""),
):
    """Apply only user-approved fixes and stream back the remediated file.

    ``approved_violations`` is a JSON-encoded list of violation IDs the user
    approved on the audit screen.  Anything not in that list is left
    untouched in the tree.  ``rejected_violations`` is informational — we
    record it in the response payload so the caller can confirm we saw the
    set of items they want queued for manual review.
    """

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".docx", ".pptx"}:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix or '(none)'}")

    try:
        approved_ids = set(json.loads(approved_violations) if approved_violations else [])
    except Exception:
        raise HTTPException(status_code=400, detail="approved_violations must be a JSON array of strings.")
    try:
        rejected_ids = set(json.loads(rejected_violations) if rejected_violations else [])
    except Exception:
        raise HTTPException(status_code=400, detail="rejected_violations must be a JSON array of strings.")

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    settings = get_settings()
    job_id = uuid.uuid4().hex[:12]
    job_dir = settings.materialized_root / "pipeline" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(file.filename or "document").name
    source_path = job_dir / safe_name
    source_path.write_bytes(payload)

    try:
        result = parse_to_tree(str(source_path))
    except Exception as exc:
        logger.exception("remediate parse failed: %s", exc)
        raise HTTPException(
            status_code=422,
            detail=f"Failed to parse document: {exc.__class__.__name__}: {exc}",
        )

    tree = result.tree
    engine = RemediationEngine()
    violations = engine.detect_violations(tree)
    plans = plan_remediations(tree, RemediationPolicy())

    # Filter plans down to only those whose violation id is approved.
    selected_plans = []
    for v in violations:
        if v.violation_id not in approved_ids:
            continue
        for plan in plans:
            if plan.target_node_id == v.location.node_id and plan.flag.code.value == v.rule_id:
                selected_plans.append(plan)
                break

    executions = execute_plans(tree, selected_plans) if selected_plans else []

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

    # Write the remediated artifact via the format-specific writer.
    output_name = _suffix_filename(safe_name, "-remediated")
    output_path = job_dir / output_name
    write_result = write_remediated(source_path, tree, output_path, source_format=result.format)

    response_meta = {
        "jobId": job_id,
        "filename": output_name,
        "downloadUrl": f"/pipeline/files/{job_id}/{output_name}",
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
    }

    # Drop a side-by-side metadata file so subsequent /pipeline/files calls
    # can echo the run summary.
    try:
        (job_dir / "meta.json").write_text(json.dumps(response_meta, indent=2), encoding="utf-8")
    except Exception:
        pass

    return response_meta


@router.get("/files/{job_id}/{filename}")
async def download_remediated_file(job_id: str, filename: str):
    """Download an artifact previously produced by /pipeline/remediate."""

    settings = get_settings()
    safe_id = "".join(c for c in job_id if c.isalnum() or c in "-_")[:64]
    safe_name = Path(filename).name
    if not safe_id or not safe_name:
        raise HTTPException(status_code=400, detail="invalid_job_or_filename")
    target = settings.materialized_root / "pipeline" / safe_id / safe_name
    if not target.exists():
        raise HTTPException(status_code=404, detail="file_not_found")
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
    return "application/octet-stream"


def _count_nodes(tree: AccessibilityTree) -> int:
    return sum(1 for _ in iter_reading_order(tree.root))


def _count_nodes_of(tree: AccessibilityTree, kind) -> int:
    return sum(1 for node in iter_reading_order(tree.root) if isinstance(node, kind))


def _build_score(*, violations, executions) -> PipelineScore:
    initial = len(violations)
    if initial == 0:
        return PipelineScore(initialIssues=0, fixedAutomatically=0, pendingManual=0, score=100.0, grade="A+")

    fixed = sum(1 for e in executions if e.status.value == "success")
    pending = sum(1 for e in executions if e.status.value == "skipped")
    error_weight = sum(2 for v in violations if v.severity == Severity.ERROR.value)
    warning_weight = sum(1 for v in violations if v.severity == Severity.WARNING.value)
    total_weight = max(error_weight + warning_weight, 1)
    fixed_weight = 0
    for execution, _ in zip(executions, range(len(executions))):  # 1:1 with violations
        if execution.status.value == "success":
            fixed_weight += 2
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
