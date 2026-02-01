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
import difflib

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parents[2]
RUNTIME_DIR = BASE_DIR / ".runtime"
UPLOADS_DIR = RUNTIME_DIR / "uploads"
FIXED_DIR = RUNTIME_DIR / "fixed"

JOBS: Dict[str, Dict[str, object]] = {}
DOCS: Dict[str, Dict[str, object]] = {}
ISSUES: Dict[str, List[Dict[str, object]]] = {}
LOCK = threading.Lock()
MAX_DIFF_CHARS = 20000


def _ensure_dirs() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    FIXED_DIR.mkdir(parents=True, exist_ok=True)


def _job_update(job_id: str, **updates: object) -> None:
    with LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(updates)


def _extract_images(reader: PdfReader) -> int:
    count = 0
    for page in reader.pages:
        resources = page.get("/Resources")
        if not resources:
            continue
        xobjects = resources.get("/XObject")
        if not xobjects:
            continue
        for obj in xobjects.values():
            try:
                xobj = obj.get_object()
            except Exception:
                continue
            if xobj.get("/Subtype") == "/Image":
                count += 1
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
    reader = PdfReader(str(src))
    writer = PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    applied: List[str] = []

    metadata = reader.metadata or {}
    title = metadata.title if metadata else None
    if not title or not str(title).strip():
        writer.add_metadata({"/Title": "Untitled Document"})
        applied.append("set_document_title")

    if not _has_outline(reader):
        try:
            writer.add_outline_item("Document", 0)
            applied.append("add_document_outline")
        except Exception:
            pass

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
                if not name or not str(name).strip():
                    field_obj.update({"/T": f"Field {idx}"})
                    applied.append("label_form_field")
            writer._root_object.update({"/AcroForm": acro})
    except Exception:
        pass

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as output:
        writer.write(output)

    return {"applied": applied}


def _extract_text(path: Path) -> str:
    reader = PdfReader(str(path))
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


def _analyze_pdf(doc_id: str, path: Path) -> List[Dict[str, object]]:
    issues: List[Dict[str, object]] = []
    reader = PdfReader(str(path))
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
            }
        )

    image_count = _extract_images(reader)
    if image_count > 0 and not _is_tagged(reader):
        issues.append(
            {
                "id": f"{doc_id}-issue-alt",
                "ruleId": "missing_alt_text",
                "title": "Images missing alternative text",
                "severity": "error",
                "description": "PDF contains images but no tagging structure for alternative text.",
                "locationHint": f"{image_count} image(s) detected",
                "recommendation": "Add PDF/UA tags with alt text for meaningful images.",
            }
        )

    if not _has_outline(reader):
        issues.append(
            {
                "id": f"{doc_id}-issue-headings",
                "ruleId": "missing_heading_structure",
                "title": "Missing heading structure",
                "severity": "warning",
                "description": "PDF does not include bookmarks/outline entries.",
                "locationHint": "Document outline",
                "recommendation": "Add heading structure and bookmarks for navigation.",
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
            }
        )

    return issues


def _scan_worker(job_id: str, doc_id: str) -> None:
    _job_update(job_id, status="running", progress=0, message="Scanning document")
    with LOCK:
        doc = DOCS.get(doc_id)
    if not doc:
        _job_update(job_id, status="error", progress=0, message="Document not found")
        return
    doc_path = Path(str(doc["path"]))
    try:
        issues = _analyze_pdf(doc_id, doc_path)
    except Exception as exc:
        _job_update(job_id, status="error", progress=0, message=f"Scan failed: {exc.__class__.__name__}")
        return
    steps = 38
    for i in range(steps):
        time.sleep(1)
        progress = int(((i + 1) / steps) * 100)
        _job_update(job_id, progress=progress)
        if progress in (25, 50, 75, 100):
            slice_count = 1 if progress == 25 else 2 if progress == 50 else 3 if progress == 75 else 4
            with LOCK:
                ISSUES[doc_id] = issues[:slice_count]
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
async def apply_fixes(doc_id: str) -> dict:
    _ensure_dirs()
    with LOCK:
        doc = DOCS.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    src = Path(str(doc["path"]))
    fixed_dir = FIXED_DIR / doc_id
    fixed_dir.mkdir(parents=True, exist_ok=True)
    dest = fixed_dir / src.name
    _apply_pdf_fixes(doc_id, src, dest)
    try:
        fixed_issues = _analyze_pdf(doc_id, dest)
        with LOCK:
            ISSUES[doc_id] = fixed_issues
    except Exception:
        pass
    return {"docId": doc_id, "fixed": True}


@router.get("/documents/{doc_id}/download")
async def download_document(doc_id: str, variant: Optional[str] = "original") -> FileResponse:
    with LOCK:
        doc = DOCS.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    src = Path(str(doc["path"]))
    if variant == "fixed":
        fixed_path = FIXED_DIR / doc_id / src.name
        if not fixed_path.exists():
            raise HTTPException(status_code=404, detail="Fixed document not found")
        return FileResponse(str(fixed_path), filename=fixed_path.name)
    return FileResponse(str(src), filename=src.name)


@router.get("/documents/{doc_id}/summary")
async def document_summary(doc_id: str) -> Dict[str, object]:
    with LOCK:
        doc = DOCS.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    src = Path(str(doc["path"]))
    reader = PdfReader(str(src))
    metadata = reader.metadata
    title = metadata.title if metadata else None
    image_count = _extract_images(reader)
    outline_count = _outline_count(reader)
    form_counts = _form_field_counts(reader)
    return {
        "docId": doc_id,
        "title": title if title else "",
        "pages": len(reader.pages),
        "images": image_count,
        "tagged": _is_tagged(reader),
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
    fixed_path = FIXED_DIR / doc_id / src.name
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
