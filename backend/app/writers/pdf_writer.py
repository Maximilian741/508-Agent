"""PDF writer — best-effort write-back of tree mutations.

PDF accessibility remediation through a tagged-PDF structure tree is a
substantial project on its own.  This writer takes a pragmatic v1 approach:

* Update document metadata (Title, Lang) via pypdf's Catalog manipulation.
* Update image XObject /Alt entries when an alt-text node mutation can be
  matched by the parser-emitted ``image_xobject`` name.
* Skip everything else — heading reflow, table headers, list structure, link
  rewrites — because they require touching the structure tree and the content
  stream, which pypdf does not do well.  Skipped items are returned to the
  caller so the UI can flag them as "needs source-app remediation".

If the source PDF lacks a Catalog or pypdf can't open it, the writer copies
the file unchanged and returns ``failed_to_open_pdf`` so the caller knows
nothing was applied. (The legacy /documents/apply-fixes pipeline this once
deferred to has been retired.)
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    IndirectObject,
    NameObject,
    TextStringObject,
)

from app.models.accessibility import (
    AccessibilityTree,
    DocumentNode,
    ImageNode,
    iter_reading_order,
)
from app.parsers.pdf_parser import derive_pdf_field_label, iter_acroform_fields
from app.pdf.ua_tagger import tag_pdf

logger = logging.getLogger(__name__)


def write_remediated_pdf(
    source_path: Path,
    tree: AccessibilityTree,
    output_path: Path,
) -> Dict[str, Any]:
    """Apply a subset of tree mutations to a copy of source_path.

    The PDF writer is intentionally conservative: only metadata and image
    /Alt entries are updated.  Everything else is recorded in ``skipped`` so
    the caller can report it as not applied.
    """

    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    pdfua_summary: Dict[str, Any] = {}

    # Always start by copying source → output so we never mutate the source.
    try:
        shutil.copyfile(source_path, output_path)
    except Exception as exc:  # pragma: no cover - filesystem errors
        return {"applied": [], "skipped": [{"target_id": str(source_path), "reason": f"copy_failed: {exc}"}]}

    # Open + clone inside one guard: PdfWriter(clone_from=...) raises on
    # encrypted / malformed PDFs, and the documented contract is to fall back
    # to the unchanged copy rather than crash the request.
    try:
        reader = PdfReader(str(output_path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")  # try the empty/owner password
            except Exception:
                pass
        writer = PdfWriter(clone_from=reader)
    except Exception as exc:
        skipped.append({"target_id": str(source_path), "reason": f"failed_to_open_pdf: {exc}"})
        return {"applied": applied, "skipped": skipped}

    # ------- 1. Document metadata -------------------------------------
    title = None
    if isinstance(tree.root, DocumentNode):
        properties = tree.root.metadata.properties or {}
        title = properties.get("title")
        language = tree.root.metadata.language

        try:
            existing_metadata = dict(reader.metadata or {})
        except Exception:
            existing_metadata = {}
        if title:
            existing_metadata["/Title"] = str(title)
            applied.append({"kind": "title", "target_id": tree.root.id, "summary": f"Title -> {title!r}"})
        try:
            writer.add_metadata(existing_metadata)
        except Exception as exc:
            skipped.append({"target_id": "document", "reason": f"failed_to_write_metadata: {exc}"})

        # /Lang lives on the Catalog, not /Info.
        if language:
            try:
                catalog = writer._root_object  # noqa: SLF001 — pypdf intentionally exposes
                catalog[NameObject("/Lang")] = TextStringObject(str(language))
                applied.append(
                    {"kind": "language", "target_id": tree.root.id, "summary": f"/Lang -> {language!r}"}
                )
            except Exception as exc:
                skipped.append({"target_id": "document", "reason": f"failed_to_write_lang: {exc}"})

        # ------- 1b. AcroForm field labels (/TU from a descriptive /T) -----
        # When the executor approved it, give each unlabeled field with a
        # confident /T-derived name a /TU (the accessible name AT announces).
        # The parser counted these with the SAME deriver, so we never write more
        # than the form_fields_derivable count the executor reported.
        if properties.get("apply_form_field_labels"):
            try:
                _apply_pdf_form_labels(writer, tree.root.id, applied, skipped)
            except Exception as exc:  # pragma: no cover - defensive
                skipped.append({"target_id": "document", "reason": f"form_labels_failed: {exc}"})

    # ------- 2. Image alt text on /XObject entries --------------------
    # Keyed by (page, XObject name), NOT by name alone. XObject names are
    # per-page resource keys, and producers reuse them: a scanned PDF names
    # every page's image /Im0. Keyed by name, the dict kept whichever node
    # came LAST, and every page's /Im0 received the last page's alt text — a
    # user's approved description of page 1 written onto page 40's picture,
    # and reported as applied. The ImageNode records its 1-based page.
    images_by_page_and_name: Dict[tuple, ImageNode] = {}
    for node in iter_reading_order(tree.root):
        if isinstance(node, ImageNode):
            xname = (node.metadata.properties or {}).get("xobject")
            pg = getattr(node.metadata, "page", None)
            if isinstance(xname, str) and xname and isinstance(pg, int):
                images_by_page_and_name[(pg, xname)] = node

    if images_by_page_and_name:
        for page_index, page in enumerate(writer.pages, start=1):
            try:
                resources = _resolve(page.get("/Resources"))
            except Exception:
                resources = None
            if not isinstance(resources, DictionaryObject):
                continue
            xobjects = _resolve(resources.get("/XObject")) if "/XObject" in resources else None
            if not isinstance(xobjects, DictionaryObject):
                continue
            for name, ref in xobjects.items():
                obj = _resolve(ref)
                if not isinstance(obj, DictionaryObject):
                    continue
                if obj.get("/Subtype") != "/Image":
                    continue
                node = images_by_page_and_name.get((page_index, str(name)))
                if node is None:
                    continue
                if node.is_decorative:
                    # Mark decorative: empty /Alt and add /Artifact marker.
                    obj[NameObject("/Alt")] = TextStringObject("")
                    obj[NameObject("/Artifact")] = TextStringObject("layout")
                    applied.append(
                        {"kind": "decorative", "target_id": node.id, "summary": f"{name} marked decorative"}
                    )
                elif node.alt_text:
                    obj[NameObject("/Alt")] = TextStringObject(str(node.alt_text))
                    applied.append(
                        {
                            "kind": "alt_text",
                            "target_id": node.id,
                            "summary": f"{name} /Alt -> {node.alt_text!r}",
                        }
                    )
                else:
                    skipped.append({"target_id": node.id, "reason": "no_change_required"})

    # ------- 3. Anything else: skip with reason -----------------------
    for node in iter_reading_order(tree.root):
        if isinstance(node, ImageNode):
            continue
        if isinstance(node, DocumentNode):
            continue
        # Per-element heading/list/table/link tagging is not yet written here
        # (it needs fine-grained content-stream surgery). The basic structure
        # tree added in step 4 makes the document tagged at page granularity;
        # the score honestly reports these as pending-manual for PDF.

    # ------- 3.5 OCR text layer for scanned pages ----------------------
    # Runs BEFORE tagging so the recognized text is real page content the
    # structure tagger can wrap. Only when the executor recorded the request
    # (which itself requires an available OCR provider).
    if (tree.root.metadata.properties or {}).get("ocr_text_layer_requested"):
        try:
            _apply_ocr_text_layer(writer, applied, skipped)
        except Exception as exc:  # pragma: no cover - defensive
            skipped.append({"target_id": "document", "reason": f"ocr_layer_failed: {exc}"})

    # ------- 4. Basic PDF/UA structure tree + document metadata --------
    # Turns an untagged PDF into a tagged one (MarkInfo, StructTreeRoot,
    # DisplayDocTitle, XMP). Fidelity-preserving and never corrupts.
    try:
        ua_report = tag_pdf(writer, tree)
        for kind in ua_report.get("applied", []):
            applied.append({"kind": f"pdfua_{kind}", "target_id": "document", "summary": kind})
        if not ua_report.get("structTree"):
            skipped.append({"target_id": "document", "reason": "pdfua_struct_tree_skipped"})
        # Surface WHAT the tagger actually did. These counts were previously
        # computed and thrown away, so a user could not tell whether their
        # tables were handled — and "tagged" silently read as "all of them".
        # tablesDeclined is the honest counterpart: grids we deliberately did
        # NOT tag because we weren't sure they were data.
        pdfua_summary = {
            k: ua_report.get(k, 0)
            for k in (
                "pages", "elements", "figures", "lists", "tables", "tablesDeclined",
                "links", "formWidgets", "artifacts", "readingOrderFixedPages",
                "perElementPages", "pagesPageLevelOnly",
            )
        }
        if ua_report.get("tablesDeclined"):
            skipped.append({
                "target_id": "document",
                "reason": (
                    f"pdfua_tables_declined: {ua_report['tablesDeclined']} table-like "
                    "grid(s) were too sparse to tag confidently — check them by hand"
                ),
            })
        # Pages we could only wrap as one page-level block (unparseable or
        # malformed content stream). They are valid and tagged, but carry no
        # headings, lists or tables — so counting them alongside fully
        # structured pages would overstate the result.
        if ua_report.get("pagesPageLevelOnly"):
            skipped.append({
                "target_id": "document",
                "reason": (
                    f"pdfua_page_level_only: {ua_report['pagesPageLevelOnly']} page(s) "
                    "could not be broken into elements — they are tagged, but their "
                    "headings, lists and tables were not identified"
                ),
            })
    except Exception as exc:
        skipped.append({"target_id": "document", "reason": f"pdfua_tagging_failed: {exc}"})

    try:
        with open(output_path, "wb") as fh:
            writer.write(fh)
    except Exception as exc:
        skipped.append({"target_id": str(output_path), "reason": f"failed_to_save_pdf: {exc}"})

    result: Dict[str, Any] = {"applied": applied, "skipped": skipped}
    if pdfua_summary:
        result["pdfua"] = pdfua_summary
    return result


def _resolve(obj: Any) -> Any:
    if isinstance(obj, IndirectObject):
        try:
            return obj.get_object()
        except Exception:
            return None
    return obj


def _apply_pdf_form_labels(
    writer: PdfWriter,
    doc_id: str,
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Write a ``/TU`` (accessible name) on each unlabeled AcroForm field whose
    ``/T`` is a confident, human-readable label.

    Walks the writer's cloned top-level fields with ``derive_pdf_field_label`` —
    the same helper ``pdf_parser`` counted with — so exactly the fields counted
    as ``form_fields_derivable`` are labeled, and nothing else.
    """
    try:
        catalog = writer._root_object  # noqa: SLF001 — pypdf intentionally exposes
        acro = _resolve(catalog.get("/AcroForm")) if "/AcroForm" in catalog else None
    except Exception:
        acro = None
    if not isinstance(acro, DictionaryObject):
        return
    for fo in iter_acroform_fields(acro):
        label = derive_pdf_field_label(fo)
        if not label:
            continue
        try:
            fo[NameObject("/TU")] = TextStringObject(label)
            applied.append({"kind": "form_field_label", "target_id": doc_id, "summary": f"/TU -> {label!r}"})
        except Exception as exc:  # pragma: no cover - defensive
            skipped.append({"target_id": doc_id, "reason": f"form_label_write_failed: {exc}"})


# ---------------------------------------------------------------------------
# OCR text layer for scanned pages
# ---------------------------------------------------------------------------


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _apply_ocr_text_layer(writer: PdfWriter, applied: List[Dict[str, Any]], skipped: List[Dict[str, Any]]) -> None:
    """Append an INVISIBLE (render mode 3) position-matched text layer to each
    image-only page, using the active OCR provider.

    The overlay is one BT..ET block per page appended as an EXTRA content
    stream — original page bytes are never touched. Word positions map from
    image pixels to page user space assuming a full-page scan (the dominant
    real-world case; minor selection offset is acceptable and disclosed).
    Runs before the structure tagger, which then wraps the new text into the
    reconstructed tree.
    """

    from app.services.ocr import get_ocr_provider

    provider = get_ocr_provider()
    if provider is None:
        skipped.append({"target_id": "document", "reason": "ocr_provider_unavailable"})
        return

    pages_done = 0
    words_total = 0
    for page_index, page in enumerate(writer.pages):
        try:
            existing_text = (page.extract_text() or "").strip()
        except Exception:
            existing_text = ""
        if len(existing_text) >= 50:
            continue  # not an image-only page

        # Largest embedded image = the scan.
        image_bytes = None
        try:
            best = None
            for img in page.images:
                data = getattr(img, "data", None)
                if data and (best is None or len(data) > len(best)):
                    best = data
            image_bytes = best
        except Exception:
            image_bytes = None
        if not image_bytes:
            continue

        result = provider.recognize(image_bytes)
        if result is None or not result.words or result.width_px <= 0 or result.height_px <= 0:
            continue

        mb = page.mediabox
        page_w = float(mb.width)
        page_h = float(mb.height)
        sx = page_w / result.width_px
        sy = page_h / result.height_px

        ops: List[str] = ["q", "BT", "3 Tr"]
        for word in result.words:
            if not word.text.strip():
                continue
            size = max(4.0, min(72.0, word.h * sy))
            x = word.x * sx
            y = page_h - (word.y + word.h) * sy
            ops.append(f"/F508OCR {size:.2f} Tf")
            ops.append(f"1 0 0 1 {x:.2f} {y:.2f} Tm")
            ops.append(f"({_escape_pdf_text(word.text)}) Tj")
        ops.extend(["ET", "Q"])
        overlay = DecodedStreamObject()
        overlay.set_data(("\n".join(ops)).encode("latin-1", "replace"))
        overlay_ref = writer._add_object(overlay)  # noqa: SLF001

        # Font resource for the overlay text.
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_ref = writer._add_object(font)  # noqa: SLF001
        resources = _resolve(page.get("/Resources"))
        if not isinstance(resources, DictionaryObject):
            resources = DictionaryObject()
            page[NameObject("/Resources")] = resources
        fonts = _resolve(resources.get("/Font")) if "/Font" in resources else None
        if not isinstance(fonts, DictionaryObject):
            fonts = DictionaryObject()
            resources[NameObject("/Font")] = fonts
        fonts[NameObject("/F508OCR")] = font_ref

        # Append the overlay as an extra content stream (originals untouched).
        raw_contents = page.raw_get("/Contents") if "/Contents" in page else None
        resolved = _resolve(raw_contents)
        if resolved is None:
            page[NameObject("/Contents")] = overlay_ref
        elif isinstance(resolved, ArrayObject):
            new_arr = ArrayObject(list(resolved) + [overlay_ref])
            page[NameObject("/Contents")] = new_arr
        else:
            page[NameObject("/Contents")] = ArrayObject([raw_contents, overlay_ref])

        pages_done += 1
        words_total += len(result.words)

    if pages_done:
        applied.append(
            {
                "kind": "ocr_text_layer",
                "target_id": "document",
                "summary": f"invisible OCR text layer on {pages_done} page(s), {words_total} word(s)",
            }
        )
    else:
        skipped.append({"target_id": "document", "reason": "ocr_no_recognizable_pages"})


__all__ = ["write_remediated_pdf"]
