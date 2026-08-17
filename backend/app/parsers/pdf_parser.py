"""Pragmatic PDF parser that produces an :class:`AccessibilityTree`.

The parser extracts what is most useful for static 508/WCAG analysis without
attempting a full PDF/UA tagged-tree implementation:

* Document metadata (title, language)
* Page-level text grouped into paragraph nodes
* Headings inferred from font-size heuristics on /Tj operands when available,
  falling back to leading-uppercase / short-line heuristics on extracted text.
* Image XObjects with ``/Alt`` markings (decorative if ``/Alt`` is empty or
  marked ``/Artifact``).
* Hyperlink annotations promoted to :class:`LinkNode` instances.

Anything we cannot reliably infer is left off the tree so the analyzers do not
emit false-positive flags.  The parser is intentionally tolerant of malformed
PDFs — in the worst case we still return a non-empty document with a single
section so the analyzer/remediator pipeline can run end-to-end.
"""

from __future__ import annotations

import base64
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from pypdf import PdfReader
from pypdf.generic import (
    ArrayObject,
    ContentStream,
    DictionaryObject,
    IndirectObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

from app.models.accessibility import (
    AccessibilityTree,
    ContentKind,
    DocumentNode,
    HeadingNode,
    ImageNode,
    LinkNode,
    ListItemNode,
    ListNode,
    NodeContent,
    NodeMetadata,
    ParagraphNode,
    ParserResult,
    SectionNode,
    TableCellNode,
    TableCellType,
    TableHeaderScope,
    TableNode,
    TableRowNode,
)


# ---------------------------------------------------------------------------
# Heading detection
# ---------------------------------------------------------------------------

# Max PDF pages analyzed per document — a guard against a single huge upload
# tying up a worker (each page is tokenized several times across parser +
# tagger). Overridable via env for hosts with more headroom.
try:
    _MAX_PDF_PAGES = max(1, int(os.environ.get("MAX_PDF_PAGES", "400")))
except (TypeError, ValueError):
    _MAX_PDF_PAGES = 400

_HEADING_RE = re.compile(r"^(?P<num>\d+(?:\.\d+){0,5})\s+(?P<text>.+)$")
_ALL_CAPS_RE = re.compile(r"^[A-Z0-9][A-Z0-9 \-:&,'\".]+$")


def _classify_paragraph(text: str) -> Optional[int]:
    """Return a heading level (1-6) if ``text`` looks like a heading, else None."""

    stripped = text.strip()
    if not stripped:
        return None
    if len(stripped) > 120:
        return None
    if "\n" in stripped:
        return None

    match = _HEADING_RE.match(stripped)
    if match:
        depth = match.group("num").count(".") + 1
        return max(1, min(depth, 6))

    if len(stripped) <= 80 and _ALL_CAPS_RE.match(stripped):
        # Short, mostly upper-case lines look like headings (e.g. "INTRODUCTION").
        return 1

    if len(stripped.split()) <= 8 and stripped.endswith(":"):
        return 2

    return None


# ---------------------------------------------------------------------------
# PDF metadata helpers
# ---------------------------------------------------------------------------


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value).strip()
    except Exception:
        return ""


def _document_title(reader: PdfReader) -> str:
    metadata = getattr(reader, "metadata", None)
    if metadata is None:
        return ""
    for key in ("/Title", "title"):
        try:
            value = metadata.get(key)
        except Exception:
            value = None
        if value:
            text = _safe_text(value)
            if text:
                return text
    return ""


def _document_language(reader: PdfReader) -> str:
    try:
        catalog = reader.trailer["/Root"]
    except Exception:
        return ""
    try:
        lang = catalog.get("/Lang")
    except Exception:
        lang = None
    return _safe_text(lang)


def _channels_to_hex(ch) -> str:
    r, g, b = (max(0, min(255, round(float(c) * 255))) for c in ch[:3])
    return f"{r:02X}{g:02X}{b:02X}"


def _cmyk_to_hex(ch) -> str:
    c, m, y, k = (float(x) for x in ch[:4])
    r = round(255 * (1 - c) * (1 - k))
    g = round(255 * (1 - m) * (1 - k))
    b = round(255 * (1 - y) * (1 - k))
    return f"{max(0, min(255, r)):02X}{max(0, min(255, g)):02X}{max(0, min(255, b)):02X}"


# Path-painting operators that FILL (used to spot coloured backgrounds).
_PAINT_FILL_OPS = {b"f", b"F", b"f*", b"b", b"b*", b"B", b"B*"}


def _is_whiteish_hex(hex6: str) -> bool:
    try:
        r, g, b = int(hex6[0:2], 16), int(hex6[2:4], 16), int(hex6[4:6], 16)
    except (TypeError, ValueError):
        return False
    return r >= 242 and g >= 242 and b >= 242


def _pdf_text_colors(reader: PdfReader) -> List[Dict[str, Any]]:
    """Distinct (colour, size) used by text, scanned from content-stream fill
    colour operators (rg / g / k). Default text colour is black.

    Backgrounds in document PDFs are overwhelmingly white, so the contrast
    analyzer compares these against white. A page that PAINTS a non-white
    fill (coloured rectangle, cell shading, gradient `sh`) breaks that
    assumption — light text on a dark band would be flagged as low-contrast
    even though it reads fine. Such pages are skipped entirely: no guess, no
    false positive (precision over recall).
    """
    out: List[Dict[str, Any]] = []
    seen = set()
    for page in reader.pages:
        try:
            ops = ContentStream(page.get_contents(), reader).operations
        except Exception:
            continue
        fill = "000000"
        size = None
        page_hits: List[Dict[str, Any]] = []
        colored_bg = False
        for operands, op in ops:
            try:
                if op == b"rg" and len(operands) >= 3:
                    fill = _channels_to_hex(operands)
                elif op == b"g" and len(operands) >= 1:
                    v = max(0, min(255, round(float(operands[0]) * 255)))
                    fill = f"{v:02X}{v:02X}{v:02X}"
                elif op == b"k" and len(operands) >= 4:
                    fill = _cmyk_to_hex(operands)
                elif op in _PAINT_FILL_OPS:
                    if not _is_whiteish_hex(fill):
                        colored_bg = True
                        break
                elif op == b"sh":  # shading/gradient paint — bg unknowable
                    colored_bg = True
                    break
                elif op == b"Tf" and len(operands) >= 2:
                    size = float(operands[1])
                elif op in (b"Tj", b"TJ", b"'", b'"'):
                    key = (fill, size)
                    if key not in seen:
                        seen.add(key)
                        page_hits.append({"c": fill, "sz": size, "b": False})
            except (TypeError, ValueError):
                continue
        if colored_bg:
            # Undo this page's contribution to the dedupe set so another
            # white-background page can still report the same colour.
            for hit in page_hits:
                seen.discard((hit["c"], hit["sz"]))
            continue
        out.extend(page_hits)
        if len(out) > 64:  # representative sample is plenty
            break
    return out


# Whole words (or, in the numbered check, word stems) that mark an
# auto-generated field name. Includes a few common non-English auto stems
# (champ/feld/campo …) — these only fire as a label-killer below, never accept.
_PDF_WIDGET_WORDS = {
    "text", "field", "fld", "form", "input", "box", "check", "checkbox",
    "cb", "radio", "button", "btn", "combo", "list", "listbox", "dropdown",
    "choice", "control", "element", "entry", "txt", "textbox", "textfield",
    "champ", "zone", "texte", "feld", "textfeld", "campo", "casilla",
}
# Suffix roots: a word that ENDS with one of these is a widget type, not a
# label ("DateField"→field, "fillText"→text, "TextBox"/"PO Box"→box). Field
# /T names are short label-like tokens, so substring/suffix matching here is
# safe (no real label is "Springfield"); we err toward leaving fields manual.
_PDF_WIDGET_SUFFIXES = (
    "textbox", "textfield", "checkbox", "combobox", "listbox", "dropdown",
    "text", "field", "box", "button", "radio", "combo", "menu",
)
# Bare auto-names with no number that still name nothing.
_PDF_JUNK_NAMES = {
    "untitled", "undefined", "field", "text", "form", "fld", "button",
    "btn", "checkbox", "check box", "radio", "radio button", "box", "control",
    "champ", "feld", "campo", "zone",
}
# Checkbox/radio export/state VALUES that are sometimes used as the /T — these
# are answers, not field labels, so they must never become an accessible name.
_PDF_VALUE_TOKENS = {"yes", "no", "on", "off", "true", "false", "none", "n/a", "na"}


def _pdf_word_kind(w: str) -> str:
    has_alpha = any(c.isalpha() for c in w)
    has_digit = any(c.isdigit() for c in w)
    if has_alpha and has_digit:
        return "mixed"
    if has_digit:
        return "number"
    if has_alpha:
        return "alpha"
    return "other"


def _clean_pdf_field_name(raw: object) -> Optional[str]:
    """A human-readable label from an AcroForm field's ``/T``, or None.

    The partial field name is frequently descriptive in real fillable PDFs
    ("First Name", "Date of Birth") and is exactly what an accessible name
    should say — but just as often it is an auto-generated placeholder
    ("Text1", "Text Field 2", "DateField"), an XFA path
    ("topmostSubform[0].Page1[0].f1_01[0]"), an opaque id ("a8f3c2d1"), or a
    code ("Q1a"). We accept only clean human labels; everything ambiguous stays
    manual, because a wrong accessible name is worse than none.
    """
    t = str(raw or "").strip()
    # Strip XFA index brackets ([0]) and #subform sigils before anything else.
    t = re.sub(r"\[\d+\]", "", t).replace("#", " ")
    if "." in t:  # fully-qualified name — the field's own terminal segment
        t = t.split(".")[-1].strip()
    t = re.sub(r"\s+", " ", t.replace("_", " ")).strip()
    if not t or len(t) > 60 or len(t) == 1:
        return None
    if not any(c.isalpha() for c in t):
        return None
    low = t.lower()
    if low in _PDF_JUNK_NAMES or low in _PDF_VALUE_TOKENS:
        return None
    words = low.split(" ")
    # Any token that mixes letters and digits is a code/id/auto-name, not a
    # label ("Text1", "Q1a", "Item3b", "f1", "a8f3c2d1", "TextField1").
    if any(_pdf_word_kind(w) == "mixed" for w in words):
        return None
    # Hex/GUID-shaped single token ("ffff" with a digit, "a8f3...") — belt and
    # braces; most are already caught as "mixed".
    if " " not in t and any(c.isdigit() for c in t) and re.fullmatch(r"[0-9a-fA-F]+", t):
        return None
    # A widget-type word ("DateField", "TextBox", "fillText", "Field", "Champ").
    for w in words:
        stem = w.rstrip("0123456789")
        if not stem:
            continue
        if stem in _PDF_WIDGET_WORDS or stem.endswith(_PDF_WIDGET_SUFFIXES):
            return None
    return t


def derive_pdf_field_label(fo: object) -> Optional[str]:
    """Confident accessible label for one unlabeled AcroForm field, or None.

    Shared by the parser (to count how many are derivable) and the writer (to
    write exactly those), so the credited count always equals what is written.
    Only ``/Tx`` (text) and ``/Ch`` (choice) fields are labeled from ``/T``;
    ``/Btn`` (checkbox / radio / pushbutton) ``/T`` is frequently the export
    VALUE ("Yes", "Male") rather than a label, so those stay manual.
    """
    try:
        if fo.get("/FT") == "/Btn":
            return None
        tu = fo.get("/TU")
        if tu and str(tu).strip():
            return None  # already has an accessible name
        return _clean_pdf_field_name(fo.get("/T"))
    except Exception:
        return None


def iter_acroform_fields(acro: object):
    """Yield the resolved top-level AcroForm field dicts — the exact set the
    label count and the writer both operate on, so they stay in lockstep."""
    fields = acro.get("/Fields") or []
    fields = fields.get_object() if hasattr(fields, "get_object") else fields
    for field in fields:
        try:
            yield field.get_object() if hasattr(field, "get_object") else field
        except Exception:
            continue


def _form_field_label_counts(reader: PdfReader) -> "tuple[int, int, int]":
    """Return ``(total, unlabeled, derivable)`` AcroForm fields.

    "Unlabeled" means no ``/TU`` (the field's accessible label/tooltip — what AT
    announces). ``derivable`` is how many unlabeled fields have a confident
    label we can auto-write from their ``/T`` (the rest stay manual).
    """
    total = 0
    unlabeled = 0
    derivable = 0
    try:
        root = reader.trailer.get("/Root", {})
        root = root.get_object() if hasattr(root, "get_object") else root
        acro = root.get("/AcroForm") if root else None
        acro = acro.get_object() if hasattr(acro, "get_object") else acro
        if not acro:
            return (0, 0, 0)
        for fo in iter_acroform_fields(acro):
            try:
                # Pushbuttons are counted (conservative) but never auto-labeled.
                total += 1
                tu = fo.get("/TU")
                if not tu or not str(tu).strip():
                    unlabeled += 1
                    if derive_pdf_field_label(fo):
                        derivable += 1
            except Exception:
                continue
    except Exception:
        return (total, unlabeled, derivable)
    return (total, unlabeled, derivable)


# ---------------------------------------------------------------------------
# Image / annotation extraction
# ---------------------------------------------------------------------------


def _resolve(obj: Any) -> Any:
    if isinstance(obj, IndirectObject):
        try:
            return obj.get_object()
        except Exception:
            return None
    return obj


def _names_drawn_on_page(page: Any, reader: Any) -> Optional[set]:
    """XObject names actually painted by the page's content stream (`Do` ops).

    Returns ``None`` when the content stream can't be parsed — callers should
    then fall back to resource listing. Restricting images to names that are
    really DRAWN avoids a classic false-positive multiplier: merged/optimized
    PDFs often share one resource dictionary across every page, which would
    otherwise yield one MISSING_ALT_TEXT per page per listed-but-unused image.
    """
    try:
        contents = page.get_contents()
        if contents is None:
            return set()
        ops = ContentStream(contents, reader).operations
    except Exception:
        return None
    drawn: set = set()
    for operands, op in ops:
        if op == b"Do" and operands:
            drawn.add(str(operands[0]).lstrip("/"))
    return drawn


def _iter_image_xobjects(page: Any, reader: Any = None) -> List[Tuple[str, Dict[str, Any]]]:
    """Return ``(name, xobject_dict)`` pairs for image XObjects DRAWN on ``page``."""

    results: List[Tuple[str, Dict[str, Any]]] = []
    try:
        resources = _resolve(page.get("/Resources"))
    except Exception:
        resources = None
    if not isinstance(resources, DictionaryObject):
        return results
    xobjects = _resolve(resources.get("/XObject")) if "/XObject" in resources else None
    if not isinstance(xobjects, DictionaryObject):
        return results
    drawn = _names_drawn_on_page(page, reader) if reader is not None else None
    for name, ref in xobjects.items():
        obj = _resolve(ref)
        if not isinstance(obj, DictionaryObject):
            continue
        if obj.get("/Subtype") != "/Image":
            continue
        if drawn is not None and str(name).lstrip("/") not in drawn:
            continue  # listed in resources but never painted on this page
        results.append((str(name), obj))
    return results


def _alt_for_xobject(xobject: Dict[str, Any]) -> Tuple[Optional[str], bool]:
    """Return ``(alt_text, decorative)``.

    A PDF marks decorative images with ``/Artifact`` or an empty ``/Alt``.
    """

    alt_obj = xobject.get("/Alt") if "/Alt" in xobject else None
    raw_alt = _safe_text(alt_obj)
    decorative = False
    structure = xobject.get("/StructParent") or xobject.get("/StructParents")
    artifact = False
    actual = xobject.get("/ActualText")
    actual_text = _safe_text(actual)
    if actual_text and not raw_alt:
        raw_alt = actual_text
    if "/Artifact" in xobject:
        artifact = True
    if artifact:
        decorative = True
        return None, True
    if alt_obj is not None and not raw_alt:
        # An explicit empty /Alt declares the image decorative.
        return None, True
    if structure is None and not raw_alt:
        # No structural parent and no alt — treat as decorative to avoid
        # generating false-positive flags for tagged decorative images.
        decorative = False
    return (raw_alt or None), decorative


_FILTER_TO_MIME = {
    "/DCTDecode": "image/jpeg",
    "/JPXDecode": "image/jp2",
    "/CCITTFaxDecode": "image/tiff",
    "/FlateDecode": "image/png",
}


def _extract_image_bytes(xobject: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Best-effort raw image extraction.

    Returns ``(base64-payload, mime-type)`` or ``(None, None)`` if extraction
    is unsupported or the image is too large to inline (>2MB raw).
    """

    try:
        raw = xobject.get_data()
    except Exception:
        return None, None
    if not raw:
        return None, None
    if len(raw) > 2_000_000:
        return None, None
    filt = xobject.get("/Filter")
    mime = "image/png"
    if isinstance(filt, list) or isinstance(filt, ArrayObject):
        for f in filt:
            mime = _FILTER_TO_MIME.get(str(f), mime)
    elif filt is not None:
        mime = _FILTER_TO_MIME.get(str(filt), mime)
    try:
        encoded = base64.b64encode(raw).decode("ascii")
    except Exception:
        return None, None
    return encoded, mime


def _link_annotations(page: Any) -> List[Dict[str, Any]]:
    annots_obj = _resolve(page.get("/Annots")) if "/Annots" in page else None
    if not isinstance(annots_obj, (list, ArrayObject)):
        return []
    links: List[Dict[str, Any]] = []
    for ref in annots_obj:
        annot = _resolve(ref)
        if not isinstance(annot, DictionaryObject):
            continue
        if annot.get("/Subtype") != "/Link":
            continue
        target = ""
        action = _resolve(annot.get("/A")) if "/A" in annot else None
        if isinstance(action, DictionaryObject):
            uri = action.get("/URI")
            if uri is not None:
                target = _safe_text(uri)
        contents = _safe_text(annot.get("/Contents"))
        links.append({"target": target, "contents": contents})
    return links


# ---------------------------------------------------------------------------
# Tree assembly
# ---------------------------------------------------------------------------


def _node_metadata(page_index: Optional[int] = None, **properties: Any) -> NodeMetadata:
    return NodeMetadata(
        page=page_index,
        source_format="pdf",
        properties={k: v for k, v in properties.items() if v is not None},
    )


def _split_paragraphs(text: str) -> List[str]:
    if not text:
        return []
    chunks = re.split(r"\n\s*\n+", text)
    out: List[str] = []
    for chunk in chunks:
        cleaned = re.sub(r"[ \t]+", " ", chunk).strip()
        if cleaned:
            out.append(cleaned)
    return out


class PDFParser:
    """Pragmatic v1 PDF parser → :class:`ParserResult`."""

    def parse(self, file_path: str) -> ParserResult:
        path = Path(file_path)
        document_id = path.stem or "doc"
        reader = PdfReader(str(path))

        title = _document_title(reader)
        language = _document_language(reader)

        properties: Dict[str, Any] = {}
        if title:
            properties["title"] = title
        ff_total, ff_unlabeled, ff_derivable = _form_field_label_counts(reader)
        if ff_total:
            properties["form_fields_total"] = ff_total
            properties["form_fields_unlabeled"] = ff_unlabeled
            # Unlabeled fields whose /T is a real label → auto-write /TU. The
            # writer re-derives with the SAME helper so credit == what's written.
            properties["form_fields_derivable"] = ff_derivable
        # Text colours from the content stream → contrast analysis (vs white).
        text_colors = _pdf_text_colors(reader)
        if text_colors:
            properties["explicit_text_colors"] = text_colors
            properties["bg_color"] = "FFFFFF"

        root = DocumentNode(
            id="doc-1",
            content=NodeContent(kind=ContentKind.NONE),
            metadata=NodeMetadata(
                language=language or None,
                source_format="pdf",
                properties=properties,
            ),
            children=[],
            accessibility_flags=[],
        )

        page_count = len(reader.pages)
        next_id = _IdCounter()

        # Tagged-PDF structure info: headings + tables read from the EXISTING
        # tree (RoleMap-resolved), and Figure /Alt mapped back to the drawn
        # XObject — fixing the false positive where a properly tagged figure
        # (alt on the StructElem, the standards-correct place) was flagged as
        # missing alt because only the XObject dictionary was consulted.
        struct_info = None
        try:
            from app.pdf.tag_reader import read_struct_info

            struct_info = read_struct_info(reader)
        except Exception:
            struct_info = None
        tree_alt_by_xobject = (struct_info or {}).get("figure_alt_by_xobject", {})
        struct_headings = (struct_info or {}).get("headings", [])
        struct_tables = (struct_info or {}).get("tables", [])
        struct_lists = (struct_info or {}).get("lists", [])
        # When the tree declares headings, the tags are authoritative — the
        # text-shape heuristic stays for untagged documents only.
        use_tag_headings = bool(struct_headings)

        # Aggregate signals for the "scanned PDF / image-only" detector.
        total_text_chars = 0
        image_only_pages = 0  # pages with image XObject(s) and <50 chars of text

        # Page cap: real-world government PDFs can be hundreds of pages, and
        # every page is tokenized multiple times (text colours, struct tree,
        # text extract, then the writer's tagger re-tokenizes). Without a
        # ceiling one huge upload ties up a worker for minutes and can blow
        # the request timeout. Analyze the first N pages and disclose the
        # truncation honestly rather than hanging.
        pages_to_process = min(page_count, _MAX_PDF_PAGES)
        if page_count > _MAX_PDF_PAGES:
            # Write to root.metadata.properties, NOT the local `properties`
            # dict: NodeMetadata is a pydantic model and COPIES the dict at
            # construction (line ~590), so the two diverged the moment the
            # root was built. Writing to the local here recorded the
            # truncation into a dict nothing ever read again — the disclosure
            # was dead on arrival for every over-cap document.
            root.metadata.properties["pages_truncated"] = True
            root.metadata.properties["pages_processed"] = pages_to_process

        for page_index in range(pages_to_process):
            page = reader.pages[page_index]
            page_label = f"page-{page_index + 1}"
            section = SectionNode(
                id=next_id(f"{page_label}-section"),
                content=NodeContent(kind=ContentKind.TEXT, text=f"Page {page_index + 1}"),
                metadata=_node_metadata(page_index=page_index + 1),
                children=[],
                accessibility_flags=[],
            )

            # --- Text → headings + paragraphs -------------------------------
            try:
                raw_text = page.extract_text() or ""
            except Exception:
                raw_text = ""
            page_text_chars = len((raw_text or "").strip())
            total_text_chars += page_text_chars
            for para_index, paragraph in enumerate(_split_paragraphs(raw_text), start=1):
                heading_level = None if use_tag_headings else _classify_paragraph(paragraph)
                if heading_level is not None:
                    section.children.append(
                        HeadingNode(
                            id=next_id(f"{page_label}-h{para_index}"),
                            level=heading_level,
                            content=NodeContent(kind=ContentKind.TEXT, text=paragraph),
                            metadata=_node_metadata(page_index=page_index + 1),
                            children=[],
                            accessibility_flags=[],
                        )
                    )
                else:
                    section.children.append(
                        ParagraphNode(
                            id=next_id(f"{page_label}-p{para_index}"),
                            content=NodeContent(kind=ContentKind.TEXT, text=paragraph),
                            metadata=_node_metadata(page_index=page_index + 1),
                            children=[],
                            accessibility_flags=[],
                        )
                    )

            # --- Headings from the EXISTING structure tree (tagged PDFs) ----
            if use_tag_headings:
                for h_idx, h in enumerate(
                    (h for h in struct_headings if h.get("page") == page_index), start=1
                ):
                    section.children.append(
                        HeadingNode(
                            id=next_id(f"{page_label}-th{h_idx}"),
                            level=max(1, min(6, int(h.get("level") or 1))),
                            content=NodeContent(kind=ContentKind.TEXT, text=h.get("text") or ""),
                            metadata=_node_metadata(page_index=page_index + 1, from_tags=True),
                            children=[],
                            accessibility_flags=[],
                        )
                    )

            # --- Tables from the EXISTING structure tree (tagged PDFs) ------
            for t_idx, t in enumerate(
                (t for t in struct_tables if (t.get("page") or 0) == page_index), start=1
            ):
                rows: List[TableRowNode] = []
                for cell_tags in t.get("rows", []):
                    cells = [
                        TableCellNode(
                            id=next_id(f"{page_label}-tcell"),
                            cell_type=TableCellType.HEADER if s == "TH" else TableCellType.DATA,
                            header_scope=TableHeaderScope.COLUMN if s == "TH" else TableHeaderScope.NONE,
                            content=NodeContent(kind=ContentKind.TEXT, text=" "),
                            metadata=_node_metadata(page_index=page_index + 1),
                            children=[],
                            accessibility_flags=[],
                        )
                        for s in cell_tags
                    ]
                    rows.append(
                        TableRowNode(
                            id=next_id(f"{page_label}-trow"),
                            content=NodeContent(kind=ContentKind.NONE),
                            metadata=_node_metadata(page_index=page_index + 1),
                            children=cells,
                            accessibility_flags=[],
                        )
                    )
                if rows:
                    section.children.append(
                        TableNode(
                            id=next_id(f"{page_label}-ttable"),
                            content=NodeContent(kind=ContentKind.NONE),
                            metadata=_node_metadata(page_index=page_index + 1, from_tags=True),
                            children=rows,
                            accessibility_flags=[],
                        )
                    )

            # --- Lists from the EXISTING structure tree (tagged PDFs) -------
            # A tagged /L whose kids aren't /LI is a classic bad-remediation
            # artifact; ListStructureAnalyzer flags exactly that shape.
            for l in (l for l in struct_lists if (l.get("page") or 0) == page_index):
                kid_nodes: List[Any] = []
                for kid_s in l.get("kids", []):
                    if kid_s == "LI":
                        kid_nodes.append(
                            ListItemNode(
                                id=next_id(f"{page_label}-tli"),
                                content=NodeContent(kind=ContentKind.TEXT, text=" "),
                                metadata=_node_metadata(page_index=page_index + 1),
                                children=[],
                                accessibility_flags=[],
                            )
                        )
                    else:
                        kid_nodes.append(
                            ParagraphNode(
                                id=next_id(f"{page_label}-tlp"),
                                content=NodeContent(kind=ContentKind.TEXT, text=" "),
                                metadata=_node_metadata(page_index=page_index + 1),
                                children=[],
                                accessibility_flags=[],
                            )
                        )
                if kid_nodes:
                    section.children.append(
                        ListNode(
                            id=next_id(f"{page_label}-tlist"),
                            content=NodeContent(kind=ContentKind.NONE),
                            metadata=_node_metadata(page_index=page_index + 1, from_tags=True),
                            children=kid_nodes,
                            accessibility_flags=[],
                        )
                    )

            # --- Images ------------------------------------------------------
            page_image_count = 0
            for image_idx, (name, xobject) in enumerate(_iter_image_xobjects(page, reader), start=1):
                page_image_count += 1
                alt_text, decorative = _alt_for_xobject(xobject)
                if alt_text is None and not decorative:
                    # Properly tagged PDFs keep alt on the Figure StructElem —
                    # honour it instead of false-flagging the image.
                    tree_alt = tree_alt_by_xobject.get(str(name).lstrip("/"))
                    if tree_alt:
                        alt_text = tree_alt
                image_b64, image_mime = _extract_image_bytes(xobject)
                section.children.append(
                    _build_image_node(
                        node_id=next_id(f"{page_label}-img{image_idx}"),
                        page=page_index + 1,
                        alt_text=alt_text,
                        decorative=decorative,
                        xobject_name=name,
                        image_b64=image_b64,
                        image_mime=image_mime,
                    )
                )
            # A page with image content but essentially no extractable text is
            # almost certainly a scanned page (or a poster/infographic). Used
            # downstream to flag SCANNED_DOCUMENT_NO_TEXT.
            if page_image_count > 0 and page_text_chars < 50:
                image_only_pages += 1

            # --- Links -------------------------------------------------------
            for link_idx, link in enumerate(_link_annotations(page), start=1):
                contents = link["contents"] or link["target"] or "(link)"
                section.children.append(
                    LinkNode(
                        id=next_id(f"{page_label}-link{link_idx}"),
                        target=link["target"] or None,
                        content=NodeContent(kind=ContentKind.TEXT, text=contents or "link"),
                        metadata=_node_metadata(page_index=page_index + 1),
                        children=[],
                        accessibility_flags=[],
                    )
                )

            root.children.append(section)

        # Stash scan-detection signals on the root for ScannedDocumentAnalyzer.
        root.metadata.properties["page_count"] = page_count
        root.metadata.properties["total_text_chars"] = total_text_chars
        root.metadata.properties["image_only_pages"] = image_only_pages

        # Is this a TAGGED PDF (has a structure tree)? Untagged PDFs are the
        # single most common real-world accessibility failure — and the thing
        # our remediation genuinely fixes (the writer reconstructs a full
        # struct tree). UntaggedPdfAnalyzer flags it; DocumentHeadingsAnalyzer
        # also uses this to avoid contradicting our own tagged output.
        try:
            catalog = reader.trailer["/Root"]
            struct = catalog.get("/StructTreeRoot")
            root.metadata.properties["pdf_tagged"] = bool(
                struct.get_object() if hasattr(struct, "get_object") else struct
            )
        except Exception:
            root.metadata.properties["pdf_tagged"] = False

        raw_metadata: Dict[str, Any] = {
            "page_count": page_count,
            "title": title,
            "language": language,
        }
        tree = AccessibilityTree(root=root, metadata=raw_metadata)
        return ParserResult(
            document_id=document_id,
            format="pdf",
            tree=tree,
            raw_metadata=raw_metadata,
        )


def _build_image_node(
    *,
    node_id: str,
    page: int,
    alt_text: Optional[str],
    decorative: bool,
    xobject_name: str,
    image_b64: Optional[str] = None,
    image_mime: Optional[str] = None,
) -> ImageNode:
    properties: Dict[str, Any] = {"xobject": xobject_name}
    if image_b64:
        properties["image_b64"] = image_b64
        properties["image_mime"] = image_mime or "image/png"
    metadata = NodeMetadata(
        page=page,
        source_format="pdf",
        properties=properties,
    )
    if decorative and alt_text:
        # Cannot construct a normal decorative ImageNode if alt_text non-empty —
        # use ``model_construct`` to bypass the validator so the analyzer can
        # flag DECORATIVE_IMAGE_WITH_ALT and the remediator can clean it up.
        return ImageNode.model_construct(
            id=node_id,
            node_type=ImageNode.type_value(),
            content=NodeContent(kind=ContentKind.NONE),
            metadata=metadata,
            children=[],
            accessibility_flags=[],
            is_decorative=True,
            alt_text=alt_text,
        )
    return ImageNode(
        id=node_id,
        content=NodeContent(kind=ContentKind.NONE),
        metadata=metadata,
        children=[],
        accessibility_flags=[],
        is_decorative=decorative,
        alt_text=alt_text,
    )


class _IdCounter:
    """Generate stable, unique node ids while preserving a hint."""

    def __init__(self) -> None:
        self._seen: Dict[str, int] = {}

    def __call__(self, hint: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", hint).strip("-") or "node"
        count = self._seen.get(cleaned, 0)
        self._seen[cleaned] = count + 1
        if count == 0:
            return cleaned
        return f"{cleaned}-{count}"

# alias for the public `parse_to_tree` convention used elsewhere
PDFParser.parse_to_tree = PDFParser.parse  # type: ignore[attr-defined]
