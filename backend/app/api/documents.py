"""Document upload and mock scan workflow routes."""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, DictionaryObject, NameObject, TextStringObject
import difflib
import json

from app.pdf.tag_tree import extract_tag_tree

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


def _ensure_dirs() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    FIXED_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _job_update(job_id: str, **updates: object) -> None:
    with LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(updates)


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
            added = _add_placeholder_alt(writer)
            if added > 0:
                applied.append(
                    {
                        "fixId": f"{doc_id}-fix-alt",
                        "ruleId": "missing_alt_text",
                        "severity": "error",
                        "action": "Added placeholder /Alt text for Figure tags.",
                        "pages": [],
                        "anchors": [],
                        "deterministic": True,
                    }
                )
                manual_review_added.append(
                    _queue_manual_review(
                        doc_id,
                        "missing_alt_text",
                        "doc-1",
                        "Missing alt text",
                        "Placeholder alt text was added; update with meaningful descriptions.",
                        pages=[],
                        anchors=[],
                        suggested_fix="Replace placeholder alt text with meaningful descriptions.",
                        confidence=0.6,
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


def _add_placeholder_alt(writer: PdfWriter) -> int:
    try:
        root = writer._root_object
        struct_root = root.get("/StructTreeRoot")
        if not struct_root:
            return 0
        count = 0
        stack = [struct_root]
        while stack:
            node = stack.pop()
            try:
                obj = node.get_object()
            except Exception:
                obj = node
            if isinstance(obj, dict):
                tag = obj.get("/S")
                if tag == "/Figure":
                    alt = obj.get("/Alt")
                    if not alt or not str(alt).strip():
                        obj.update({NameObject("/Alt"): TextStringObject("[TODO] Add alt text")})
                        count += 1
                kids = obj.get("/K")
                if kids:
                    if isinstance(kids, list):
                        stack.extend(kids)
                    else:
                        stack.append(kids)
        return count
    except Exception:
        return 0


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
    return item


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
    if tagged and tree_nodes:
        for node_id, node in tree_nodes.items():
            if node.get("tag") == "Figure" and not node.get("alt"):
                missing_alt_nodes.append(node_id)
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

    return issues


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
    for node_id in order:
        node = nodes.get(node_id)
        if not node:
            continue
        tag = node.get("tag")
        if tag and tag.startswith("H") and len(tag) == 2 and tag[1].isdigit():
            level = int(tag[1])
            if prev_level is not None and level > prev_level + 1:
                page_value = nodes.get(node_id, {}).get("page")
                pages = [page_value] if isinstance(page_value, int) else []
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
                        "pages": pages,
                        "page": page_value if isinstance(page_value, int) else None,
                    },
                }
            prev_level = level
            prev_tag = tag
    return None


def _scan_worker(job_id: str, doc_id: str) -> None:
    _job_update(job_id, status="running", progress=0, message="Scanning document")
    with LOCK:
        doc = DOCS.get(doc_id)
    if not doc:
        _job_update(job_id, status="error", progress=0, message="Document not found")
        return
    doc_path = Path(str(doc["path"]))
    try:
        _job_update(job_id, progress=5, message="Loading PDF")

        def update_page_progress(current: int, total: int) -> None:
            if total <= 0:
                return
            progress = 5 + int((current / total) * 85)
            _job_update(job_id, progress=progress, message=f"Scanning page {current} of {total}")

        try:
            tag_tree = extract_tag_tree(PdfReader(str(doc_path), strict=False))
        except Exception as exc:
            tag_tree = _safe_tag_tree([f"tag tree: parse failed: {exc.__class__.__name__}"])
        tag_path = _write_tag_tree(doc_id, tag_tree)
        with LOCK:
            DOCS[doc_id]["tagTreePath"] = str(tag_path)
            DOCS[doc_id]["tagSummary"] = tag_tree.get("summary", {})
        issues = _analyze_pdf(doc_id, doc_path, on_page_progress=update_page_progress, tag_tree=tag_tree)
        _job_update(job_id, progress=95, message="Finalizing issues")
    except Exception as exc:
        _job_update(job_id, status="error", progress=0, message=f"Scan failed: {exc.__class__.__name__}")
        return
    if issues:
        with LOCK:
            ISSUES[doc_id] = issues
    with LOCK:
        ISSUES[doc_id] = issues
        JOBS[job_id].update(status="done", progress=100, message="Scan complete")


@router.post("/documents/upload")
async def upload_document(file: UploadFile = File(...)) -> dict:
    _ensure_dirs()
    doc_id = f"doc-{int(time.time())}"
    doc_dir = UPLOADS_DIR / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    dest = doc_dir / file.filename
    with dest.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    size = dest.stat().st_size
    with LOCK:
        DOCS[doc_id] = {
            "filename": file.filename,
            "path": str(dest),
        }
    return {"docId": doc_id, "filename": file.filename, "sizeBytes": size}


@router.post("/documents/{doc_id}/scan")
async def start_scan(doc_id: str) -> dict:
    with LOCK:
        if doc_id not in DOCS:
            raise HTTPException(status_code=404, detail="Document not found")
    job_id = f"job-{int(time.time() * 1000)}"
    with LOCK:
        JOBS[job_id] = {"jobId": job_id, "status": "queued", "progress": 0}
    thread = threading.Thread(target=_scan_worker, args=(job_id, doc_id), daemon=True)
    thread.start()
    return {"jobId": job_id}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    with LOCK:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/documents/{doc_id}/issues")
async def get_issues(doc_id: str) -> List[Dict[str, object]]:
    with LOCK:
        issues = ISSUES.get(doc_id, [])
    return issues


@router.post("/documents/{doc_id}/apply-fixes")
async def apply_fixes(doc_id: str, mode: Optional[str] = "in_place") -> dict:
    _ensure_dirs()
    with LOCK:
        doc = DOCS.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    src = Path(str(doc["path"]))
    fixed_dir = FIXED_DIR / doc_id
    fixed_dir.mkdir(parents=True, exist_ok=True)
    fixed_dest = fixed_dir / "fixed.pdf"
    rebuild_dest = fixed_dir / "rebuilt.pdf"
    fix_result = _apply_pdf_fixes(doc_id, src, fixed_dest)
    rebuilt = False
    if mode == "rebuild":
        rebuilt = False
    try:
        try:
            tag_tree = extract_tag_tree(PdfReader(str(fixed_dest), strict=False))
        except Exception as exc:
            tag_tree = _safe_tag_tree([f"tag tree: parse failed: {exc.__class__.__name__}"])
        tag_path = _write_tag_tree(doc_id, tag_tree)
        with LOCK:
            DOCS[doc_id]["tagTreePath"] = str(tag_path)
            DOCS[doc_id]["tagSummary"] = tag_tree.get("summary", {})
        fixed_issues = _analyze_pdf(doc_id, fixed_dest, tag_tree=tag_tree)
        with LOCK:
            ISSUES[doc_id] = fixed_issues
    except Exception:
        pass
    report = {
        "applied_fixes": fix_result.get("applied", []),
        "remaining_issues": ISSUES.get(doc_id, []),
        "manual_review_added": fix_result.get("manual_review_added", []),
        "before_after": {
            "metadata_before": fix_result.get("metadata_before", {}),
            "metadata_after": fix_result.get("metadata_after", {}),
        },
        "deterministic": True,
        "mode": mode,
        "rebuilt": rebuilt,
    }
    report_path = RESULTS_DIR / doc_id / "fix_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {"docId": doc_id, "fixed": True, "report": report}


@router.get("/documents/{doc_id}/download")
async def download_document(doc_id: str, variant: Optional[str] = "original") -> FileResponse:
    with LOCK:
        doc = DOCS.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    src = Path(str(doc["path"]))
    if variant == "fixed":
        fixed_path = FIXED_DIR / doc_id / "fixed.pdf"
        if not fixed_path.exists():
            raise HTTPException(status_code=404, detail="Fixed document not found")
        return FileResponse(str(fixed_path), filename=fixed_path.name)
    return FileResponse(str(src), filename=src.name)


@router.get("/documents/{doc_id}/pdf")
async def download_pdf(doc_id: str) -> FileResponse:
    with LOCK:
        doc = DOCS.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    src = Path(str(doc["path"]))
    if not src.exists():
        raise HTTPException(status_code=404, detail="Document file not found")
    return FileResponse(str(src), filename=src.name, media_type="application/pdf")


@router.head("/documents/{doc_id}/pdf")
async def head_pdf(doc_id: str) -> FileResponse:
    return await download_pdf(doc_id)


@router.get("/documents/{doc_id}/pdf-fixed")
async def download_pdf_fixed(doc_id: str) -> FileResponse:
    with LOCK:
        doc = DOCS.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    src = Path(str(doc["path"]))
    fixed_path = FIXED_DIR / doc_id / "fixed.pdf"
    if not fixed_path.exists():
        raise HTTPException(status_code=404, detail="Fixed document not found")
    return FileResponse(str(fixed_path), filename=fixed_path.name, media_type="application/pdf")


@router.head("/documents/{doc_id}/pdf-fixed")
async def head_pdf_fixed(doc_id: str) -> FileResponse:
    return await download_pdf_fixed(doc_id)


@router.get("/documents/{doc_id}/summary")
async def document_summary(doc_id: str) -> Dict[str, object]:
    with LOCK:
        doc = DOCS.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    src = Path(str(doc["path"]))
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
    }


@router.get("/documents/{doc_id}/diff")
async def document_diff(doc_id: str) -> Dict[str, object]:
    with LOCK:
        doc = DOCS.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    src = Path(str(doc["path"]))
    fixed_path = FIXED_DIR / doc_id / "fixed.pdf"
    if not fixed_path.exists():
        raise HTTPException(status_code=404, detail="Fixed document not found")

    before_text = _extract_text(src)
    after_text = _extract_text(fixed_path)
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
