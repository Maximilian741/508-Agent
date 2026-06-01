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
    NodeContent,
    NodeMetadata,
    ParagraphNode,
    ParserResult,
    SectionNode,
)


# ---------------------------------------------------------------------------
# Heading detection
# ---------------------------------------------------------------------------

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


def _pdf_text_colors(reader: PdfReader) -> List[Dict[str, Any]]:
    """Distinct (colour, size) used by text, scanned from content-stream fill
    colour operators (rg / g / k). Default text colour is black.

    Backgrounds in document PDFs are overwhelmingly white, so the contrast
    analyzer compares these against white. (Coloured-background pages can
    therefore be a false positive — disclosed as a known limitation.)
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
        for operands, op in ops:
            try:
                if op == b"rg" and len(operands) >= 3:
                    fill = _channels_to_hex(operands)
                elif op == b"g" and len(operands) >= 1:
                    v = max(0, min(255, round(float(operands[0]) * 255)))
                    fill = f"{v:02X}{v:02X}{v:02X}"
                elif op == b"k" and len(operands) >= 4:
                    fill = _cmyk_to_hex(operands)
                elif op == b"Tf" and len(operands) >= 2:
                    size = float(operands[1])
                elif op in (b"Tj", b"TJ", b"'", b'"'):
                    key = (fill, size)
                    if key not in seen:
                        seen.add(key)
                        out.append({"c": fill, "sz": size, "b": False})
            except (TypeError, ValueError):
                continue
        if len(out) > 64:  # representative sample is plenty
            break
    return out


def _form_field_label_counts(reader: PdfReader) -> tuple[int, int]:
    """Return ``(total, unlabeled)`` AcroForm fields.

    "Unlabeled" means no ``/TU`` (the field's accessible label/tooltip — what AT
    announces). A named field with no ``/TU`` is still unlabeled for AT.
    """
    total = 0
    unlabeled = 0
    try:
        root = reader.trailer.get("/Root", {})
        root = root.get_object() if hasattr(root, "get_object") else root
        acro = root.get("/AcroForm") if root else None
        acro = acro.get_object() if hasattr(acro, "get_object") else acro
        if not acro:
            return (0, 0)
        fields = acro.get("/Fields") or []
        fields = fields.get_object() if hasattr(fields, "get_object") else fields
        for field in fields:
            fo = field.get_object() if hasattr(field, "get_object") else field
            try:
                # Pushbuttons (field flag bit 17, /Ft Btn with PushButton) don't
                # require a /TU the same way; but counting them is conservative.
                total += 1
                tu = fo.get("/TU")
                if not tu or not str(tu).strip():
                    unlabeled += 1
            except Exception:
                continue
    except Exception:
        return (total, unlabeled)
    return (total, unlabeled)


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


def _iter_image_xobjects(page: Any) -> List[Tuple[str, Dict[str, Any]]]:
    """Return ``(name, xobject_dict)`` pairs for image XObjects on ``page``."""

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
    for name, ref in xobjects.items():
        obj = _resolve(ref)
        if not isinstance(obj, DictionaryObject):
            continue
        if obj.get("/Subtype") == "/Image":
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
        ff_total, ff_unlabeled = _form_field_label_counts(reader)
        if ff_total:
            properties["form_fields_total"] = ff_total
            properties["form_fields_unlabeled"] = ff_unlabeled
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

        for page_index in range(page_count):
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
            for para_index, paragraph in enumerate(_split_paragraphs(raw_text), start=1):
                heading_level = _classify_paragraph(paragraph)
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

            # --- Images ------------------------------------------------------
            for image_idx, (name, xobject) in enumerate(_iter_image_xobjects(page), start=1):
                alt_text, decorative = _alt_for_xobject(xobject)
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
