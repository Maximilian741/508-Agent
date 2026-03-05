"""Document upload and mock scan workflow routes."""

from __future__ import annotations

import shutil
import threading
import time
from datetime import UTC, datetime
from enum import Enum
import mimetypes
import os
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, BooleanObject, ContentStream, DictionaryObject, NameObject, NumberObject, TextStringObject
import difflib
import json
from docx import Document as DocxDocument
from pptx import Presentation

from app.pdf.tag_tree import extract_tag_tree
from app.parsers.docx_parser import DOCXParser
from app.parsers.pptx_parser import PPTXParser
from app.ai.alt_text_suggester import build_alt_text_suggestions
from app.ai.auto_review import apply_decision as ai_apply_decision
from app.ai.auto_review import build_alt_context, is_alt_text_manual_item, propose_alt_text
from app.config import get_settings
from app.persistence.db import get_repo
from app.schemas.status import DocStatusListResponse, DocStatusSummary
from app.storage import encode_storage_key, get_storage, parse_artifact_ref
from app.storage.materialize import cleanup_materialized_scope, materialize_to_path

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parents[2]
RUNTIME_DIR = BASE_DIR / ".runtime"
UPLOADS_DIR = RUNTIME_DIR / "uploads"
FIXED_DIR = RUNTIME_DIR / "fixed"
RESULTS_DIR = RUNTIME_DIR / "results"

JOBS: Dict[str, Dict[str, object]] = {}
DOCS: Dict[str, Dict[str, object]] = {}
ISSUES: Dict[str, List[Dict[str, object]]] = {}
LOCK = threading.Lock()
MAX_DIFF_CHARS = 20000
REPO = get_repo()
SETTINGS = get_settings()
STORAGE = get_storage()


class DocumentType(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"


class JobPolicyRequest(BaseModel):
    policy_pack_id: str


class ScanStartRequest(BaseModel):
    policy_pack_id: Optional[str] = None


class AiReviewRequest(BaseModel):
    mode: str = "propose"  # propose | apply
    maxItems: Optional[int] = 20
    minConfidence: Optional[float] = 0.8


def _ensure_dirs() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    FIXED_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx"}
_ALLOWED_MIME_HINTS = {
    ".pdf": {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".pptx": {"application/vnd.openxmlformats-officedocument.presentationml.presentation"},
}


def _sniff_signature(payload: bytes, suffix: str) -> bool:
    if suffix == ".pdf":
        return payload.startswith(b"%PDF-")
    if suffix in {".docx", ".pptx"}:
        return payload.startswith(b"PK\x03\x04")
    return False


def _safe_filename(raw_name: str) -> str:
    name = Path(str(raw_name or "upload.bin")).name
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", ".", " "} else "_" for ch in name).strip()
    if not safe:
        safe = "upload.bin"
    return safe


def _validate_upload_header(filename: str, content_type: str) -> str:
    suffix = Path(filename or "").suffix.lower().strip()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported file type. Only PDF, DOCX, and PPTX are allowed.")
    hinted = (content_type or "").split(";")[0].strip().lower()
    expected_hints = _ALLOWED_MIME_HINTS.get(suffix, set())
    if hinted and hinted != "application/octet-stream" and expected_hints and hinted not in expected_hints:
        raise HTTPException(status_code=400, detail="Upload MIME type does not match file extension.")
    return suffix


def _stream_to_quarantine(upload: UploadFile, dest_path: Path, *, max_bytes: int) -> Tuple[int, bytes]:
    size = 0
    sniff = bytearray()
    with dest_path.open("wb") as handle:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            if len(sniff) < 64:
                remaining = 64 - len(sniff)
                sniff.extend(chunk[:remaining])
            size += len(chunk)
            if size > max_bytes:
                handle.close()
                try:
                    dest_path.unlink(missing_ok=True)
                except Exception:
                    pass
                raise HTTPException(status_code=413, detail=f"File exceeds MAX_UPLOAD_MB ({SETTINGS.max_upload_mb}MB).")
            handle.write(chunk)
    return size, bytes(sniff[:64])


def _content_type_for_name(name: str) -> Optional[str]:
    media_type, _ = mimetypes.guess_type(name)
    return media_type


def _download_response_from_ref(ref_value: object, *, filename_hint: Optional[str] = None, media_type: Optional[str] = None):
    parsed = parse_artifact_ref(ref_value)
    if parsed.type == "storage_key":
        if SETTINGS.storage_provider == "s3":
            return RedirectResponse(
                url=STORAGE.get_download_url(parsed.value, expires_seconds=SETTINGS.presign_expires_seconds),
                status_code=302,
            )
        if not STORAGE.exists(parsed.value):
            raise HTTPException(status_code=404, detail="Stored object not found")
        local_path = STORAGE.resolve_local_path(parsed.value)
        return FileResponse(str(local_path), filename=filename_hint or local_path.name, media_type=media_type or _content_type_for_name(local_path.name))

    path = Path(parsed.value)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact file not found")
    return FileResponse(str(path), filename=filename_hint or path.name, media_type=media_type or _content_type_for_name(path.name))


def _materialize_local_path(doc_id: str, ref_value: object, fallback_name: str) -> Path:
    parsed = parse_artifact_ref(ref_value)
    if parsed.type == "local_path":
        if not parsed.value:
            raise HTTPException(status_code=404, detail="Artifact path missing")
        return Path(parsed.value)
    scope = f"{doc_id}-{int(time.time() * 1000)}"
    path = materialize_to_path(
        storage=STORAGE,
        artifact_ref=ref_value,
        base_dir=SETTINGS.materialized_root,
        scope=scope,
        filename_hint=fallback_name,
    )
    try:
        scopes = [p for p in SETTINGS.materialized_root.glob(f"{doc_id}-*") if p.is_dir()]
        scopes = sorted(scopes, key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in scopes[5:]:
            cleanup_materialized_scope(SETTINGS.materialized_root, stale.name)
    except Exception:
        pass
    return path


def _infer_doc_type(filename: str) -> DocumentType:
    name = filename.lower().strip()
    if name.endswith(".docx"):
        return DocumentType.DOCX
    if name.endswith(".pptx"):
        return DocumentType.PPTX
    return DocumentType.PDF


def _get_doc(doc_id: str) -> Optional[Dict[str, object]]:
    with LOCK:
        cached = DOCS.get(doc_id)
    if cached:
        return cached
    persisted = REPO.get_document(doc_id)
    if persisted:
        with LOCK:
            DOCS[doc_id] = {
                "filename": persisted.get("filename"),
                "path": persisted.get("path"),
                "localPath": persisted.get("localPath"),
                "docType": persisted.get("docType", "pdf"),
                "tagTreePath": persisted.get("tagTreePath"),
                "tagSummary": persisted.get("tagSummary"),
                "fixReport": persisted.get("fixReport"),
                "localFixedPath": persisted.get("localFixedPath"),
                "localRebuiltPath": persisted.get("localRebuiltPath"),
                "localScanTargetPath": persisted.get("localScanTargetPath"),
            }
    return DOCS.get(doc_id)


def _save_doc(doc_id: str, doc: Dict[str, object]) -> None:
    with LOCK:
        DOCS[doc_id] = doc
    payload = {
        "id": doc_id,
        "filename": doc.get("filename"),
        "docType": doc.get("docType", "pdf"),
        "path": doc.get("path"),
        "localPath": doc.get("localPath"),
        "scanTargetPath": doc.get("scanTargetPath"),
        "localScanTargetPath": doc.get("localScanTargetPath"),
        "tagTreePath": doc.get("tagTreePath"),
        "tagSummary": doc.get("tagSummary"),
        "fixReport": doc.get("fixReport"),
    }
    if doc.get("fixedPath"):
        payload["fixedPath"] = doc.get("fixedPath")
    if doc.get("localFixedPath"):
        payload["localFixedPath"] = doc.get("localFixedPath")
    if doc.get("rebuiltPath"):
        payload["rebuiltPath"] = doc.get("rebuiltPath")
    if doc.get("localRebuiltPath"):
        payload["localRebuiltPath"] = doc.get("localRebuiltPath")
    REPO.save_document(payload)


def _job_update(job_id: str, **updates: object) -> None:
    with LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(updates)
            REPO.update_job(job_id, updates)


def _resolve_policy_pack(policy_pack_id: Optional[str] = None) -> Dict[str, object]:
    if policy_pack_id:
        pack = REPO.get_policy_pack(policy_pack_id)
        if not pack:
            raise HTTPException(status_code=404, detail="Policy pack not found")
        return pack
    default_id = "policy-508-wcag20-aa"
    pack = REPO.get_policy_pack(default_id)
    if pack:
        return pack
    packs = REPO.list_policy_packs()
    if packs:
        return packs[0]
    raise HTTPException(status_code=500, detail="No policy packs available")


def _snapshot_job_policy(job_id: str, policy_pack_id: Optional[str] = None, overwrite: bool = False) -> Dict[str, object]:
    existing = REPO.get_job_policy_snapshot(job_id)
    if existing and not overwrite:
        return existing
    pack = _resolve_policy_pack(policy_pack_id)
    REPO.save_job_policy_snapshot(
        job_id=job_id,
        policy_pack_id=str(pack.get("id") or ""),
        policy_name=str(pack.get("name") or "Unnamed policy"),
        policy_version=int(pack.get("version") or 1),
        policy_json=pack.get("policy_json", {}) if isinstance(pack.get("policy_json"), dict) else {},
    )
    snapshot = REPO.get_job_policy_snapshot(job_id)
    if not snapshot:
        raise HTTPException(status_code=500, detail="Failed to persist job policy snapshot")
    return snapshot


def _normalize_scoring_severity(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if value in {"critical", "serious", "moderate", "minor"}:
        return value
    if value == "error":
        return "serious"
    if value == "warning":
        return "moderate"
    return "minor"


def _policy_override_for_rule(policy_json: Dict[str, object], rule_id: str, doc_type: str) -> Dict[str, object]:
    rules = policy_json.get("rules", {}) if isinstance(policy_json.get("rules"), dict) else {}
    overrides = rules.get("overrides", {}) if isinstance(rules.get("overrides"), dict) else {}
    normalized_rule = str(rule_id or "").strip()
    normalized_doc = str(doc_type or "").strip().upper()
    candidates = [
        normalized_rule,
        normalized_rule.upper(),
        f"{normalized_doc}.{normalized_rule}",
        f"{normalized_doc}.{normalized_rule.upper()}",
    ]
    for key in candidates:
        if key in overrides and isinstance(overrides.get(key), dict):
            return overrides.get(key)  # type: ignore[return-value]
    return {}


def _compute_score_payload(
    issues: List[Dict[str, object]],
    policy_json: Dict[str, object],
    doc_type: str,
) -> Dict[str, object]:
    scoring = policy_json.get("scoring", {}) if isinstance(policy_json.get("scoring"), dict) else {}
    thresholds = policy_json.get("thresholds", {}) if isinstance(policy_json.get("thresholds"), dict) else {}
    status_rules = thresholds.get("statusRules", {}) if isinstance(thresholds.get("statusRules"), dict) else {}
    pass_rule = status_rules.get("pass", {}) if isinstance(status_rules.get("pass"), dict) else {}
    needs_review_rule = status_rules.get("needs_review", {}) if isinstance(status_rules.get("needs_review"), dict) else {}
    rules_config = policy_json.get("rules", {}) if isinstance(policy_json.get("rules"), dict) else {}
    defaults = rules_config.get("defaults", {}) if isinstance(rules_config.get("defaults"), dict) else {}
    default_enabled = bool(defaults.get("enabled", True))

    severity_weights = scoring.get("severityWeights", {}) if isinstance(scoring.get("severityWeights"), dict) else {}
    base_score = float(scoring.get("baseScore", 100) or 100)
    confidence_multiplier = bool(scoring.get("confidenceMultiplier", False))
    coverage_penalty = scoring.get("coveragePenalty", {}) if isinstance(scoring.get("coveragePenalty"), dict) else {}
    coverage_enabled = bool(coverage_penalty.get("enabled", False))
    per_skipped = float(coverage_penalty.get("perSkippedRule", 0.0) or 0.0)
    max_penalty = float(coverage_penalty.get("maxPenalty", 0.0) or 0.0)

    counts = {"critical": 0, "serious": 0, "moderate": 0, "minor": 0}
    points_by_category = {"issuePenalty": 0.0, "coveragePenalty": 0.0}
    applicable = 0
    executed = 0
    skipped = 0
    total_deduction = 0.0

    for issue in issues:
        rule_id = str(issue.get("ruleId", "")).strip()
        override = _policy_override_for_rule(policy_json, rule_id, doc_type)
        enabled = bool(override.get("enabled", default_enabled))
        if not enabled:
            continue
        applicable += 1
        executed += 1
        severity = _normalize_scoring_severity(str(override.get("severity") or issue.get("severity") or "minor"))
        counts[severity] = int(counts.get(severity, 0)) + 1
        weight = float(severity_weights.get(severity, 0.0) or 0.0)
        confidence = 1.0
        if confidence_multiplier:
            evidence = issue.get("evidence", {}) if isinstance(issue.get("evidence"), dict) else {}
            raw_conf = evidence.get("confidence")
            if isinstance(raw_conf, (int, float)):
                confidence = max(0.0, min(1.0, float(raw_conf)))
        penalty = weight * confidence
        total_deduction += penalty
        points_by_category["issuePenalty"] += penalty

    coverage_penalty_points = 0.0
    if coverage_enabled:
        coverage_penalty_points = min(max_penalty, float(skipped) * per_skipped)
        total_deduction += coverage_penalty_points
        points_by_category["coveragePenalty"] = coverage_penalty_points

    raw_score = base_score - total_deduction
    score_total = int(max(0, min(100, round(raw_score))))

    pass_critical = int(pass_rule.get("maxCritical", 0) or 0)
    pass_serious = int(pass_rule.get("maxSerious", 0) or 0)
    needs_critical = int(needs_review_rule.get("maxCritical", 0) or 0)
    needs_serious = int(needs_review_rule.get("maxSerious", 0) or 0)
    if counts["critical"] <= pass_critical and counts["serious"] <= pass_serious:
        status = "pass"
    elif counts["critical"] <= needs_critical and counts["serious"] <= needs_serious:
        status = "needs_review"
    else:
        status = "fail"

    return {
        "scoreTotal": score_total,
        "status": status,
        "countsBySeverity": counts,
        "pointsByCategory": points_by_category,
        "coverage": {"applicable": applicable, "executed": executed, "skipped": skipped},
    }


def _compute_and_store_job_score(
    job_id: str,
    pass_type: str,
    issues: List[Dict[str, object]],
    doc_type: str,
) -> Dict[str, object]:
    snapshot = REPO.get_job_policy_snapshot(job_id)
    if not snapshot:
        snapshot = _snapshot_job_policy(job_id)
    policy_json = snapshot.get("policyJson", {}) if isinstance(snapshot.get("policyJson"), dict) else {}
    result = _compute_score_payload(issues=issues, policy_json=policy_json, doc_type=doc_type)
    REPO.save_job_score(
        job_id=job_id,
        pass_type=pass_type,
        score_total=int(result["scoreTotal"]),
        status=str(result["status"]),
        counts_by_severity=result["countsBySeverity"],  # type: ignore[arg-type]
        points_by_category=result["pointsByCategory"],  # type: ignore[arg-type]
        coverage=result["coverage"],  # type: ignore[arg-type]
    )
    return result


def _extract_images(reader: PdfReader, on_progress: Optional[callable] = None) -> int:
    count = 0
    total_pages = len(reader.pages)
    for idx, page in enumerate(reader.pages, start=1):
        resources = page.get("/Resources")
        if not resources:
            if on_progress:
                on_progress(idx, total_pages)
            continue
        xobjects = resources.get("/XObject")
        if not xobjects:
            if on_progress:
                on_progress(idx, total_pages)
            continue
        for obj in xobjects.values():
            try:
                xobj = obj.get_object()
            except Exception:
                continue
            if xobj.get("/Subtype") == "/Image":
                count += 1
        if on_progress:
            on_progress(idx, total_pages)
    return count


def _has_outline(reader: PdfReader) -> bool:
    try:
        outlines = reader.outline
        return bool(outlines)
    except Exception:
        return False


def _has_unlabeled_form_field(reader: PdfReader) -> bool:
    try:
        root = reader.trailer.get("/Root", {})
        acro = root.get("/AcroForm")
        if not acro:
            return False
        fields = acro.get("/Fields", [])
        for field in fields:
            try:
                field_obj = field.get_object()
            except Exception:
                field_obj = field
            name = field_obj.get("/T")
            if not name or not str(name).strip():
                return True
        return False
    except Exception:
        return False


def _is_tagged(reader: PdfReader) -> bool:
    try:
        root = reader.trailer.get("/Root", {})
        return "/StructTreeRoot" in root
    except Exception:
        return False


def _write_tag_tree(doc_id: str, tag_tree: Dict[str, object]) -> Path:
    doc_dir = RESULTS_DIR / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    dest = doc_dir / "tag_tree.json"
    dest.write_text(json.dumps(tag_tree, indent=2), encoding="utf-8")
    return dest


def _normalize_text(value: Optional[str]) -> str:
    if not value:
        return ""
    return " ".join(str(value).split()).strip().lower()


def _issue_key(issue: Dict[str, object]) -> str:
    rule_id = _normalize_text(issue.get("ruleId"))
    severity = _normalize_text(issue.get("severity"))
    title = _normalize_text(issue.get("title"))
    location = _normalize_text(issue.get("locationHint"))
    evidence = issue.get("evidence", {}) if isinstance(issue.get("evidence"), dict) else {}
    node_ids = evidence.get("nodeIds") or []
    if isinstance(node_ids, list):
        node_ids = [str(item) for item in node_ids][:10]
    else:
        node_ids = []
    pages = evidence.get("pages") or []
    if isinstance(pages, list):
        pages = [str(int(item)) for item in pages if isinstance(item, int)]
    else:
        pages = []
    if not pages:
        page = evidence.get("page")
        if isinstance(page, int):
            pages = [str(page)]
    key = "|".join(
        [
            f"rule:{rule_id}",
            f"sev:{severity}",
            f"nodes:{','.join(sorted(node_ids))}",
            f"pages:{','.join(sorted(set(pages), key=lambda x: int(x)))}",
            f"loc:{location}",
            f"title:{title}",
        ]
    )
    return key


def _summarize_issues(issues: List[Dict[str, object]]) -> Dict[str, object]:
    by_severity: Dict[str, int] = {}
    by_rule: Dict[str, int] = {}
    for issue in issues:
        sev = str(issue.get("severity", "")).lower()
        rule = str(issue.get("ruleId", "")).lower()
        by_severity[sev] = by_severity.get(sev, 0) + 1
        by_rule[rule] = by_rule.get(rule, 0) + 1
    return {"issueCount": len(issues), "bySeverity": by_severity, "byRuleId": by_rule}


def _anchor_counts_by_rule(issues: List[Dict[str, object]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for issue in issues:
        rule = str(issue.get("ruleId", "")).lower()
        evidence = issue.get("evidence", {}) if isinstance(issue.get("evidence"), dict) else {}
        anchors = evidence.get("anchors") or []
        if isinstance(anchors, list):
            counts[rule] = counts.get(rule, 0) + len(anchors)
    return counts


def _sample_anchors_by_rule(issues: List[Dict[str, object]]) -> Dict[str, List[Dict[str, object]]]:
    samples: Dict[str, List[Dict[str, object]]] = {}
    for issue in issues:
        rule = str(issue.get("ruleId", "")).lower()
        evidence = issue.get("evidence", {}) if isinstance(issue.get("evidence"), dict) else {}
        anchors = evidence.get("anchors") or []
        if isinstance(anchors, list) and anchors:
            if rule not in samples:
                samples[rule] = anchors[:3]
    return samples


def _compute_delta(
    before: List[Dict[str, object]],
    after: List[Dict[str, object]],
) -> Dict[str, List[Dict[str, object]]]:
    before_map = {_issue_key(issue): issue for issue in before}
    after_map = {_issue_key(issue): issue for issue in after}
    fixed = [before_map[key] for key in before_map.keys() if key not in after_map]
    remaining = [after_map[key] for key in after_map.keys() if key in before_map]
    introduced = [after_map[key] for key in after_map.keys() if key not in before_map]
    return {"fixed": fixed, "remaining": remaining, "introduced": introduced}


def _safe_tag_tree(warnings: Optional[List[str]] = None) -> Dict[str, object]:
    return {
        "tagged": False,
        "warnings": warnings or [],
        "summary": {
            "nodeCount": 0,
            "tagCounts": {},
            "figures": 0,
            "figuresMissingAlt": 0,
            "headings": {},
            "tables": {},
        },
        "tree": {"rootId": "0", "nodes": {}},
    }


def _outline_count(reader: PdfReader) -> int:
    try:
        outlines = reader.outline
        if not outlines:
            return 0
        return len(outlines) if isinstance(outlines, list) else 1
    except Exception:
        return 0


def _form_field_counts(reader: PdfReader) -> Dict[str, int]:
    total = 0
    unlabeled = 0
    try:
        root = reader.trailer.get("/Root", {})
        acro = root.get("/AcroForm")
        if not acro:
            return {"total": 0, "unlabeled": 0}
        fields = acro.get("/Fields", [])
        total = len(fields)
        for field in fields:
            try:
                field_obj = field.get_object()
            except Exception:
                field_obj = field
            name = field_obj.get("/T")
            if not name or not str(name).strip():
                unlabeled += 1
    except Exception:
        return {"total": total, "unlabeled": unlabeled}
    return {"total": total, "unlabeled": unlabeled}


def _apply_pdf_fixes(doc_id: str, src: Path, dest: Path) -> Dict[str, object]:
    reader = PdfReader(str(src), strict=False)
    writer = PdfWriter()

    if hasattr(writer, "clone_document_from_reader"):
        try:
            writer.clone_document_from_reader(reader)
        except Exception:
            for page in reader.pages:
                writer.add_page(page)
    else:
        for page in reader.pages:
            writer.add_page(page)

    applied: List[Dict[str, object]] = []
    manual_review_added: List[Dict[str, object]] = []
    metadata_before: Dict[str, Optional[str]] = {}
    metadata_after: Dict[str, Optional[str]] = {}

    metadata = reader.metadata or {}
    title = metadata.title if metadata else None
    metadata_before["Title"] = str(title) if title else None
    if not title or not str(title).strip():
        fallback_title = src.stem
        writer.add_metadata({"/Title": fallback_title})
        applied.append(
            {
                "fixId": f"{doc_id}-fix-title",
                "ruleId": "document_title_missing",
                "severity": "error",
                "action": "Set /Title metadata from filename.",
                "pages": [],
                "anchors": [],
                "deterministic": True,
            }
        )
        metadata_after["Title"] = fallback_title

    try:
        root = writer._root_object
        if "/Lang" not in root:
            root.update({NameObject("/Lang"): TextStringObject("en-US")})
            applied.append(
                {
                    "fixId": f"{doc_id}-fix-lang",
                    "ruleId": "document_language_missing",
                    "severity": "warning",
                    "action": "Set /Lang to en-US.",
                    "pages": [],
                    "anchors": [],
                    "deterministic": True,
                }
            )
    except Exception:
        pass

    if not _has_outline(reader):
        try:
            headings = _extract_heading_titles(reader)
            if headings:
                for heading in headings[:20]:
                    writer.add_outline_item(heading, 0)
                applied.append(
                    {
                        "fixId": f"{doc_id}-fix-outline",
                        "ruleId": "missing_outline",
                        "severity": "warning",
                        "action": "Added outline from existing headings.",
                        "pages": [],
                        "anchors": [],
                        "deterministic": True,
                    }
                )
            else:
                manual_review_added.append(
                    _queue_manual_review(
                        doc_id,
                        "missing_outline",
                        "doc-1",
                        "Missing outline",
                        "No headings available to build bookmarks.",
                        pages=[],
                        anchors=[],
                        suggested_fix="Add bookmarks matching document headings.",
                        confidence=0.7,
                    )
                )
        except Exception:
            manual_review_added.append(
                _queue_manual_review(
                    doc_id,
                    "missing_outline",
                    "doc-1",
                    "Missing outline",
                    "Failed to add bookmarks from headings.",
                    pages=[],
                    anchors=[],
                    suggested_fix="Add bookmarks matching document headings.",
                    confidence=0.5,
                )
            )

    try:
        root = reader.trailer.get("/Root", {})
        acro = root.get("/AcroForm")
        if acro:
            fields = acro.get("/Fields", [])
            for idx, field in enumerate(fields, start=1):
                try:
                    field_obj = field.get_object()
                except Exception:
                    field_obj = field
                name = field_obj.get("/T")
                tu = field_obj.get("/TU")
                if not name or not str(name).strip():
                    field_obj.update({"/T": f"Field {idx}", "/TU": f"Field {idx}"})
                    applied.append(
                        {
                            "fixId": f"{doc_id}-fix-form-{idx}",
                            "ruleId": "unlabeled_form_field",
                            "severity": "info",
                            "action": f"Set placeholder label Field {idx}.",
                            "pages": [],
                            "anchors": [str(name) if name else f"field-{idx}"],
                            "deterministic": True,
                        }
                    )
                    manual_review_added.append(
                        _queue_manual_review(
                            doc_id,
                            "unlabeled_form_field",
                            "doc-1",
                            "Unlabeled form field",
                            "Placeholder labels were applied; review field labels for accuracy.",
                            pages=[],
                            anchors=[str(name) if name else f"field-{idx}"],
                            suggested_fix="Replace placeholder labels with meaningful field names.",
                            confidence=0.6,
                        )
                    )
            writer._root_object.update({"/AcroForm": acro})
    except Exception:
        pass

    try:
        tag_tree = extract_tag_tree(reader)
        if tag_tree.get("tagged"):
            if int(tag_tree.get("summary", {}).get("figuresMissingAlt", 0) or 0) > 0:
                manual_review_added.append(
                    _queue_manual_review(
                        doc_id,
                        "missing_alt_text",
                        "doc-1",
                        "Missing alt text",
                        "Programmatic placeholder alt text is disabled; add meaningful alt text via manual review or approved AI proposal.",
                        pages=[],
                        anchors=[],
                        suggested_fix="Provide concise, meaningful alt text for each figure.",
                        confidence=0.7,
                    )
                )
        else:
            manual_review_added.append(
                _queue_manual_review(
                    doc_id,
                    "missing_tag_structure",
                    "doc-1",
                    "Missing tag structure",
                    "PDF is untagged. Manual tagging required before remediation.",
                    pages=[],
                    anchors=[],
                    suggested_fix="Tag the PDF structure per PDF/UA.",
                    confidence=0.8,
                )
            )
    except Exception:
        manual_review_added.append(
            _queue_manual_review(
                doc_id,
                "missing_tag_structure",
                "doc-1",
                "Missing tag structure",
                "Failed to analyze tag tree; manual tagging required.",
                pages=[],
                anchors=[],
                suggested_fix="Tag the PDF structure per PDF/UA.",
                confidence=0.5,
            )
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as output:
        writer.write(output)

    return {
        "applied": applied,
        "manual_review_added": manual_review_added,
        "metadata_before": metadata_before,
        "metadata_after": metadata_after,
    }


def _apply_docx_fixes(doc_id: str, src: Path, dest: Path) -> Dict[str, object]:
    shutil.copy2(src, dest)
    doc = DocxDocument(str(dest))
    core = doc.core_properties
    applied: List[Dict[str, object]] = []
    manual_review_added: List[Dict[str, object]] = []
    metadata_before = {"Title": (core.title or "").strip() or None, "Language": (getattr(core, "language", None) or "").strip() or None}
    metadata_after = dict(metadata_before)
    if not metadata_before["Title"]:
        core.title = src.stem
        metadata_after["Title"] = src.stem
        applied.append(
            {
                "fixId": f"{doc_id}-fix-title",
                "ruleId": "missing_document_title",
                "severity": "warning",
                "action": "Set DOCX title from filename.",
                "pages": [],
                "anchors": [],
                "deterministic": True,
            }
        )
    if not metadata_before["Language"]:
        try:
            core.language = "en-US"
            metadata_after["Language"] = "en-US"
            applied.append(
                {
                    "fixId": f"{doc_id}-fix-language",
                    "ruleId": "missing_language",
                    "severity": "warning",
                    "action": "Set DOCX language to en-US.",
                    "pages": [],
                    "anchors": [],
                    "deterministic": True,
                }
            )
        except Exception:
            pass
    image_count = 0
    for rel in doc.part.rels.values():
        if "image" in str(rel.reltype):
            image_count += 1
    if image_count > 0:
        manual_review_added.append(
            _queue_manual_review(
                doc_id,
                "missing_alt_text",
                "doc-1",
                "Image alt text requires review",
                "DOCX image alt text cannot be safely remediated automatically.",
                pages=[],
                anchors=[],
                suggested_fix="Review all images and set meaningful alt text.",
                confidence=0.7,
            )
        )
    doc.save(str(dest))
    return {
        "applied": applied,
        "manual_review_added": manual_review_added,
        "metadata_before": metadata_before,
        "metadata_after": metadata_after,
    }


def _apply_pptx_fixes(doc_id: str, src: Path, dest: Path) -> Dict[str, object]:
    shutil.copy2(src, dest)
    prs = Presentation(str(dest))
    core = prs.core_properties
    applied: List[Dict[str, object]] = []
    manual_review_added: List[Dict[str, object]] = []
    metadata_before = {"Title": (core.title or "").strip() or None, "Language": (getattr(core, "language", None) or "").strip() or None}
    metadata_after = dict(metadata_before)
    if not metadata_before["Title"]:
        core.title = src.stem
        metadata_after["Title"] = src.stem
        applied.append(
            {
                "fixId": f"{doc_id}-fix-title",
                "ruleId": "missing_document_title",
                "severity": "warning",
                "action": "Set PPTX title from filename.",
                "pages": [],
                "anchors": [],
                "deterministic": True,
            }
        )
    if not metadata_before["Language"]:
        try:
            core.language = "en-US"
            metadata_after["Language"] = "en-US"
            applied.append(
                {
                    "fixId": f"{doc_id}-fix-language",
                    "ruleId": "missing_language",
                    "severity": "warning",
                    "action": "Set PPTX language to en-US.",
                    "pages": [],
                    "anchors": [],
                    "deterministic": True,
                }
            )
        except Exception:
            pass
    image_count = 0
    for slide in prs.slides:
        for shape in slide.shapes:
            try:
                if shape.shape_type == 13:  # PICTURE
                    image_count += 1
            except Exception:
                continue
    if image_count > 0:
        manual_review_added.append(
            _queue_manual_review(
                doc_id,
                "missing_alt_text",
                "doc-1",
                "Image alt text requires review",
                "PPTX image alt text should be reviewed manually after deterministic fixes.",
                pages=[],
                anchors=[],
                suggested_fix="Review all slide images and add alt text where needed.",
                confidence=0.7,
            )
        )
    prs.save(str(dest))
    return {
        "applied": applied,
        "manual_review_added": manual_review_added,
        "metadata_before": metadata_before,
        "metadata_after": metadata_after,
    }


def _rebuild_pdf(doc_id: str, src: Path, dest: Path) -> Dict[str, object]:
    reader = PdfReader(str(src), strict=False)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    manual_review_added: List[Dict[str, object]] = []

    try:
        root = writer._root_object
        root.update({NameObject("/MarkInfo"): DictionaryObject({NameObject("/Marked"): BooleanObject(True)})})
        if "/Lang" not in root:
            root.update({NameObject("/Lang"): TextStringObject("en-US")})

        struct_root = DictionaryObject({NameObject("/Type"): NameObject("/StructTreeRoot")})
        doc_elem = DictionaryObject(
            {NameObject("/Type"): NameObject("/StructElem"), NameObject("/S"): NameObject("/Document")}
        )
        doc_kids = ArrayObject()
        parent_nums = ArrayObject()

        for idx, page in enumerate(writer.pages):
            page_ref = page.indirect_reference
            try:
                page_obj = page.get_object()
            except Exception:
                page_obj = page
            page_obj.update({NameObject("/StructParents"): NumberObject(idx)})
            sect_elem = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/StructElem"),
                    NameObject("/S"): NameObject("/Sect"),
                    NameObject("/Pg"): page_ref,
                    NameObject("/K"): ArrayObject(),
                }
            )
            sect_elem_ref = writer._add_object(sect_elem)
            doc_kids.append(sect_elem_ref)
            parent_nums.append(NumberObject(idx))
            page_fig_refs = ArrayObject()
            try:
                resources = page_obj.get("/Resources")
                xobjects = resources.get("/XObject") if resources else None
                image_xobjects: Dict[NameObject, object] = {}
                if xobjects:
                    for name, obj in xobjects.items():
                        try:
                            xobj = obj.get_object()
                        except Exception:
                            xobj = obj
                        if xobj.get("/Subtype") == "/Image":
                            image_xobjects[name] = xobj
                contents = page_obj.get("/Contents")
                if contents:
                    content_stream = ContentStream(contents, writer)
                    new_ops = []
                    mcid = 0
                    text_ops = {b"Tj", b"TJ", b"'", b"\""}
                    text_items: List[Dict[str, object]] = []
                    image_items: List[int] = []
                    font_size = 12.0
                    for operands, operator in content_stream.operations:
                        if operator == b"Tf" and operands and len(operands) >= 2:
                            if isinstance(operands[1], (int, float)):
                                font_size = float(operands[1])
                        if operator == b"Do" and operands:
                            name_obj = operands[0]
                            if name_obj in image_xobjects:
                                new_ops.append(
                                    (
                                        [
                                            NameObject("/Figure"),
                                            DictionaryObject({NameObject("/MCID"): NumberObject(mcid)}),
                                        ],
                                        b"BDC",
                                    )
                                )
                                new_ops.append((operands, operator))
                                new_ops.append(([], b"EMC"))
                                image_items.append(mcid)
                                mcid += 1
                                continue
                        if operator in text_ops:
                            new_ops.append(
                                (
                                    [
                                        NameObject("/Span"),
                                        DictionaryObject({NameObject("/MCID"): NumberObject(mcid)}),
                                    ],
                                    b"BDC",
                                )
                            )
                            new_ops.append((operands, operator))
                            new_ops.append(([], b"EMC"))
                            text_value = ""
                            if operator == b"Tj" and operands:
                                text_value = str(operands[0])
                            elif operator == b"TJ" and operands:
                                text_value = "".join([str(part) for part in operands[0] if isinstance(part, str)])
                            elif operator in {b"'", b"\""} and operands:
                                text_value = str(operands[-1])
                            text_items.append({"mcid": mcid, "text": text_value, "fontSize": font_size})
                            mcid += 1
                            continue
                        new_ops.append((operands, operator))
                    content_stream.operations = new_ops
                    page_obj.update({NameObject("/Contents"): content_stream})

                    def is_list_prefix(value: str) -> bool:
                        prefix = value.strip().split(" ")[0]
                        if prefix.startswith(("-", "•")):
                            return True
                        if prefix.endswith(".") and prefix[:-1].isdigit():
                            return True
                        return False

                    text_items_sorted = sorted(text_items, key=lambda item: int(item["mcid"]))
                    font_sizes = [item.get("fontSize", 0) for item in text_items_sorted if item.get("fontSize")]
                    body_font = 12.0
                    if font_sizes:
                        sorted_sizes = sorted(font_sizes)
                        body_font = sorted_sizes[len(sorted_sizes) // 2]
                    unique_sizes = sorted({item.get("fontSize", 0) for item in text_items_sorted}, reverse=True)
                    heading_levels = {size: min(idx + 1, 6) for idx, size in enumerate(unique_sizes)}

                    blocks: List[Dict[str, object]] = []
                    idx_item = 0
                    while idx_item < len(text_items_sorted):
                        item = text_items_sorted[idx_item]
                        text_val = str(item.get("text", ""))
                        size = float(item.get("fontSize", body_font))
                        if size >= body_font * 1.25:
                            level = heading_levels.get(size, 1)
                            blocks.append({"type": f"H{level}", "mcids": [item["mcid"]]})
                            idx_item += 1
                            continue
                        if text_val and is_list_prefix(text_val):
                            blocks.append(
                                {
                                    "type": "L",
                                    "items": [
                                        {"label": [item["mcid"]], "body": [item["mcid"]]},
                                    ],
                                }
                            )
                            idx_item += 1
                            continue
                        para_mcids = [item["mcid"]]
                        idx_item += 1
                        while idx_item < len(text_items_sorted):
                            next_item = text_items_sorted[idx_item]
                            next_text = str(next_item.get("text", ""))
                            next_size = float(next_item.get("fontSize", body_font))
                            if next_size >= body_font * 1.25 or (next_text and is_list_prefix(next_text)):
                                break
                            para_mcids.append(next_item["mcid"])
                            idx_item += 1
                        blocks.append({"type": "P", "mcids": para_mcids})

                    for mcid_value in image_items:
                        blocks.append({"type": "Figure", "mcids": [mcid_value]})

                    blocks_sorted = sorted(blocks, key=lambda b: min(b.get("mcids", [0])))
                    max_mcid = -1
                    for block in blocks_sorted:
                        for m in block.get("mcids", []):
                            max_mcid = max(max_mcid, int(m))
                        for item in block.get("items", []):
                            for m in item.get("label", []) + item.get("body", []):
                                max_mcid = max(max_mcid, int(m))
                    mcid_map = ArrayObject([None] * (max_mcid + 1 if max_mcid >= 0 else 0))

                    for block in blocks_sorted:
                        block_type = block.get("type")
                        if block_type == "L":
                            list_elem = DictionaryObject(
                                {
                                    NameObject("/Type"): NameObject("/StructElem"),
                                    NameObject("/S"): NameObject("/L"),
                                    NameObject("/Pg"): page_ref,
                                    NameObject("/K"): ArrayObject(),
                                }
                            )
                            list_elem_ref = writer._add_object(list_elem)
                            for li in block.get("items", []):
                                li_elem = DictionaryObject(
                                    {
                                        NameObject("/Type"): NameObject("/StructElem"),
                                        NameObject("/S"): NameObject("/LI"),
                                        NameObject("/Pg"): page_ref,
                                        NameObject("/K"): ArrayObject(),
                                    }
                                )
                                li_elem_ref = writer._add_object(li_elem)
                                lbl_elem = DictionaryObject(
                                    {
                                        NameObject("/Type"): NameObject("/StructElem"),
                                        NameObject("/S"): NameObject("/Lbl"),
                                        NameObject("/Pg"): page_ref,
                                        NameObject("/K"): ArrayObject([NumberObject(m) for m in li.get("label", [])]),
                                    }
                                )
                                lbl_ref = writer._add_object(lbl_elem)
                                body_elem = DictionaryObject(
                                    {
                                        NameObject("/Type"): NameObject("/StructElem"),
                                        NameObject("/S"): NameObject("/LBody"),
                                        NameObject("/Pg"): page_ref,
                                        NameObject("/K"): ArrayObject([NumberObject(m) for m in li.get("body", [])]),
                                    }
                                )
                                body_ref = writer._add_object(body_elem)
                                li_elem_ref.get_object()[NameObject("/K")].extend([lbl_ref, body_ref])
                                list_elem_ref.get_object()[NameObject("/K")].append(li_elem_ref)
                                for m in li.get("label", []) + li.get("body", []):
                                    if 0 <= int(m) < len(mcid_map):
                                        mcid_map[int(m)] = li_elem_ref
                            sect_elem_ref.get_object()[NameObject("/K")].append(list_elem_ref)
                            continue
                        tag = block_type if isinstance(block_type, str) else "P"
                        mcid_list = [NumberObject(m) for m in block.get("mcids", [])]
                        elem_dict = {
                            NameObject("/Type"): NameObject("/StructElem"),
                            NameObject("/S"): NameObject(f"/{tag}"),
                            NameObject("/Pg"): page_ref,
                            NameObject("/K"): ArrayObject(mcid_list) if len(mcid_list) > 1 else (mcid_list[0] if mcid_list else ArrayObject()),
                        }
                        elem = DictionaryObject(elem_dict)
                        elem_ref = writer._add_object(elem)
                        sect_elem_ref.get_object()[NameObject("/K")].append(elem_ref)
                        for m in block.get("mcids", []):
                            if 0 <= int(m) < len(mcid_map):
                                mcid_map[int(m)] = elem_ref

                    parent_nums.append(mcid_map)
            except Exception:
                continue

        doc_elem.update({NameObject("/K"): doc_kids})
        doc_elem_ref = writer._add_object(doc_elem)
        struct_root.update({NameObject("/K"): ArrayObject([doc_elem_ref])})
        struct_root.update({NameObject("/ParentTree"): DictionaryObject({NameObject("/Nums"): parent_nums})})
        struct_root_ref = writer._add_object(struct_root)
        root.update({NameObject("/StructTreeRoot"): struct_root_ref})
    except Exception as exc:
        manual_review_added.append(
            _queue_manual_review(
                doc_id,
                "rebuild_failed",
                "doc-1",
                "Rebuild tagging failed",
                f"Rebuild failed to create structure tree: {exc.__class__.__name__}",
                pages=[],
                anchors=[],
                suggested_fix="Rebuild tagging manually or retry with different settings.",
                confidence=0.4,
            )
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as output:
        writer.write(output)

    manual_review_added.append(
        _queue_manual_review(
            doc_id,
            "rebuild_needs_review",
            "doc-1",
            "Rebuilt PDF requires review",
            "Rebuild created minimal tag structure; manual review required for semantic accuracy.",
            pages=[],
            anchors=[],
            suggested_fix="Review rebuilt structure, headings, and alt text placeholders.",
            confidence=0.6,
        )
    )
    manual_review_added.append(
        _queue_manual_review(
            doc_id,
            "rebuild_structure_heuristic",
            "doc-1",
            "Rebuild structure inferred",
            "Structure tags inferred deterministically; verify headings, lists, and reading order.",
            pages=[],
            anchors=[],
            suggested_fix="Review inferred structure and adjust tags for semantic accuracy.",
            confidence=0.6,
        )
    )

    return {"manual_review_added": manual_review_added}


def _collect_mcids_from_k(k_value: object) -> List[int]:
    mcids: List[int] = []

    def walk(value: object) -> None:
        if isinstance(value, int):
            mcids.append(int(value))
            return
        try:
            obj = value.get_object()  # type: ignore[attr-defined]
        except Exception:
            obj = value
        if isinstance(obj, dict):
            if obj.get("/Type") == "/MCR":
                m = obj.get("/MCID")
                if isinstance(m, int):
                    mcids.append(int(m))
            k = obj.get("/K")
            if isinstance(k, list):
                for child in k:
                    walk(child)
            elif k is not None:
                walk(k)
        elif isinstance(obj, list):
            for child in obj:
                walk(child)

    walk(k_value)
    return mcids


def _extract_approved_alt_updates(doc_id: str, reader: PdfReader) -> Dict[Tuple[int, int], Tuple[str, str]]:
    items = REPO.list_manual_review_items_for_doc(doc_id, include_resolved=True)
    updates: Dict[Tuple[int, int], Tuple[str, str]] = {}
    pending_node_ids: List[Tuple[str, str, str]] = []  # item_id, node_id, approved_text

    for item in items:
        status = str(item.get("status", "")).lower()
        approved_text = str(item.get("approvedText", "")).strip()
        issue_id = str(item.get("issueId", "")).lower()
        reason = str(item.get("reason", "")).lower()
        if status != "approved" or not approved_text:
            continue
        if "missing_alt_text" not in issue_id and "issue-alt" not in issue_id and "alt text" not in reason:
            continue
        item_id = str(item.get("id", ""))
        anchor = item.get("anchor")
        anchors = item.get("anchors", [])
        candidates: List[object] = []
        if anchor is not None:
            candidates.append(anchor)
        if isinstance(anchors, list):
            candidates.extend(anchors)
        found_anchor = False
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            page = candidate.get("page")
            mcid = candidate.get("mcid")
            if isinstance(page, int) and isinstance(mcid, int):
                updates[(int(page), int(mcid))] = (approved_text, item_id)
                found_anchor = True
        if not found_anchor:
            node_id = str(item.get("targetNodeId", "")).strip()
            if node_id:
                pending_node_ids.append((item_id, node_id, approved_text))

    if pending_node_ids:
        try:
            tree = extract_tag_tree(reader)
            nodes = tree.get("tree", {}).get("nodes", {}) if isinstance(tree.get("tree", {}), dict) else {}
            if isinstance(nodes, dict):
                for item_id, node_id, approved_text in pending_node_ids:
                    node = nodes.get(node_id, {}) if isinstance(nodes.get(node_id, {}), dict) else {}
                    page = node.get("page")
                    mcid = None
                    if isinstance(node.get("mcid"), int):
                        mcid = int(node.get("mcid"))
                    if mcid is None:
                        for kid_id in node.get("kids", []) if isinstance(node.get("kids", []), list) else []:
                            kid = nodes.get(kid_id, {}) if isinstance(nodes.get(kid_id, {}), dict) else {}
                            if isinstance(kid.get("mcid"), int):
                                mcid = int(kid.get("mcid"))
                                break
                    if isinstance(page, int) and isinstance(mcid, int):
                        updates[(int(page), int(mcid))] = (approved_text, item_id)
        except Exception:
            pass

    return updates


def _apply_approved_alt_to_pdf(doc_id: str, pdf_path: Path) -> List[str]:
    if not pdf_path.exists():
        return []
    try:
        reader = PdfReader(str(pdf_path), strict=False)
    except Exception:
        return []

    updates = _extract_approved_alt_updates(doc_id, reader)
    if not updates:
        return []

    page_ref_map: Dict[Tuple[int, int], int] = {}
    for idx, page in enumerate(reader.pages, start=1):
        try:
            ref = page.indirect_reference
            if ref is not None:
                page_ref_map[(ref.idnum, ref.generation)] = idx
        except Exception:
            continue

    writer = PdfWriter()
    if hasattr(writer, "clone_document_from_reader"):
        try:
            writer.clone_document_from_reader(reader)
        except Exception:
            for page in reader.pages:
                writer.add_page(page)
    else:
        for page in reader.pages:
            writer.add_page(page)

    applied_item_ids: Set[str] = set()
    try:
        root = writer._root_object
        struct_root = root.get("/StructTreeRoot")
        if struct_root:
            stack = [struct_root]
            while stack:
                node = stack.pop()
                try:
                    obj = node.get_object()
                except Exception:
                    obj = node
                if not isinstance(obj, dict):
                    continue
                tag = obj.get("/S")
                if tag == "/Figure":
                    page_num = None
                    pg = obj.get("/Pg")
                    if pg is not None and hasattr(pg, "idnum"):
                        page_num = page_ref_map.get((pg.idnum, pg.generation))
                    mcids = _collect_mcids_from_k(obj.get("/K"))
                    if page_num is not None:
                        for mcid in mcids:
                            update = updates.get((int(page_num), int(mcid)))
                            if update:
                                approved_text, item_id = update
                                obj.update({NameObject("/Alt"): TextStringObject(approved_text)})
                                applied_item_ids.add(item_id)
                                break
                kids = obj.get("/K")
                if isinstance(kids, list):
                    stack.extend(kids)
                elif kids is not None:
                    stack.append(kids)
    except Exception:
        return []

    if not applied_item_ids:
        return []

    try:
        with pdf_path.open("wb") as out:
            writer.write(out)
    except Exception:
        return []

    for item_id in sorted(applied_item_ids):
        item = REPO.get_manual_review_item(item_id)
        if not item:
            continue
        item["applied"] = True
        item["appliedAt"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        REPO.update_manual_review_item(item_id, item, resolved=True)

    return sorted(applied_item_ids)


def _extract_heading_titles(reader: PdfReader) -> List[str]:
    tag_tree = extract_tag_tree(reader)
    if not tag_tree.get("tagged"):
        return []
    nodes = tag_tree.get("tree", {}).get("nodes", {})
    titles: List[str] = []
    for node in nodes.values():
        tag = node.get("tag")
        if tag in {"H1", "H2", "H3", "H4", "H5", "H6"}:
            title = node.get("title") or node.get("actualText")
            if title:
                titles.append(str(title))
    return titles


def _queue_manual_review(
    doc_id: str,
    issue_id: str,
    target_node_id: str,
    reason: str,
    notes: str,
    pages: List[int],
    anchors: List[str],
    suggested_fix: str,
    confidence: float,
) -> Dict[str, object]:
    from app.api import state

    item = {
        "id": f"mr-{int(time.time() * 1000)}-{len(state.manual_review_queue)}",
        "issueId": issue_id,
        "targetNodeId": target_node_id,
        "reason": reason,
        "notes": notes,
        "pages": pages,
        "anchors": anchors,
        "instructions": notes,
        "suggestedFix": suggested_fix,
        "confidence": confidence,
        "requiresHuman": True,
    }
    state.manual_review_queue.append(item)
    REPO.add_manual_review_items(doc_id, [item])
    return item


def _manual_review_item_from_issue(
    doc_id: str,
    issue: Dict[str, object],
    suggested_fix: str,
    confidence: float = 0.7,
) -> Dict[str, object]:
    evidence = issue.get("evidence", {}) if isinstance(issue.get("evidence"), dict) else {}
    node_ids = evidence.get("nodeIds", []) if isinstance(evidence.get("nodeIds"), list) else []
    target_node_id = str(node_ids[0]) if node_ids else "doc-1"
    pages = [int(p) for p in evidence.get("pages", []) if isinstance(p, int)] if isinstance(evidence.get("pages"), list) else []
    anchors = evidence.get("anchors", []) if isinstance(evidence.get("anchors"), list) else []
    issue_id = str(issue.get("id") or issue.get("ruleId") or f"{doc_id}-issue")
    reason = str(issue.get("title") or issue.get("ruleId") or "Manual review required")
    notes = str(issue.get("description") or "Issue remains after deterministic fixes.")
    return {
        "id": f"mr-{int(time.time() * 1000)}-{issue_id}",
        "issueId": issue_id,
        "targetNodeId": target_node_id,
        "reason": reason,
        "notes": notes,
        "pages": pages,
        "anchors": anchors,
        "instructions": notes,
        "suggestedFix": suggested_fix,
        "confidence": confidence,
        "requiresHuman": True,
        "status": "pending",
    }


def _manual_review_for_remaining_issues(
    doc_id: str,
    remaining_issues: List[Dict[str, object]],
    existing_items: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    by_rule_suggestion = {
        "missing_heading_structure": "Add proper heading structure (H1-H6 or equivalent styles) and rescan.",
        "skipped_heading_level": "Normalize heading levels so they progress without jumps.",
        "reading_order": "Review and correct logical reading order in source content.",
        "empty_heading_text": "Add text content for empty heading elements and rescan.",
        "table_missing_headers": "Add header labels to table first rows and verify scope associations.",
        "missing_slide_titles": "Add a title placeholder to each slide and rescan.",
        "generic_link_text": "Replace generic link labels with descriptive destination-oriented text.",
    }
    existing_issue_ids = {str(item.get("issueId", "")) for item in existing_items}
    generated: List[Dict[str, object]] = []
    for issue in remaining_issues:
        rule_id = str(issue.get("ruleId", "")).strip()
        if rule_id not in by_rule_suggestion:
            continue
        issue_id = str(issue.get("id") or "")
        if issue_id and issue_id in existing_issue_ids:
            continue
        generated.append(
            _manual_review_item_from_issue(
                doc_id=doc_id,
                issue=issue,
                suggested_fix=by_rule_suggestion[rule_id],
                confidence=0.75,
            )
        )
    return generated


def _extract_text(path: Path) -> str:
    reader = PdfReader(str(path), strict=False)
    chunks: List[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text:
            chunks.append(text)
    return "\n".join(chunks).strip()


def _extract_text_any(path: Path, doc_type: str) -> str:
    if doc_type == DocumentType.PDF.value:
        return _extract_text(path)
    if doc_type == DocumentType.DOCX.value:
        try:
            doc = DocxDocument(str(path))
            return "\n".join([(p.text or "") for p in doc.paragraphs]).strip()
        except Exception:
            return ""
    if doc_type == DocumentType.PPTX.value:
        try:
            prs = Presentation(str(path))
            chunks: List[str] = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    text = getattr(shape, "text", "") or ""
                    if text:
                        chunks.append(str(text))
            return "\n".join(chunks).strip()
        except Exception:
            return ""
    return ""


def _build_diff(before_text: str, after_text: str) -> str:
    before_lines = before_text.splitlines()
    after_lines = after_text.splitlines()
    diff_lines = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile="before",
        tofile="after",
        lineterm="",
    )
    return "\n".join(diff_lines)


def _analyze_pdf(
    doc_id: str,
    path: Path,
    on_page_progress: Optional[callable] = None,
    tag_tree: Optional[Dict[str, object]] = None,
) -> List[Dict[str, object]]:
    issues: List[Dict[str, object]] = []
    reader = PdfReader(str(path), strict=False)
    metadata = reader.metadata
    title = metadata.title if metadata else None
    if not title or not str(title).strip():
        issues.append(
            {
                "id": f"{doc_id}-issue-title",
                "ruleId": "document_title_missing",
                "title": "Missing document title",
                "severity": "error",
                "description": "PDF metadata title is missing or empty.",
                "locationHint": "Document metadata",
                "recommendation": "Set a descriptive document title in PDF metadata.",
                "evidence": {"pages": []},
            }
        )

    image_count = _extract_images(reader, on_progress=on_page_progress)
    struct_info = tag_tree["summary"] if tag_tree else extract_tag_tree(reader).get("summary", {})
    tagged = bool(tag_tree.get("tagged") if tag_tree else _is_tagged(reader))
    tree_nodes = tag_tree.get("tree", {}).get("nodes", {}) if tag_tree else {}
    if not tagged:
        issues.append(
            {
                "id": f"{doc_id}-issue-tags",
                "ruleId": "missing_tag_structure",
                "title": "Missing tag structure",
                "severity": "error",
                "description": "PDF is not tagged. Accessibility structure cannot be determined.",
                "locationHint": "Structure tree",
                "recommendation": "Add PDF/UA tags before remediating content.",
                "evidence": {"tagged": False, "pages": []},
            }
        )
    if image_count > 0 and not tagged:
        issues.append(
            {
                "id": f"{doc_id}-issue-images",
                "ruleId": "images_not_tagged",
                "title": "Images are not tagged",
                "severity": "warning",
                "description": "Images were detected but the PDF is not tagged.",
                "locationHint": f"{image_count} image(s) detected",
                "recommendation": "Add PDF/UA tags and alt text for images.",
                "evidence": {"pages": []},
            }
        )
    figures = struct_info.get("figures", 0)
    figures_missing_alt = struct_info.get("figuresMissingAlt", 0)
    missing_alt_nodes: List[str] = []
    missing_alt_anchors: List[Dict[str, object]] = []
    if tagged and tree_nodes:
        for node_id, node in tree_nodes.items():
            if node.get("tag") == "Figure" and not node.get("alt"):
                missing_alt_nodes.append(node_id)
                node_page = node.get("page")
                mcid = None
                for kid in node.get("kids", []):
                    kid_node = tree_nodes.get(kid, {})
                    if kid_node.get("role") == "MCID" and kid_node.get("mcid") is not None:
                        mcid = kid_node.get("mcid")
                        break
                if isinstance(node_page, int) and mcid is not None:
                    missing_alt_anchors.append({"page": node_page, "mcid": mcid, "kind": "figure"})
                if len(missing_alt_nodes) >= 50:
                    break
    missing_alt_pages: List[int] = []
    if missing_alt_nodes:
        for node_id in missing_alt_nodes:
            node_page = tree_nodes.get(node_id, {}).get("page")
            if isinstance(node_page, int) and node_page not in missing_alt_pages:
                missing_alt_pages.append(node_page)
            if len(missing_alt_pages) >= 10:
                break
    missing_alt_page = missing_alt_pages[0] if missing_alt_pages else None
    if tagged and figures and figures_missing_alt:
        issues.append(
            {
                "id": f"{doc_id}-issue-alt",
                "ruleId": "missing_alt_text",
                "title": "Images missing alternative text",
                "severity": "error",
                "description": "Tagged figures are missing alternative text.",
                "locationHint": f"Figure tags missing alt: {figures_missing_alt} of {figures} (see tag tree nodes)",
                "recommendation": "Provide meaningful alt text for each figure element.",
                "evidence": {
                    "nodeIds": missing_alt_nodes,
                    "pages": missing_alt_pages,
                    "page": missing_alt_page if missing_alt_page else None,
                    "anchors": missing_alt_anchors[:50],
                },
            }
        )

    tag_counts = struct_info.get("tagCounts", {}) if struct_info else {}
    heading_tags = sum(tag_counts.get(tag, 0) for tag in ("H1", "H2", "H3", "H4", "H5", "H6"))
    if tagged and heading_tags == 0:
        issues.append(
            {
                "id": f"{doc_id}-issue-headings",
                "ruleId": "missing_heading_structure",
                "title": "Missing heading structure",
                "severity": "warning",
                "description": "Tagged PDF has no heading elements in the structure tree.",
                "locationHint": "Structure tree",
                "recommendation": "Add heading tags (H1-H6) for navigable structure.",
                "evidence": {"headingCount": 0, "pages": []},
            }
        )
    if not tagged and not _has_outline(reader):
        issues.append(
            {
                "id": f"{doc_id}-issue-outline",
                "ruleId": "missing_outline",
                "title": "Missing document outline",
                "severity": "warning",
                "description": "PDF has no outline/bookmarks.",
                "locationHint": "Document outline",
                "recommendation": "Add bookmarks to improve navigation.",
                "evidence": {"pages": []},
            }
        )

    if _has_unlabeled_form_field(reader):
        issues.append(
            {
                "id": f"{doc_id}-issue-form",
                "ruleId": "unlabeled_form_field",
                "title": "Unlabeled form field",
                "severity": "info",
                "description": "One or more form fields are missing a label.",
                "locationHint": "AcroForm fields",
                "recommendation": "Add labels (/T) for form fields in the PDF.",
                "evidence": {"pages": []},
            }
        )

    skipped_issue = _find_skipped_heading_issue(tree_nodes)
    if skipped_issue:
        issues.append(skipped_issue)

    reading_order_issue = _find_reading_order_issue(tree_nodes)
    if reading_order_issue:
        issues.append(reading_order_issue)

    return issues


def _analyze_docx(doc_id: str, path: Path) -> List[Dict[str, object]]:
    parser = DOCXParser()
    parsed = parser.parse(str(path))
    issues: List[Dict[str, object]] = []
    title = str(parsed.get("title", "") or "").strip()
    language = str(parsed.get("language", "") or "").strip()
    headings = parsed.get("headings", []) if isinstance(parsed.get("headings", []), list) else []
    empty_heading_sections = (
        [int(section) for section in parsed.get("emptyHeadingSections", []) if isinstance(section, int)]
        if isinstance(parsed.get("emptyHeadingSections", []), list)
        else []
    )
    heading_jumps = parsed.get("headingJumps", []) if isinstance(parsed.get("headingJumps", []), list) else []
    image_count = int(parsed.get("imageCount", 0) or 0)
    missing_alt = int(parsed.get("missingAltCount", 0) or 0)
    hyperlink_count = int(parsed.get("hyperlinkCount", 0) or 0)
    generic_links = parsed.get("genericLinks", []) if isinstance(parsed.get("genericLinks", []), list) else []
    table_count = int(parsed.get("tables", 0) or 0)
    tables_missing_headers = (
        [int(table_idx) for table_idx in parsed.get("tablesMissingHeaders", []) if isinstance(table_idx, int)]
        if isinstance(parsed.get("tablesMissingHeaders", []), list)
        else []
    )

    if not title:
        issues.append(
            {
                "id": f"{doc_id}-issue-title",
                "ruleId": "missing_document_title",
                "title": "Missing document title",
                "severity": "warning",
                "description": "DOCX metadata title is missing.",
                "locationHint": "Document metadata",
                "recommendation": "Set a descriptive title in document properties.",
                "evidence": {"sections": []},
            }
        )
    if not language:
        issues.append(
            {
                "id": f"{doc_id}-issue-language",
                "ruleId": "missing_language",
                "title": "Missing document language",
                "severity": "warning",
                "description": "DOCX metadata language is missing.",
                "locationHint": "Document metadata",
                "recommendation": "Set document language metadata.",
                "evidence": {"sections": []},
            }
        )
    if len(headings) == 0:
        issues.append(
            {
                "id": f"{doc_id}-issue-headings",
                "ruleId": "missing_heading_structure",
                "title": "Missing heading structure",
                "severity": "warning",
                "description": "No heading styles were detected.",
                "locationHint": "Document body",
                "recommendation": "Apply Heading 1-6 styles to section headers.",
                "evidence": {"sections": []},
            }
        )
    if heading_jumps:
        first = heading_jumps[0]
        issues.append(
            {
                "id": f"{doc_id}-issue-skipped-heading",
                "ruleId": "skipped_heading_level",
                "title": "Skipped heading level",
                "severity": "warning",
                "description": "Heading levels skip at least one level.",
                "locationHint": f"Section {first.get('section', 0)}",
                "recommendation": "Ensure heading levels progress without skips.",
                "evidence": {"anchors": [{"section": first.get("section"), "kind": "heading"}], "jumps": heading_jumps[:10]},
            }
        )
    if empty_heading_sections:
        first_section = empty_heading_sections[0]
        issues.append(
            {
                "id": f"{doc_id}-issue-empty-heading",
                "ruleId": "empty_heading_text",
                "title": "Empty heading text",
                "severity": "warning",
                "description": "One or more heading paragraphs have empty text.",
                "locationHint": f"Section {first_section}",
                "recommendation": "Populate heading text for each heading style paragraph.",
                "evidence": {
                    "sections": empty_heading_sections[:20],
                    "anchors": [{"section": section, "kind": "heading"} for section in empty_heading_sections[:20]],
                },
            }
        )
    if table_count > 0 and tables_missing_headers:
        first_table = tables_missing_headers[0]
        issues.append(
            {
                "id": f"{doc_id}-issue-table-headers",
                "ruleId": "table_missing_headers",
                "title": "Table may be missing headers",
                "severity": "warning",
                "description": "One or more tables have an empty first row; table headers may be missing.",
                "locationHint": f"Table {first_table}",
                "recommendation": "Add clear header labels in the first row of each data table.",
                "evidence": {
                    "tables": tables_missing_headers[:20],
                    "anchors": [{"table": table_idx, "kind": "table"} for table_idx in tables_missing_headers[:20]],
                },
            }
        )
    if hyperlink_count > 0 and generic_links:
        first_link = generic_links[0] if isinstance(generic_links[0], dict) else {}
        first_section = int(first_link.get("section", 0) or 0)
        issues.append(
            {
                "id": f"{doc_id}-issue-generic-link-text",
                "ruleId": "generic_link_text",
                "title": "Generic link text detected",
                "severity": "warning",
                "description": "One or more links use generic text (for example: 'click here').",
                "locationHint": f"Section {first_section}" if first_section else "Document body",
                "recommendation": "Use descriptive link text that explains the destination or action.",
                "evidence": {
                    "links": generic_links[:20],
                    "hyperlinkCount": hyperlink_count,
                    "genericCount": len(generic_links),
                },
            }
        )
    if image_count > 0:
        issues.append(
            {
                "id": f"{doc_id}-issue-alt-review",
                "ruleId": "missing_alt_text",
                "title": "Image alt text requires review",
                "severity": "warning",
                "description": "Programmatic DOCX alt text extraction is limited; manual validation required.",
                "locationHint": f"{image_count} image(s) detected",
                "recommendation": "Review each image and add meaningful alt text where needed.",
                "evidence": {"images": image_count, "missingAltUnverified": missing_alt},
            }
        )
    return issues


def _analyze_pptx(doc_id: str, path: Path) -> List[Dict[str, object]]:
    parser = PPTXParser()
    parsed = parser.parse(str(path))
    issues: List[Dict[str, object]] = []
    title = str(parsed.get("title", "") or "").strip()
    language = str(parsed.get("language", "") or "").strip()
    headings = parsed.get("headings", []) if isinstance(parsed.get("headings", []), list) else []
    slides_missing_titles = (
        [int(slide) for slide in parsed.get("slidesMissingTitles", []) if isinstance(slide, int)]
        if isinstance(parsed.get("slidesMissingTitles", []), list)
        else []
    )
    slide_details = parsed.get("slides", []) if isinstance(parsed.get("slides", []), list) else []
    reading_order_warnings = parsed.get("readingOrderWarnings", []) if isinstance(parsed.get("readingOrderWarnings", []), list) else []
    image_count = int(parsed.get("imageCount", 0) or 0)
    missing_alt = int(parsed.get("missingAltCount", 0) or 0)
    hyperlink_count = int(parsed.get("hyperlinkCount", 0) or 0)
    generic_links = parsed.get("genericLinks", []) if isinstance(parsed.get("genericLinks", []), list) else []
    tables_missing_headers = (
        [item for item in parsed.get("tablesMissingHeaders", []) if isinstance(item, dict)]
        if isinstance(parsed.get("tablesMissingHeaders", []), list)
        else []
    )

    if not title:
        issues.append(
            {
                "id": f"{doc_id}-issue-title",
                "ruleId": "missing_document_title",
                "title": "Missing presentation title",
                "severity": "warning",
                "description": "PPTX metadata title is missing.",
                "locationHint": "Presentation metadata",
                "recommendation": "Set a descriptive title in presentation properties.",
                "evidence": {"slides": []},
            }
        )
    if not language:
        issues.append(
            {
                "id": f"{doc_id}-issue-language",
                "ruleId": "missing_language",
                "title": "Missing presentation language",
                "severity": "warning",
                "description": "PPTX metadata language is missing.",
                "locationHint": "Presentation metadata",
                "recommendation": "Set presentation language metadata.",
                "evidence": {"slides": []},
            }
        )
    if not headings:
        issues.append(
            {
                "id": f"{doc_id}-issue-headings",
                "ruleId": "missing_heading_structure",
                "title": "Missing slide title structure",
                "severity": "warning",
                "description": "No slide titles were detected.",
                "locationHint": "Slides",
                "recommendation": "Add title placeholders to improve navigability.",
                "evidence": {"slides": [int(s.get("slide")) for s in slide_details if not s.get("title")]},
            }
        )
    elif slides_missing_titles:
        issues.append(
            {
                "id": f"{doc_id}-issue-missing-slide-titles",
                "ruleId": "missing_slide_titles",
                "title": "Some slides are missing titles",
                "severity": "warning",
                "description": "Some slides do not have a detectable title placeholder.",
                "locationHint": f"Slides {', '.join(str(slide) for slide in slides_missing_titles[:5])}",
                "recommendation": "Add title placeholders for slides to improve navigability.",
                "evidence": {
                    "slides": slides_missing_titles[:50],
                    "anchors": [{"slide": slide, "kind": "title"} for slide in slides_missing_titles[:50]],
                },
            }
        )
    if image_count > 0 and missing_alt > 0:
        slides = [int(s.get("slide")) for s in slide_details if int(s.get("images", 0) or 0) > 0][:10]
        issues.append(
            {
                "id": f"{doc_id}-issue-alt",
                "ruleId": "missing_alt_text",
                "title": "Images may be missing alt text",
                "severity": "error",
                "description": "One or more slide images are missing alternative text.",
                "locationHint": f"{missing_alt} of {image_count} image(s)",
                "recommendation": "Provide meaningful alt text on slide images.",
                "evidence": {"slides": slides, "anchors": [{"slide": s, "kind": "image"} for s in slides]},
            }
        )
    if tables_missing_headers:
        first = tables_missing_headers[0]
        first_slide = int(first.get("slide", 0) or 0)
        issues.append(
            {
                "id": f"{doc_id}-issue-table-headers",
                "ruleId": "table_missing_headers",
                "title": "Table may be missing headers",
                "severity": "warning",
                "description": "One or more slide tables have an empty first row; table headers may be missing.",
                "locationHint": f"Slide {first_slide}" if first_slide else "Slides",
                "recommendation": "Add header labels in the first row of each data table.",
                "evidence": {
                    "anchors": [
                        {"slide": int(item.get("slide", 0) or 0), "table": int(item.get("table", 0) or 0), "kind": "table"}
                        for item in tables_missing_headers[:20]
                    ],
                    "count": len(tables_missing_headers),
                },
            }
        )
    if hyperlink_count > 0 and generic_links:
        first = generic_links[0] if isinstance(generic_links[0], dict) else {}
        first_slide = int(first.get("slide", 0) or 0)
        issues.append(
            {
                "id": f"{doc_id}-issue-generic-link-text",
                "ruleId": "generic_link_text",
                "title": "Generic link text detected",
                "severity": "warning",
                "description": "One or more slide links use generic text (for example: 'click here').",
                "locationHint": f"Slide {first_slide}" if first_slide else "Slides",
                "recommendation": "Use descriptive hyperlink text for each slide link.",
                "evidence": {
                    "links": generic_links[:20],
                    "hyperlinkCount": hyperlink_count,
                    "genericCount": len(generic_links),
                },
            }
        )
    if reading_order_warnings:
        first = reading_order_warnings[0]
        issues.append(
            {
                "id": f"{doc_id}-issue-reading-order",
                "ruleId": "reading_order",
                "title": "Reading order may be ambiguous",
                "severity": "warning",
                "description": "Shape order suggests potential reading order ambiguity on one or more slides.",
                "locationHint": f"Slide {first.get('slide')}",
                "recommendation": "Verify and adjust reading order in the selection pane.",
                "evidence": {
                    "slides": [int(item.get("slide")) for item in reading_order_warnings[:10]],
                    "anchors": [{"slide": int(item.get("slide")), "kind": "shape-order"} for item in reading_order_warnings[:10]],
                },
            }
        )
    return issues


def _anchors_from_nodes(nodes: Dict[str, Dict[str, object]], node_ids: List[Optional[str]]) -> List[Dict[str, object]]:
    anchors: List[Dict[str, object]] = []
    for node_id in node_ids:
        if not node_id:
            continue
        node = nodes.get(node_id)
        if not node:
            continue
        page_value = node.get("page")
        if not isinstance(page_value, int):
            for kid_id in node.get("kids", []):
                kid = nodes.get(kid_id, {})
                kid_page = kid.get("page")
                if isinstance(kid_page, int):
                    page_value = kid_page
                    break
        mcid_value = None
        if node.get("role") == "MCID" and node.get("mcid") is not None:
            mcid_value = node.get("mcid")
        if mcid_value is None:
            for kid_id in node.get("kids", []):
                kid = nodes.get(kid_id, {})
                if kid.get("role") == "MCID" and kid.get("mcid") is not None:
                    mcid_value = kid.get("mcid")
                    break
        if isinstance(page_value, int) and isinstance(mcid_value, int):
            tag = node.get("tag")
            kind = "figure" if tag == "Figure" else "text"
            anchors.append({"page": page_value, "mcid": mcid_value, "kind": kind, "nodeId": node_id})
        if len(anchors) >= 50:
            break
    return anchors


def _find_reading_order_issue(nodes: Dict[str, Dict[str, object]]) -> Optional[Dict[str, object]]:
    if not nodes:
        return None
    order: List[str] = []

    def walk(node_id: str) -> None:
        node = nodes.get(node_id)
        if not node:
            return
        order.append(node_id)
        for kid in node.get("kids", []):
            walk(kid)

    walk("0")
    last_page: Optional[int] = None
    sequence: List[str] = []
    for node_id in order:
        node = nodes.get(node_id, {})
        page_value = node.get("page")
        if not isinstance(page_value, int):
            continue
        if last_page is not None and page_value < last_page:
            sequence.append(node_id)
            break
        last_page = page_value
    if not sequence:
        return None
    node_ids = sequence[:5]
    pages: List[int] = []
    for node_id in node_ids:
        page_value = nodes.get(node_id, {}).get("page")
        if isinstance(page_value, int) and page_value not in pages:
            pages.append(page_value)
    anchors = _anchors_from_nodes(nodes, node_ids)
    return {
        "id": f"issue-reading-order-{node_ids[0]}",
        "ruleId": "reading_order",
        "title": "Reading order may be ambiguous",
        "severity": "warning",
        "description": "Structure tree order includes nodes that move backward in page order.",
        "locationHint": "Structure tree order",
        "recommendation": "Verify reading order and adjust tags if needed.",
        "evidence": {"nodeIds": node_ids, "pages": pages, "anchors": anchors},
    }


def _find_skipped_heading_issue(nodes: Dict[str, Dict[str, object]]) -> Optional[Dict[str, object]]:
    if not nodes:
        return None
    order: List[str] = []

    def walk(node_id: str) -> None:
        node = nodes.get(node_id)
        if not node:
            return
        order.append(node_id)
        for kid in node.get("kids", []):
            walk(kid)

    root = "0"
    walk(root)
    prev_level: Optional[int] = None
    prev_tag: Optional[str] = None
    prev_node_id: Optional[str] = None
    for node_id in order:
        node = nodes.get(node_id)
        if not node:
            continue
        tag = node.get("tag")
        if tag and tag.startswith("H") and len(tag) == 2 and tag[1].isdigit():
            level = int(tag[1])
            if prev_level is not None and level > prev_level + 1:
                page_value = nodes.get(node_id, {}).get("page")
                pages = []
                if isinstance(page_value, int):
                    pages.append(page_value)
                if prev_node_id:
                    prev_page = nodes.get(prev_node_id, {}).get("page")
                    if isinstance(prev_page, int) and prev_page not in pages:
                        pages.append(prev_page)
                node_ids = [value for value in [prev_node_id, node_id] if value]
                anchors = _anchors_from_nodes(nodes, node_ids)
                return {
                    "id": f"issue-skipped-heading-{node_id}",
                    "ruleId": "skipped_heading_level",
                    "title": "Skipped heading level",
                    "severity": "warning",
                    "description": "Heading levels skip at least one level in the tag tree.",
                    "locationHint": f"{prev_tag} then {tag}",
                    "recommendation": "Ensure heading levels progress in order without skipping levels.",
                    "evidence": {
                        "from": prev_tag,
                        "to": tag,
                        "nodeId": node_id,
                        "nodeIds": node_ids,
                        "pages": pages,
                        "anchors": anchors,
                        "page": page_value if isinstance(page_value, int) else None,
                    },
                }
            prev_level = level
            prev_tag = tag
            prev_node_id = node_id
    return None


def _scan_worker(job_id: str, doc_id: str) -> None:
    _job_update(job_id, status="running", progress=0, message="Scanning document")
    doc = _get_doc(doc_id)
    if not doc:
        _job_update(job_id, status="error", progress=0, message="Document not found")
        return
    source_ref = doc.get("localPath") or doc.get("path")
    doc_path = _materialize_local_path(doc_id, source_ref, str(doc.get("filename") or "document.bin"))
    doc_type = str(doc.get("docType", "pdf")).lower()
    try:
        _job_update(job_id, progress=5, message=f"Loading {doc_type.upper()}")

        def update_page_progress(current: int, total: int) -> None:
            if total <= 0:
                return
            progress = 5 + int((current / total) * 85)
            _job_update(job_id, progress=progress, message=f"Scanning page {current} of {total}")

        if doc_type == DocumentType.PDF.value:
            try:
                tag_tree = extract_tag_tree(PdfReader(str(doc_path), strict=False))
            except Exception as exc:
                tag_tree = _safe_tag_tree([f"tag tree: parse failed: {exc.__class__.__name__}"])
            tag_path = _write_tag_tree(doc_id, tag_tree)
            doc["tagTreePath"] = str(tag_path)
            doc["tagSummary"] = tag_tree.get("summary", {})
            _save_doc(doc_id, doc)
            issues = _analyze_pdf(doc_id, doc_path, on_page_progress=update_page_progress, tag_tree=tag_tree)
        elif doc_type == DocumentType.DOCX.value:
            issues = _analyze_docx(doc_id, doc_path)
        elif doc_type == DocumentType.PPTX.value:
            issues = _analyze_pptx(doc_id, doc_path)
        else:
            issues = []
        _job_update(job_id, progress=95, message="Finalizing issues")
    except Exception as exc:
        _job_update(job_id, status="error", progress=0, message=f"Scan failed: {exc.__class__.__name__}")
        return
    keys = [_issue_key(issue) for issue in issues]
    REPO.save_issues(doc_id, "before", issues, keys)
    with LOCK:
        ISSUES[doc_id] = issues
        JOBS[job_id].update(status="done", progress=100, message="Scan complete")
    doc["issues"] = issues
    _save_doc(doc_id, doc)
    try:
        _compute_and_store_job_score(job_id=job_id, pass_type="baseline", issues=issues, doc_type=doc_type)
    except Exception:
        pass
    REPO.update_job(job_id, {"status": "done", "progress": 100, "message": "Scan complete"})


@router.post("/documents/upload")
async def upload_document(file: UploadFile = File(...)) -> dict:
    _ensure_dirs()
    doc_id = f"doc-{int(time.time())}"
    doc_dir = UPLOADS_DIR / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")
    safe_name = _safe_filename(file.filename)
    suffix = _validate_upload_header(safe_name, file.content_type or "")
    quarantine_dir = doc_dir / "quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    quarantine_path = quarantine_dir / safe_name
    size, signature = _stream_to_quarantine(file, quarantine_path, max_bytes=SETTINGS.max_upload_bytes)
    if not _sniff_signature(signature, suffix):
        quarantine_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Upload signature does not match expected file type.")
    dest = doc_dir / safe_name
    shutil.move(str(quarantine_path), str(dest))
    doc_type = _infer_doc_type(safe_name).value
    storage_key = f"documents/{doc_id}/original/{Path(safe_name).name}"
    try:
        STORAGE.save_file(key=storage_key, src_path=str(dest), content_type=file.content_type or _content_type_for_name(safe_name))
        persisted_path = encode_storage_key(storage_key)
    except Exception as exc:
        if SETTINGS.storage_provider == "s3":
            raise HTTPException(status_code=500, detail=f"Failed to store upload artifact: {exc}")
        persisted_path = str(dest)
    with LOCK:
        DOCS[doc_id] = {
            "filename": safe_name,
            "path": persisted_path,
            "localPath": str(dest),
            "docType": doc_type,
        }
    _save_doc(doc_id, DOCS[doc_id])
    return {"docId": doc_id, "filename": safe_name, "sizeBytes": size, "docType": doc_type}


@router.get("/documents")
async def list_documents() -> List[Dict[str, object]]:
    return REPO.list_documents()


@router.get("/documents/status", response_model=DocStatusListResponse)
async def list_documents_status(limit: int = 50, offset: int = 0) -> Dict[str, object]:
    safe_limit = max(1, min(1000, int(limit or 50)))
    safe_offset = max(0, int(offset or 0))
    items = REPO.list_documents_with_status(limit=safe_limit, offset=safe_offset)
    total = REPO.count_documents()
    return {
        "items": items,
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
    }


@router.get("/documents/{doc_id}/status", response_model=DocStatusSummary)
async def get_document_status(doc_id: str) -> Dict[str, object]:
    payload = REPO.get_document_status(doc_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return payload


@router.post("/documents/{doc_id}/scan")
async def start_scan(doc_id: str, request: Optional[ScanStartRequest] = None) -> dict:
    if _get_doc(doc_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    job_id = f"job-{int(time.time() * 1000)}"
    with LOCK:
        JOBS[job_id] = {
            "jobId": job_id,
            "docId": doc_id,
            "status": "queued",
            "progress": 0,
            "message": "Queued",
        }
    REPO.save_job({"jobId": job_id, "docId": doc_id, "status": "queued", "progress": 0, "message": "Queued"})
    policy_pack_id = request.policy_pack_id if request else None
    _snapshot_job_policy(job_id, policy_pack_id=policy_pack_id)
    thread = threading.Thread(target=_scan_worker, args=(job_id, doc_id), daemon=True)
    thread.start()
    return {"jobId": job_id}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    with LOCK:
        job = JOBS.get(job_id)
    if not job:
        job = REPO.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    snapshot = REPO.get_job_policy_snapshot(job_id)
    if snapshot:
        job["policy"] = {
            "policyPackId": snapshot.get("policyPackId"),
            "name": snapshot.get("policyName"),
            "version": snapshot.get("policyVersion"),
        }
    return job


@router.post("/jobs/{job_id}/policy")
async def set_job_policy(job_id: str, request: JobPolicyRequest) -> Dict[str, object]:
    with LOCK:
        job = JOBS.get(job_id)
    if not job:
        job = REPO.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    status = str(job.get("status") or "")
    if status in {"done", "completed", "error", "failed"}:
        raise HTTPException(status_code=409, detail="Policy can only be changed before job completion")
    if REPO.get_job_scores(job_id):
        raise HTTPException(status_code=409, detail="Policy can no longer be changed after scoring starts")
    snapshot = _snapshot_job_policy(job_id, policy_pack_id=request.policy_pack_id, overwrite=True)
    return {
        "jobId": job_id,
        "policy": {
            "policyPackId": snapshot.get("policyPackId"),
            "name": snapshot.get("policyName"),
            "version": snapshot.get("policyVersion"),
        },
    }


@router.get("/jobs/{job_id}/score")
async def get_job_score(job_id: str) -> Dict[str, object]:
    with LOCK:
        job = JOBS.get(job_id)
    if not job:
        job = REPO.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    doc_id = str(job.get("docId") or job.get("doc_id") or "")
    if not doc_id:
        persisted_job = REPO.get_job(job_id) or {}
        doc_id = str(persisted_job.get("docId") or persisted_job.get("doc_id") or "")
    if not doc_id:
        print(f"[score] job_id={job_id} passes=0 computed=false reason=no_doc_context")
        return {"jobId": job_id, "scores": []}
    doc = _get_doc(doc_id) or {}
    doc_type = str(doc.get("docType", "pdf")).lower()
    computed = False
    scores = REPO.get_job_scores(job_id)
    if not scores:
        before_issues = REPO.get_issues(doc_id, "before")
        if before_issues:
            _compute_and_store_job_score(job_id=job_id, pass_type="baseline", issues=before_issues, doc_type=doc_type)
            computed = True
        after_issues = REPO.get_issues(doc_id, "after")
        if after_issues:
            _compute_and_store_job_score(job_id=job_id, pass_type="post_fix", issues=after_issues, doc_type=doc_type)
            computed = True
        scores = REPO.get_job_scores(job_id)
    print(f"[score] job_id={job_id} passes={len(scores)} computed={'true' if computed else 'false'}")
    return {"jobId": job_id, "scores": scores}


@router.get("/documents/{doc_id}/issues")
async def get_issues(doc_id: str) -> List[Dict[str, object]]:
    with LOCK:
        issues = ISSUES.get(doc_id, [])
    if not issues:
        issues = REPO.get_latest_issues(doc_id)
    return issues


@router.get("/documents/{doc_id}/manual-review")
async def get_document_manual_review(doc_id: str) -> List[Dict[str, object]]:
    if _get_doc(doc_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return REPO.list_manual_review_items_for_doc(doc_id, include_resolved=False)


@router.post("/documents/{doc_id}/ai-review")
async def ai_review(doc_id: str, request: AiReviewRequest) -> Dict[str, object]:
    doc = _get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    doc_type = str(doc.get("docType", "pdf")).lower()
    if doc_type != DocumentType.PDF.value:
        raise HTTPException(status_code=400, detail="AI review is currently supported for PDF alt text only.")

    mode = str(request.mode or "propose").strip().lower()
    if mode not in {"propose", "apply"}:
        raise HTTPException(status_code=400, detail="mode must be propose|apply")
    max_items = max(1, min(int(request.maxItems or 20), 200))
    min_confidence = float(request.minConfidence if request.minConfidence is not None else 0.8)

    items = REPO.list_manual_review_items_for_doc(doc_id, include_resolved=False)
    candidates: List[Dict[str, object]] = []
    for item in items:
        status = str(item.get("status") or "pending").lower()
        if status not in {"", "pending"}:
            continue
        if not is_alt_text_manual_item(item):
            continue
        candidates.append(item)
    candidates = candidates[:max_items]

    if not candidates:
        return {
            "docId": doc_id,
            "mode": mode,
            "processed": 0,
            "approved": 0,
            "escalated": 0,
            "notes": ["No eligible pending missing_alt_text manual review items found."],
            "items": [],
        }

    source_ref = (
        doc.get("scanTargetPath")
        or doc.get("fixedPath")
        or doc.get("rebuiltPath")
        or doc.get("localPath")
        or doc.get("path")
    )
    source_path = _materialize_local_path(doc_id, source_ref, str(doc.get("filename") or "document.pdf"))
    if source_path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="AI review context requires a PDF artifact.")

    processed = 0
    approved = 0
    escalated = 0
    updated: List[Dict[str, object]] = []
    for item in candidates:
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        item["docId"] = doc_id
        if mode == "propose":
            context = build_alt_context(item, source_path)
            proposal = propose_alt_text(item, context)
            item.update(proposal)
            item["aiContext"] = {
                "buildStatus": context.get("buildStatus"),
                "page": context.get("page"),
                "mcid": context.get("mcid"),
            }
            if str(item.get("aiStatus") or "") == "escalated":
                escalated += 1
            REPO.update_manual_review_item(item_id, item, resolved=False)
        else:
            item = ai_apply_decision(item, min_confidence=min_confidence)
            resolved = str(item.get("status") or "").lower() in {"approved", "rejected"}
            if str(item.get("status") or "").lower() == "approved":
                approved += 1
            if str(item.get("aiStatus") or "").lower() == "escalated":
                escalated += 1
            REPO.update_manual_review_item(item_id, item, resolved=resolved)
        processed += 1
        updated.append(
            {
                "id": item_id,
                "status": item.get("status"),
                "aiStatus": item.get("aiStatus"),
                "validatorStatus": item.get("validatorStatus"),
                "aiConfidence": item.get("aiConfidence"),
            }
        )

    pending_items = REPO.list_manual_review_items_for_doc(doc_id, include_resolved=False)
    pending_alt = [item for item in pending_items if is_alt_text_manual_item(item)]
    return {
        "docId": doc_id,
        "mode": mode,
        "processed": processed,
        "approved": approved,
        "escalated": escalated,
        "pending": len(pending_alt),
        "items": updated,
    }


@router.post("/documents/{doc_id}/apply-fixes")
async def apply_fixes(doc_id: str, mode: Optional[str] = "patch") -> dict:
    _ensure_dirs()
    doc = _get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    source_ref = doc.get("localPath") or doc.get("path")
    src = _materialize_local_path(doc_id, source_ref, str(doc.get("filename") or "document.bin"))
    doc_type = str(doc.get("docType", "pdf")).lower()
    fixed_dir = FIXED_DIR / doc_id
    fixed_dir.mkdir(parents=True, exist_ok=True)
    suffix = src.suffix if src.suffix else ".bin"
    fixed_dest = fixed_dir / f"fixed{suffix}"
    rebuild_dest = fixed_dir / "rebuilt.pdf"
    before_issues = REPO.get_issues(doc_id, "before")
    if not before_issues:
        before_issues = doc.get("issues", []) if isinstance(doc.get("issues"), list) else []
    if doc_type == DocumentType.PDF.value:
        fix_result = _apply_pdf_fixes(doc_id, src, fixed_dest)
    elif doc_type == DocumentType.DOCX.value:
        fix_result = _apply_docx_fixes(doc_id, src, fixed_dest)
    elif doc_type == DocumentType.PPTX.value:
        fix_result = _apply_pptx_fixes(doc_id, src, fixed_dest)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported document type: {doc_type}")
    rebuild_result: Dict[str, object] = {"manual_review_added": []}
    rebuilt = False
    if mode == "rebuild" and doc_type == DocumentType.PDF.value:
        rebuild_result = _rebuild_pdf(doc_id, src, rebuild_dest)
        rebuilt = True
    applied_manual_item_ids: List[str] = []
    if doc_type == DocumentType.PDF.value:
        applied_manual_item_ids.extend(_apply_approved_alt_to_pdf(doc_id, fixed_dest))
        if rebuilt:
            applied_manual_item_ids.extend(_apply_approved_alt_to_pdf(doc_id, rebuild_dest))
    if applied_manual_item_ids:
        applied_manual_item_ids = sorted(set(applied_manual_item_ids))
    fixed_issues: List[Dict[str, object]] = []
    scan_after_error: Optional[str] = None
    try:
        scan_target = rebuild_dest if rebuilt else fixed_dest
        if doc_type == DocumentType.PDF.value:
            try:
                tag_tree = extract_tag_tree(PdfReader(str(scan_target), strict=False))
            except Exception as exc:
                tag_tree = _safe_tag_tree([f"tag tree: parse failed: {exc.__class__.__name__}"])
            tag_path = _write_tag_tree(doc_id, tag_tree)
            doc["tagTreePath"] = str(tag_path)
            doc["tagSummary"] = tag_tree.get("summary", {})
            fixed_issues = _analyze_pdf(doc_id, scan_target, tag_tree=tag_tree)
        elif doc_type == DocumentType.DOCX.value:
            fixed_issues = _analyze_docx(doc_id, scan_target)
        elif doc_type == DocumentType.PPTX.value:
            fixed_issues = _analyze_pptx(doc_id, scan_target)
        else:
            fixed_issues = []
        with LOCK:
            ISSUES[doc_id] = fixed_issues
    except Exception as exc:
        scan_after_error = f"after-scan failed: {exc.__class__.__name__}: {exc}"
        fixed_issues = []
    if scan_after_error:
        after_issues = list(before_issues)
        delta = {"fixed": [], "remaining": list(before_issues), "introduced": []}
    else:
        after_issues = fixed_issues
        delta = _compute_delta(before_issues, after_issues)
    try:
        fixed_size = fixed_dest.stat().st_size
    except Exception:
        fixed_size = 0
    fixed_exists = fixed_dest.exists() and fixed_size > 0
    try:
        rebuilt_size = rebuild_dest.stat().st_size
    except Exception:
        rebuilt_size = 0
    rebuilt_exists = rebuilt and rebuild_dest.exists() and rebuilt_size > 0
    fixed_ref: Optional[str] = None
    rebuilt_ref: Optional[str] = None
    scan_target_ref: Optional[str] = None
    if fixed_exists:
        fixed_key = f"documents/{doc_id}/fixed/{fixed_dest.name}"
        try:
            STORAGE.save_file(key=fixed_key, src_path=str(fixed_dest), content_type=_content_type_for_name(fixed_dest.name))
            fixed_ref = encode_storage_key(fixed_key)
        except Exception as exc:
            if SETTINGS.storage_provider == "s3":
                raise HTTPException(status_code=500, detail=f"Failed to store fixed artifact: {exc}")
            fixed_ref = str(fixed_dest)
    if rebuilt_exists:
        rebuilt_key = f"documents/{doc_id}/rebuilt/{rebuild_dest.name}"
        try:
            STORAGE.save_file(key=rebuilt_key, src_path=str(rebuild_dest), content_type="application/pdf")
            rebuilt_ref = encode_storage_key(rebuilt_key)
        except Exception as exc:
            if SETTINGS.storage_provider == "s3":
                raise HTTPException(status_code=500, detail=f"Failed to store rebuilt artifact: {exc}")
            rebuilt_ref = str(rebuild_dest)
    if rebuilt and rebuilt_ref:
        scan_target_ref = rebuilt_ref
    elif fixed_ref:
        scan_target_ref = fixed_ref
    else:
        scan_target_ref = str(scan_target)
    print(f"[apply_fixes] fixed_path={fixed_dest} size={fixed_size}")
    print(f"[apply_fixes] before={len(before_issues)} after={len(after_issues)}")
    fixed_doc_id = doc_id
    rebuilt_doc_id = doc_id if rebuilt else None
    ai_suggestions = build_alt_text_suggestions(doc_id, doc_type, src, before_issues)
    manual_review_items = fix_result.get("manual_review_added", []) + rebuild_result.get("manual_review_added", [])
    manual_review_items.extend(ai_suggestions)
    manual_review_items.extend(
        _manual_review_for_remaining_issues(
            doc_id=doc_id,
            remaining_issues=delta.get("remaining", []) if isinstance(delta.get("remaining", []), list) else [],
            existing_items=manual_review_items,
        )
    )

    report = {
        "docId": doc_id,
        "fixedDocId": fixed_doc_id,
        "fixedPath": fixed_ref or str(fixed_dest),
        "rebuiltDocId": rebuilt_doc_id,
        "rebuiltPath": rebuilt_ref if rebuilt else None,
        "scanTargetPath": scan_target_ref,
        "localFixedPath": str(fixed_dest),
        "localRebuiltPath": str(rebuild_dest) if rebuilt else None,
        "localScanTargetPath": str(scan_target),
        "fixedExists": fixed_exists,
        "rebuiltExists": rebuilt_exists,
        "fixedSize": fixed_size,
        "rebuiltSize": rebuilt_size,
        "appliedFixes": fix_result.get("applied", []),
        "before": _summarize_issues(before_issues),
        "after": _summarize_issues(after_issues),
        "delta": delta,
        "manualReview": manual_review_items,
        "beforeAfter": {
            "metadata_before": fix_result.get("metadata_before", {}),
            "metadata_after": fix_result.get("metadata_after", {}),
        },
        "deterministic": True,
        "mode": mode,
        "rebuilt": rebuilt,
        "appliedManualReviewCount": len(applied_manual_item_ids),
        "appliedManualReviewItemIds": applied_manual_item_ids,
    }
    report["scanAfterOk"] = scan_after_error is None
    if scan_after_error:
        report["scanAfterError"] = scan_after_error
    doc["fixReport"] = report
    doc["issues_before"] = before_issues
    doc["issues_after"] = after_issues
    doc["fixedPath"] = fixed_ref or str(fixed_dest)
    doc["localFixedPath"] = str(fixed_dest)
    doc["scanTargetPath"] = scan_target_ref
    doc["localScanTargetPath"] = str(scan_target)
    if rebuilt:
        doc["rebuiltPath"] = rebuilt_ref or str(rebuild_dest)
        doc["localRebuiltPath"] = str(rebuild_dest)
    _save_doc(doc_id, doc)
    REPO.save_issues(doc_id, "before", before_issues, [_issue_key(i) for i in before_issues])
    REPO.save_issues(doc_id, "after", after_issues, [_issue_key(i) for i in after_issues])
    REPO.save_fix_report(doc_id, report)
    REPO.add_manual_review_items(doc_id, report.get("manualReview", []) if isinstance(report.get("manualReview"), list) else [])
    latest_job = REPO.get_latest_job_for_doc(doc_id)
    job_id_for_scores: Optional[str] = None
    if latest_job and latest_job.get("jobId"):
        try:
            job_id_for_scores = str(latest_job.get("jobId"))
            _compute_and_store_job_score(job_id=job_id_for_scores, pass_type="baseline", issues=before_issues, doc_type=doc_type)
            _compute_and_store_job_score(job_id=job_id_for_scores, pass_type="post_fix", issues=after_issues, doc_type=doc_type)
        except Exception:
            pass
    report_path = RESULTS_DIR / doc_id / "fix_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {"docId": doc_id, "jobId": job_id_for_scores, "fixed": True, "report": report}


@router.post("/documents/{doc_id}/finalize")
async def finalize_document(doc_id: str) -> Dict[str, object]:
    _ensure_dirs()
    doc = _get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc_type = str(doc.get("docType", "pdf")).lower()
    report = REPO.get_fix_report(doc_id) or {}
    base_ref = (
        doc.get("rebuiltPath")
        or (report.get("rebuiltPath") if isinstance(report, dict) else None)
        or doc.get("fixedPath")
        or (report.get("fixedPath") if isinstance(report, dict) else None)
        or doc.get("path")
    )
    if not base_ref:
        raise HTTPException(status_code=404, detail="No artifact available to finalize")
    base_path = _materialize_local_path(doc_id, base_ref, str(doc.get("filename") or "document.bin"))

    fixed_dir = FIXED_DIR / doc_id
    fixed_dir.mkdir(parents=True, exist_ok=True)
    suffix = base_path.suffix if base_path.suffix else ".bin"
    final_dest = fixed_dir / f"final{suffix}"
    shutil.copyfile(str(base_path), str(final_dest))

    applied_manual_item_ids: List[str] = []
    if doc_type == DocumentType.PDF.value:
        applied_manual_item_ids = _apply_approved_alt_to_pdf(doc_id, final_dest)

    before_issues = REPO.get_issues(doc_id, "before")
    if not before_issues:
        before_issues = doc.get("issues_before", doc.get("issues", [])) if isinstance(doc, dict) else []
    if not isinstance(before_issues, list):
        before_issues = []

    try:
        if doc_type == DocumentType.PDF.value:
            try:
                tag_tree = extract_tag_tree(PdfReader(str(final_dest), strict=False))
            except Exception as exc:
                tag_tree = _safe_tag_tree([f"tag tree: parse failed: {exc.__class__.__name__}"])
            tag_path = _write_tag_tree(doc_id, tag_tree)
            doc["tagTreePath"] = str(tag_path)
            doc["tagSummary"] = tag_tree.get("summary", {})
            after_issues = _analyze_pdf(doc_id, final_dest, tag_tree=tag_tree)
        elif doc_type == DocumentType.DOCX.value:
            after_issues = _analyze_docx(doc_id, final_dest)
        elif doc_type == DocumentType.PPTX.value:
            after_issues = _analyze_pptx(doc_id, final_dest)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported document type: {doc_type}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Finalize scan failed: {exc.__class__.__name__}")

    delta = _compute_delta(before_issues, after_issues)
    final_key = f"documents/{doc_id}/fixed/{final_dest.name}"
    try:
        STORAGE.save_file(key=final_key, src_path=str(final_dest), content_type=_content_type_for_name(final_dest.name))
        final_ref = encode_storage_key(final_key)
    except Exception as exc:
        if SETTINGS.storage_provider == "s3":
            raise HTTPException(status_code=500, detail=f"Failed to store finalized artifact: {exc}")
        final_ref = str(final_dest)

    manual_items_all = REPO.list_manual_review_items_for_doc(doc_id, include_resolved=True)
    pending_manual = 0
    approved_manual = 0
    rejected_manual = 0
    for item in manual_items_all:
        status = str(item.get("status") or "pending").strip().lower()
        if status == "approved":
            approved_manual += 1
        elif status == "rejected":
            rejected_manual += 1
        else:
            pending_manual += 1

    report = report if isinstance(report, dict) else {}
    report.update(
        {
            "docId": doc_id,
            "fixedDocId": doc_id,
            "fixedPath": final_ref,
            "localFixedPath": str(final_dest),
            "scanTargetPath": final_ref,
            "localScanTargetPath": str(final_dest),
            "before": _summarize_issues(before_issues),
            "after": _summarize_issues(after_issues),
            "delta": delta,
            "mode": "finalize",
            "finalized": True,
            "appliedManualReviewCount": len(applied_manual_item_ids),
            "appliedManualReviewItemIds": sorted(set(applied_manual_item_ids)),
            "manualReviewSummary": {
                "pending": pending_manual,
                "approved": approved_manual,
                "rejected": rejected_manual,
            },
        }
    )

    with LOCK:
        ISSUES[doc_id] = after_issues
    doc["issues_after"] = after_issues
    doc["fixReport"] = report
    doc["fixedPath"] = final_ref
    doc["localFixedPath"] = str(final_dest)
    doc["scanTargetPath"] = final_ref
    doc["localScanTargetPath"] = str(final_dest)
    _save_doc(doc_id, doc)

    REPO.save_issues(doc_id, "before", before_issues, [_issue_key(i) for i in before_issues])
    REPO.save_issues(doc_id, "after", after_issues, [_issue_key(i) for i in after_issues])
    REPO.save_fix_report(doc_id, report)

    latest_job = REPO.get_latest_job_for_doc(doc_id)
    job_id_for_scores: Optional[str] = None
    if latest_job and latest_job.get("jobId"):
        try:
            job_id_for_scores = str(latest_job.get("jobId"))
            _compute_and_store_job_score(job_id=job_id_for_scores, pass_type="post_manual", issues=after_issues, doc_type=doc_type)
        except Exception:
            pass

    report_path = RESULTS_DIR / doc_id / "fix_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {
        "docId": doc_id,
        "jobId": job_id_for_scores,
        "finalized": True,
        "finalizedPath": final_ref,
        "report": report,
        "counts": {
            "remaining": len(delta.get("remaining", [])) if isinstance(delta.get("remaining"), list) else 0,
            "introduced": len(delta.get("introduced", [])) if isinstance(delta.get("introduced"), list) else 0,
            "pendingManual": pending_manual,
            "approvedManual": approved_manual,
            "rejectedManual": rejected_manual,
        },
    }


@router.get("/documents/{doc_id}/download")
async def download_document(doc_id: str, variant: Optional[str] = "original"):
    doc = _get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if variant == "fixed":
        fixed_ref = doc.get("fixedPath") or (doc.get("fixReport", {}) if isinstance(doc.get("fixReport"), dict) else {}).get("fixedPath")
        if not fixed_ref:
            raise HTTPException(status_code=404, detail="Fixed document not found")
        return _download_response_from_ref(fixed_ref, filename_hint=str(doc.get("filename") or "fixed"))
    return _download_response_from_ref(doc.get("path"), filename_hint=str(doc.get("filename") or "document"))


@router.get("/documents/{doc_id}/pdf")
async def download_pdf(doc_id: str):
    doc = _get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if str(doc.get("docType", "pdf")) != DocumentType.PDF.value:
        raise HTTPException(status_code=400, detail="Document is not a PDF")
    return _download_response_from_ref(doc.get("path"), filename_hint=str(doc.get("filename") or "document.pdf"), media_type="application/pdf")


@router.head("/documents/{doc_id}/pdf")
async def head_pdf(doc_id: str) -> FileResponse:
    return await download_pdf(doc_id)


@router.get("/documents/{doc_id}/pdf-fixed")
async def download_pdf_fixed(doc_id: str):
    doc = _get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    fixed_ref = doc.get("fixedPath") or (doc.get("fixReport", {}) if isinstance(doc.get("fixReport"), dict) else {}).get("fixedPath")
    if not str(fixed_ref or ""):
        report = REPO.get_fix_report(doc_id)
        if report and report.get("fixedPath"):
            fixed_ref = report["fixedPath"]
    if not fixed_ref:
        raise HTTPException(status_code=404, detail="Fixed document not found")
    return _download_response_from_ref(fixed_ref, filename_hint=f"fixed-{doc_id}", media_type=None)


@router.head("/documents/{doc_id}/pdf-fixed")
async def head_pdf_fixed(doc_id: str) -> FileResponse:
    return await download_pdf_fixed(doc_id)


@router.get("/documents/{doc_id}/file-fixed")
async def download_fixed_file(doc_id: str) -> FileResponse:
    return await download_pdf_fixed(doc_id)


@router.get("/documents/{doc_id}/pdf-rebuilt")
async def download_pdf_rebuilt(doc_id: str):
    doc = _get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    rebuilt_ref = doc.get("rebuiltPath")
    if not rebuilt_ref:
        raise HTTPException(status_code=404, detail="Rebuilt document not found")
    return _download_response_from_ref(rebuilt_ref, filename_hint=f"rebuilt-{doc_id}.pdf", media_type="application/pdf")


@router.head("/documents/{doc_id}/pdf-rebuilt")
async def head_pdf_rebuilt(doc_id: str) -> FileResponse:
    return await download_pdf_rebuilt(doc_id)


@router.get("/documents/{doc_id}/summary")
async def document_summary(doc_id: str) -> Dict[str, object]:
    doc = _get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    source_ref = doc.get("localPath") or doc.get("path")
    src = _materialize_local_path(doc_id, source_ref, str(doc.get("filename") or "document.bin"))
    doc_type = str(doc.get("docType", "pdf"))
    if doc_type == DocumentType.PDF.value:
        reader = PdfReader(str(src), strict=False)
        metadata = reader.metadata
        title = metadata.title if metadata else None
        image_count = _extract_images(reader)
        try:
            tag_tree = extract_tag_tree(reader)
        except Exception as exc:
            tag_tree = _safe_tag_tree([f"tag tree: parse failed: {exc.__class__.__name__}"])
        summary = tag_tree.get("summary", {})
        outline_count = _outline_count(reader)
        form_counts = _form_field_counts(reader)
        return {
            "docId": doc_id,
            "title": title if title else "",
            "pages": len(reader.pages),
            "images": image_count,
            "tagged": bool(tag_tree.get("tagged")),
            "nodeCount": summary.get("nodeCount", 0),
            "tagCounts": summary.get("tagCounts", {}),
            "figures": summary.get("figures", 0),
            "figuresMissingAlt": summary.get("figuresMissingAlt", 0),
            "outlineCount": outline_count,
            "formFields": form_counts["total"],
            "unlabeledFields": form_counts["unlabeled"],
            "docType": doc_type,
        }
    if doc_type == DocumentType.DOCX.value:
        parsed = DOCXParser().parse(str(src))
        return {
            "docId": doc_id,
            "title": parsed.get("title", ""),
            "pages": 0,
            "images": parsed.get("imageCount", 0),
            "tagged": False,
            "nodeCount": 0,
            "tagCounts": {},
            "figures": parsed.get("imageCount", 0),
            "figuresMissingAlt": parsed.get("missingAltCount", 0),
            "outlineCount": parsed.get("outlineCount", 0),
            "formFields": 0,
            "unlabeledFields": 0,
            "docType": doc_type,
        }
    parsed = PPTXParser().parse(str(src))
    return {
        "docId": doc_id,
        "title": parsed.get("title", ""),
        "pages": parsed.get("slideCount", 0),
        "images": parsed.get("imageCount", 0),
        "tagged": False,
        "nodeCount": 0,
        "tagCounts": {},
        "figures": parsed.get("imageCount", 0),
        "figuresMissingAlt": parsed.get("missingAltCount", 0),
        "outlineCount": len(parsed.get("headings", [])) if isinstance(parsed.get("headings", []), list) else 0,
        "formFields": 0,
        "unlabeledFields": 0,
        "docType": doc_type,
    }


@router.get("/documents/{doc_id}/diff")
async def document_diff(doc_id: str) -> Dict[str, object]:
    doc = _get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    source_ref = doc.get("localPath") or doc.get("path")
    src = _materialize_local_path(doc_id, source_ref, str(doc.get("filename") or "document.bin"))
    fixed_ref = doc.get("fixedPath") or (doc.get("fixReport", {}) if isinstance(doc.get("fixReport"), dict) else {}).get("fixedPath")
    fixed_path = _materialize_local_path(doc_id, fixed_ref, f"fixed-{doc_id}.bin")
    doc_type = str(doc.get("docType", "pdf"))
    if not fixed_path.exists():
        raise HTTPException(status_code=404, detail="Fixed document not found")

    before_text = _extract_text_any(src, doc_type)
    after_text = _extract_text_any(fixed_path, doc_type)
    diff_text = _build_diff(before_text, after_text)

    if len(before_text) > MAX_DIFF_CHARS:
        before_text = before_text[:MAX_DIFF_CHARS] + "\n...truncated"
    if len(after_text) > MAX_DIFF_CHARS:
        after_text = after_text[:MAX_DIFF_CHARS] + "\n...truncated"
    if len(diff_text) > MAX_DIFF_CHARS:
        diff_text = diff_text[:MAX_DIFF_CHARS] + "\n...truncated"

    return {
        "beforeText": before_text,
        "afterText": after_text,
        "diffText": diff_text,
    }


@router.get("/documents/{doc_id}/tag-tree")
async def get_tag_tree(doc_id: str) -> Dict[str, object]:
    tag_path = RESULTS_DIR / doc_id / "tag_tree.json"
    if not tag_path.exists():
        return _safe_tag_tree(["tag tree: not available"])
    try:
        return json.loads(tag_path.read_text(encoding="utf-8"))
    except Exception:
        return _safe_tag_tree(["tag tree: failed to load cached results"])


@router.get("/documents/{doc_id}/fix-report")
async def get_fix_report(doc_id: str) -> Dict[str, object]:
    doc = _get_doc(doc_id) or {}
    report = doc.get("fixReport")
    if report:
        return report
    persisted = REPO.get_fix_report(doc_id)
    if persisted:
        return persisted
    report_path = RESULTS_DIR / doc_id / "fix_report.json"
    if report_path.exists():
        try:
            return json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    fixed_path = FIXED_DIR / doc_id / "fixed.pdf"
    if fixed_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Fix report not found (fixed.pdf exists but fix_report.json is missing).",
        )
    raise HTTPException(status_code=404, detail="Fix report not found")


@router.get("/documents/{doc_id}/debug-issues")
async def debug_issues(doc_id: str) -> Dict[str, object]:
    doc = _get_doc(doc_id) or {}
    before_issues = REPO.get_issues(doc_id, "before") or (doc.get("issues_before", doc.get("issues", [])) or [])
    after_issues = REPO.get_issues(doc_id, "after") or (doc.get("issues_after", []) or [])
    before_keys = [_issue_key(issue) for issue in before_issues][:5]
    after_keys = [_issue_key(issue) for issue in after_issues][:5]
    fixed_path = doc.get("fixReport", {}).get("fixedPath")
    rebuilt_path = doc.get("fixReport", {}).get("rebuiltPath")
    scan_target_path = doc.get("fixReport", {}).get("scanTargetPath")
    local_fixed_path: Optional[Path] = None
    local_rebuilt_path: Optional[Path] = None
    local_scan_target_path: Optional[Path] = None
    try:
        if fixed_path:
            local_fixed_path = _materialize_local_path(doc_id, fixed_path, f"fixed-{doc_id}.bin")
    except Exception:
        local_fixed_path = None
    try:
        if rebuilt_path:
            local_rebuilt_path = _materialize_local_path(doc_id, rebuilt_path, f"rebuilt-{doc_id}.pdf")
    except Exception:
        local_rebuilt_path = None
    try:
        if scan_target_path:
            local_scan_target_path = _materialize_local_path(doc_id, scan_target_path, f"scan-target-{doc_id}.bin")
    except Exception:
        local_scan_target_path = None
    fixed_size = 0
    if local_fixed_path:
        try:
            fixed_size = local_fixed_path.stat().st_size
        except Exception:
            fixed_size = 0
    rebuilt_size = 0
    if local_rebuilt_path:
        try:
            rebuilt_size = local_rebuilt_path.stat().st_size
        except Exception:
            rebuilt_size = 0
    fixed_exists = doc.get("fixReport", {}).get("fixedExists")
    rebuilt_exists = doc.get("fixReport", {}).get("rebuiltExists")
    scan_after_error = doc.get("fixReport", {}).get("scanAfterError")
    mcid_counts: List[int] = []
    text_mcid_counts: List[int] = []
    struct_root_exists = False
    parent_tree_ok = False
    struct_counts: Dict[str, int] = {}
    mcid_coverage: List[Dict[str, object]] = []
    figure_counts_after: Dict[str, int] = {"figures": 0, "figuresMissingAlt": 0, "figuresWithAlt": 0}
    if local_rebuilt_path and local_rebuilt_path.exists():
        try:
            rebuilt_reader = PdfReader(str(local_rebuilt_path), strict=False)
            root = rebuilt_reader.trailer.get("/Root", {})
            struct_root_exists = "/StructTreeRoot" in root
            if struct_root_exists:
                try:
                    summary = extract_tag_tree(rebuilt_reader).get("summary", {})
                    struct_counts = summary.get("tagCounts", {})
                except Exception:
                    struct_counts = {}
            parent_tree = None
            try:
                struct_root = root.get("/StructTreeRoot")
                if struct_root:
                    parent_tree = struct_root.get("/ParentTree")
            except Exception:
                parent_tree = None
            for page in rebuilt_reader.pages:
                count = 0
                text_count = 0
                try:
                    content_stream = ContentStream(page.get("/Contents"), rebuilt_reader)
                    for operands, operator in content_stream.operations:
                        if operator == b"BDC" and len(operands) >= 2:
                            props = operands[1]
                            try:
                                mcid = props.get("/MCID")
                                if mcid is not None:
                                    count += 1
                                    if operands[0] == "/Span" or operands[0] == NameObject("/Span"):
                                        text_count += 1
                            except Exception:
                                continue
                except Exception:
                    count = 0
                    text_count = 0
                mcid_counts.append(count)
                text_mcid_counts.append(text_count)
            if parent_tree and isinstance(parent_tree, dict):
                nums = parent_tree.get("/Nums")
                if isinstance(nums, list) and len(nums) >= 2:
                    parent_tree_ok = True
                    for idx in range(0, len(nums), 2):
                        page_index = nums[idx]
                        arr = nums[idx + 1] if idx + 1 < len(nums) else None
                        if isinstance(page_index, int) and isinstance(arr, list):
                            max_mcid = len(arr) - 1
                            mapped = sum(1 for entry in arr if entry is not None)
                            total = mcid_counts[page_index] if page_index < len(mcid_counts) else 0
                            mcid_coverage.append(
                                {
                                    "page": page_index + 1,
                                    "maxMcid": max_mcid,
                                    "mappedMcids": mapped,
                                    "totalMcids": total,
                                }
                            )
        except Exception:
            mcid_counts = []
            text_mcid_counts = []
    target_for_alt = None
    if local_scan_target_path and local_scan_target_path.exists() and local_scan_target_path.suffix.lower() == ".pdf":
        target_for_alt = local_scan_target_path
    elif local_fixed_path and local_fixed_path.exists() and local_fixed_path.suffix.lower() == ".pdf":
        target_for_alt = local_fixed_path
    elif local_rebuilt_path and local_rebuilt_path.exists() and local_rebuilt_path.suffix.lower() == ".pdf":
        target_for_alt = local_rebuilt_path
    if target_for_alt:
        try:
            summary_after = extract_tag_tree(PdfReader(str(target_for_alt), strict=False)).get("summary", {})
            figures = int(summary_after.get("figures", 0) or 0)
            missing = int(summary_after.get("figuresMissingAlt", 0) or 0)
            figure_counts_after = {
                "figures": figures,
                "figuresMissingAlt": missing,
                "figuresWithAlt": max(0, figures - missing),
            }
        except Exception:
            figure_counts_after = {"figures": 0, "figuresMissingAlt": 0, "figuresWithAlt": 0}
    return {
        "before_count": len(before_issues),
        "after_count": len(after_issues),
        "before_sample_keys": before_keys,
        "after_sample_keys": after_keys,
        "fixedPath": fixed_path,
        "rebuiltPath": rebuilt_path,
        "scanTargetPath": scan_target_path,
        "fixedExists": fixed_exists,
        "rebuiltExists": rebuilt_exists,
        "fixedSize": fixed_size,
        "rebuiltSize": rebuilt_size,
        "scanAfterError": scan_after_error,
        "rebuiltStructRoot": struct_root_exists,
        "mcidCounts": mcid_counts,
        "textMcidCounts": text_mcid_counts,
        "parentTreeOk": parent_tree_ok,
        "structCounts": struct_counts,
        "mcidCoverage": mcid_coverage,
        "figureCountsAfter": figure_counts_after,
        "appliedManualReviewCount": int(doc.get("fixReport", {}).get("appliedManualReviewCount", 0) or 0),
        "appliedManualReviewItemIds": doc.get("fixReport", {}).get("appliedManualReviewItemIds", []),
        "anchorCountsByRuleId": _anchor_counts_by_rule(after_issues),
        "sampleAnchorsByRuleId": _sample_anchors_by_rule(after_issues),
    }
