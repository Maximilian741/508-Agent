"""Document upload and mock scan workflow routes."""

from __future__ import annotations

import shutil
import threading
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
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
from app.persistence.db import get_repo

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


class DocumentType(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"


def _ensure_dirs() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    FIXED_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


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
                "docType": persisted.get("docType", "pdf"),
                "tagTreePath": persisted.get("tagTreePath"),
                "tagSummary": persisted.get("tagSummary"),
                "fixReport": persisted.get("fixReport"),
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
        "scanTargetPath": doc.get("scanTargetPath"),
        "tagTreePath": doc.get("tagTreePath"),
        "tagSummary": doc.get("tagSummary"),
        "fixReport": doc.get("fixReport"),
    }
    if doc.get("fixedPath"):
        payload["fixedPath"] = doc.get("fixedPath")
    if doc.get("rebuiltPath"):
        payload["rebuiltPath"] = doc.get("rebuiltPath")
    REPO.save_document(payload)


def _job_update(job_id: str, **updates: object) -> None:
    with LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(updates)
            REPO.update_job(job_id, updates)


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
                        if tag == "Figure":
                            elem_dict[NameObject("/Alt")] = TextStringObject("[TODO] Add alt text")
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
        item["appliedAt"] = datetime.utcnow().isoformat() + "Z"
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
    heading_jumps = parsed.get("headingJumps", []) if isinstance(parsed.get("headingJumps", []), list) else []
    image_count = int(parsed.get("imageCount", 0) or 0)
    missing_alt = int(parsed.get("missingAltCount", 0) or 0)

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
    slide_details = parsed.get("slides", []) if isinstance(parsed.get("slides", []), list) else []
    reading_order_warnings = parsed.get("readingOrderWarnings", []) if isinstance(parsed.get("readingOrderWarnings", []), list) else []
    image_count = int(parsed.get("imageCount", 0) or 0)
    missing_alt = int(parsed.get("missingAltCount", 0) or 0)

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
    doc_path = Path(str(doc["path"]))
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
    REPO.update_job(job_id, {"status": "done", "progress": 100, "message": "Scan complete"})


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
    doc_type = _infer_doc_type(file.filename or "").value
    with LOCK:
        DOCS[doc_id] = {
            "filename": file.filename,
            "path": str(dest),
            "docType": doc_type,
        }
    _save_doc(doc_id, DOCS[doc_id])
    return {"docId": doc_id, "filename": file.filename, "sizeBytes": size, "docType": doc_type}


@router.get("/documents")
async def list_documents() -> List[Dict[str, object]]:
    return REPO.list_documents()


@router.post("/documents/{doc_id}/scan")
async def start_scan(doc_id: str) -> dict:
    if _get_doc(doc_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    job_id = f"job-{int(time.time() * 1000)}"
    with LOCK:
        JOBS[job_id] = {"jobId": job_id, "status": "queued", "progress": 0}
    REPO.save_job({"jobId": job_id, "docId": doc_id, "status": "queued", "progress": 0, "message": "Queued"})
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
    return job


@router.get("/documents/{doc_id}/issues")
async def get_issues(doc_id: str) -> List[Dict[str, object]]:
    with LOCK:
        issues = ISSUES.get(doc_id, [])
    if not issues:
        issues = REPO.get_latest_issues(doc_id)
    return issues


@router.post("/documents/{doc_id}/apply-fixes")
async def apply_fixes(doc_id: str, mode: Optional[str] = "patch") -> dict:
    _ensure_dirs()
    doc = _get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    src = Path(str(doc["path"]))
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
    print(f"[apply_fixes] fixed_path={fixed_dest} size={fixed_size}")
    print(f"[apply_fixes] before={len(before_issues)} after={len(after_issues)}")
    fixed_doc_id = doc_id
    rebuilt_doc_id = doc_id if rebuilt else None
    ai_suggestions = build_alt_text_suggestions(doc_id, doc_type, src, before_issues)
    manual_review_items = fix_result.get("manual_review_added", []) + rebuild_result.get("manual_review_added", [])
    manual_review_items.extend(ai_suggestions)

    report = {
        "docId": doc_id,
        "fixedDocId": fixed_doc_id,
        "fixedPath": str(fixed_dest),
        "rebuiltDocId": rebuilt_doc_id,
        "rebuiltPath": str(rebuild_dest) if rebuilt else None,
        "scanTargetPath": str(scan_target),
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
    doc["fixedPath"] = str(fixed_dest)
    doc["scanTargetPath"] = str(scan_target)
    if rebuilt:
        doc["rebuiltPath"] = str(rebuild_dest)
    _save_doc(doc_id, doc)
    REPO.save_issues(doc_id, "before", before_issues, [_issue_key(i) for i in before_issues])
    REPO.save_issues(doc_id, "after", after_issues, [_issue_key(i) for i in after_issues])
    REPO.save_fix_report(doc_id, report)
    REPO.add_manual_review_items(doc_id, report.get("manualReview", []) if isinstance(report.get("manualReview"), list) else [])
    report_path = RESULTS_DIR / doc_id / "fix_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {"docId": doc_id, "fixed": True, "report": report}


@router.get("/documents/{doc_id}/download")
async def download_document(doc_id: str, variant: Optional[str] = "original") -> FileResponse:
    doc = _get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    src = Path(str(doc["path"]))
    if variant == "fixed":
        fixed_path = Path(str(doc.get("fixedPath") or (doc.get("fixReport", {}) if isinstance(doc.get("fixReport"), dict) else {}).get("fixedPath") or ""))
        if not fixed_path.exists():
            raise HTTPException(status_code=404, detail="Fixed document not found")
        return FileResponse(str(fixed_path), filename=fixed_path.name)
    return FileResponse(str(src), filename=src.name)


@router.get("/documents/{doc_id}/pdf")
async def download_pdf(doc_id: str) -> FileResponse:
    doc = _get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if str(doc.get("docType", "pdf")) != DocumentType.PDF.value:
        raise HTTPException(status_code=400, detail="Document is not a PDF")
    src = Path(str(doc["path"]))
    if not src.exists():
        raise HTTPException(status_code=404, detail="Document file not found")
    return FileResponse(str(src), filename=src.name, media_type="application/pdf")


@router.head("/documents/{doc_id}/pdf")
async def head_pdf(doc_id: str) -> FileResponse:
    return await download_pdf(doc_id)


@router.get("/documents/{doc_id}/pdf-fixed")
async def download_pdf_fixed(doc_id: str) -> FileResponse:
    doc = _get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    fixed_path = Path(str(doc.get("fixedPath") or (doc.get("fixReport", {}) if isinstance(doc.get("fixReport"), dict) else {}).get("fixedPath") or ""))
    if not str(fixed_path):
        report = REPO.get_fix_report(doc_id)
        if report and report.get("fixedPath"):
            fixed_path = Path(str(report["fixedPath"]))
    if not fixed_path.exists():
        raise HTTPException(status_code=404, detail="Fixed document not found")
    media_type = "application/pdf" if fixed_path.suffix.lower() == ".pdf" else None
    return FileResponse(str(fixed_path), filename=fixed_path.name, media_type=media_type)


@router.head("/documents/{doc_id}/pdf-fixed")
async def head_pdf_fixed(doc_id: str) -> FileResponse:
    return await download_pdf_fixed(doc_id)


@router.get("/documents/{doc_id}/file-fixed")
async def download_fixed_file(doc_id: str) -> FileResponse:
    return await download_pdf_fixed(doc_id)


@router.get("/documents/{doc_id}/pdf-rebuilt")
async def download_pdf_rebuilt(doc_id: str) -> FileResponse:
    doc = _get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    rebuilt_path = Path(str(doc.get("rebuiltPath") or ""))
    if not rebuilt_path.exists():
        raise HTTPException(status_code=404, detail="Rebuilt document not found")
    return FileResponse(str(rebuilt_path), filename=rebuilt_path.name, media_type="application/pdf")


@router.head("/documents/{doc_id}/pdf-rebuilt")
async def head_pdf_rebuilt(doc_id: str) -> FileResponse:
    return await download_pdf_rebuilt(doc_id)


@router.get("/documents/{doc_id}/summary")
async def document_summary(doc_id: str) -> Dict[str, object]:
    doc = _get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    src = Path(str(doc["path"]))
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
    src = Path(str(doc["path"]))
    fixed_path = Path(str(doc.get("fixedPath") or (doc.get("fixReport", {}) if isinstance(doc.get("fixReport"), dict) else {}).get("fixedPath") or ""))
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
    fixed_size = 0
    if fixed_path:
        try:
            fixed_size = Path(str(fixed_path)).stat().st_size
        except Exception:
            fixed_size = 0
    rebuilt_size = 0
    if rebuilt_path:
        try:
            rebuilt_size = Path(str(rebuilt_path)).stat().st_size
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
    if rebuilt_path and Path(str(rebuilt_path)).exists():
        try:
            rebuilt_reader = PdfReader(str(rebuilt_path), strict=False)
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
    if scan_target_path and Path(str(scan_target_path)).exists() and str(scan_target_path).lower().endswith(".pdf"):
        target_for_alt = Path(str(scan_target_path))
    elif fixed_path and Path(str(fixed_path)).exists() and str(fixed_path).lower().endswith(".pdf"):
        target_for_alt = Path(str(fixed_path))
    elif rebuilt_path and Path(str(rebuilt_path)).exists() and str(rebuilt_path).lower().endswith(".pdf"):
        target_for_alt = Path(str(rebuilt_path))
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
