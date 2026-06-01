"""DOCX extraction helpers used by scan/fix routes.

Two complementary entry points are exposed:

* :meth:`DOCXParser.parse` returns the dict-shaped detection payload used by
  the legacy ``/documents`` API (this format is consumed by the existing
  ``_apply_docx_fixes`` machinery).
* :meth:`DOCXParser.parse_to_tree` builds an :class:`AccessibilityTree` so the
  document can flow through the same analyzer + executor pipeline as PDFs.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from docx import Document

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


class DOCXParser:
    def parse(self, file_path: str) -> Dict[str, object]:
        doc = Document(file_path)
        core = doc.core_properties
        w_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
        generic_link_labels = {
            "click here",
            "here",
            "read more",
            "learn more",
            "more",
            "link",
            "this",
        }

        headings: List[Dict[str, object]] = []
        empty_heading_sections: List[int] = []
        prev_level: Optional[int] = None
        skipped_jumps: List[Dict[str, object]] = []
        for idx, para in enumerate(doc.paragraphs, start=1):
            style_name = (para.style.name or "") if para.style else ""
            if not style_name.lower().startswith("heading"):
                continue
            level = 1
            try:
                tail = style_name.split(" ", 1)[1]
                level = max(1, min(6, int(tail)))
            except Exception:
                level = 1
            text = (para.text or "").strip()
            if not text:
                empty_heading_sections.append(idx)
            headings.append({"level": level, "text": text, "section": idx})
            if prev_level is not None and level > prev_level + 1:
                skipped_jumps.append({"from": prev_level, "to": level, "section": idx, "text": text})
            prev_level = level

        image_count = 0
        alt_missing = 0
        for rel in doc.part.rels.values():
            reltype = str(rel.reltype)
            if "image" in reltype:
                image_count += 1
                alt_missing += 1

        hyperlink_count = 0
        generic_links: List[Dict[str, object]] = []
        invalid_links: List[Dict[str, object]] = []
        for idx, para in enumerate(doc.paragraphs, start=1):
            hyperlink_nodes = list(para._p.iterfind(f".//{w_ns}hyperlink"))
            if not hyperlink_nodes:
                continue
            for hyperlink in hyperlink_nodes:
                text_bits = [node.text or "" for node in hyperlink.iterfind(f".//{w_ns}t")]
                link_text = "".join(text_bits).strip()
                hyperlink_count += 1
                rid = hyperlink.get(f"{rel_ns}id")
                anchor = hyperlink.get(f"{w_ns}anchor")
                target = ""
                if rid and rid in doc.part.rels:
                    rel = doc.part.rels[rid]
                    target = str(getattr(rel, "target_ref", "") or "")
                elif anchor:
                    target = f"#{anchor}"
                if not link_text:
                    continue
                normalized = link_text.lower().strip()
                if normalized in generic_link_labels:
                    generic_links.append({"section": idx, "text": link_text})
                invalid_reason = _invalid_link_reason(target)
                if invalid_reason:
                    invalid_links.append({"section": idx, "text": link_text, "target": target, "reason": invalid_reason})

        tables = len(doc.tables)
        tables_missing_headers: List[int] = []
        table_header_scope_flags: List[Dict[str, object]] = []
        generic_headers = {"column", "column 1", "column 2", "header", "n/a", "na", "value"}
        for table_index, table in enumerate(doc.tables, start=1):
            if not table.rows:
                tables_missing_headers.append(table_index)
                continue
            header_cells = table.rows[0].cells
            header_text = [(cell.text or "").strip() for cell in header_cells]
            if not any(header_text):
                tables_missing_headers.append(table_index)
                continue
            normalized_headers = [text.lower() for text in header_text if text]
            unique_headers = set(normalized_headers)
            if len(header_text) > 1 and len(unique_headers) <= 1:
                table_header_scope_flags.append(
                    {"table": table_index, "reason": "duplicate_or_single_header_label", "headers": header_text[:10]}
                )
            elif any(text.lower() in generic_headers for text in header_text if text):
                table_header_scope_flags.append(
                    {"table": table_index, "reason": "generic_header_labels", "headers": header_text[:10]}
                )
        return {
            "documentType": "docx",
            "title": (core.title or "").strip(),
            "language": (getattr(core, "language", None) or "").strip(),
            "headings": headings,
            "emptyHeadingSections": empty_heading_sections,
            "headingJumps": skipped_jumps,
            "imageCount": image_count,
            "missingAltCount": alt_missing,
            "hyperlinkCount": hyperlink_count,
            "genericLinks": generic_links,
            "invalidLinks": invalid_links,
            "tables": tables,
            "tablesMissingHeaders": tables_missing_headers,
            "tableHeaderScopeFlags": table_header_scope_flags,
            "outlineCount": len(headings),
        }


    def parse_to_tree(self, file_path: str) -> ParserResult:
        """Build an :class:`AccessibilityTree` from a DOCX file.

        The tree captures: document title/language, paragraphs, headings,
        images (with embedded base64 + mime), tables (with header detection),
        lists, and hyperlinks.  Reading order in DOCX flows linearly so the
        children of the synthetic root section preserve the source ordering.
        """

        path = Path(file_path)
        doc = Document(file_path)
        core = doc.core_properties

        title = (core.title or "").strip()
        language = (getattr(core, "language", None) or "").strip()
        properties: Dict[str, Any] = {"filename": path.name}
        if title:
            properties["title"] = title
        root = DocumentNode(
            id="doc-1",
            content=NodeContent(kind=ContentKind.NONE),
            metadata=NodeMetadata(
                language=language or None,
                source_format="docx",
                properties=properties,
            ),
            children=[],
            accessibility_flags=[],
        )

        body_section = SectionNode(
            id="docx-body",
            content=NodeContent(kind=ContentKind.TEXT, text="Document"),
            metadata=NodeMetadata(source_format="docx", properties={}),
            children=[],
            accessibility_flags=[],
        )
        root.children.append(body_section)
        ids = _IdCounter()

        # Pre-load embedded images so we can attach bytes to the matching
        # <w:drawing> nodes encountered while iterating paragraphs.
        image_blobs = _collect_image_blobs(doc)
        rid_counter = 0

        # Paragraph + heading walker (preserves order; lists handled as groups).
        list_collector: List[ListItemNode] = []
        list_marker: Optional[str] = None

        for paragraph in doc.paragraphs:
            style_name = (paragraph.style.name or "") if paragraph.style else ""
            text = (paragraph.text or "").strip()

            if _is_list_paragraph(paragraph):
                marker = _detect_list_marker(paragraph)
                if list_marker is None:
                    list_marker = marker
                if list_marker != marker:
                    body_section.children.append(_finalize_list(ids, list_collector, list_marker))
                    list_collector = []
                    list_marker = marker
                list_collector.append(
                    ListItemNode(
                        id=ids("docx-li"),
                        content=NodeContent(kind=ContentKind.TEXT, text=text or "•"),
                        metadata=NodeMetadata(source_format="docx", properties=_text_color_props(paragraph)),
                        children=[],
                        accessibility_flags=[],
                    )
                )
                continue

            if list_collector:
                body_section.children.append(_finalize_list(ids, list_collector, list_marker))
                list_collector = []
                list_marker = None

            heading_level = _heading_level_from_style(style_name)
            if heading_level:
                body_section.children.append(
                    HeadingNode(
                        id=ids("docx-h"),
                        level=heading_level,
                        content=NodeContent(kind=ContentKind.TEXT, text=text or "Heading"),
                        metadata=NodeMetadata(source_format="docx", properties=_text_color_props(paragraph)),
                        children=[],
                        accessibility_flags=[],
                    )
                )
                continue

            # Hyperlink runs first.
            link_nodes = _hyperlink_nodes_in_paragraph(paragraph, ids)
            for link in link_nodes:
                body_section.children.append(link)

            # Inline images.
            for image in _inline_images_in_paragraph(paragraph, image_blobs, ids):
                body_section.children.append(image)

            if text and not link_nodes:
                body_section.children.append(
                    ParagraphNode(
                        id=ids("docx-p"),
                        content=NodeContent(kind=ContentKind.TEXT, text=text),
                        metadata=NodeMetadata(source_format="docx", properties=_text_color_props(paragraph)),
                        children=[],
                        accessibility_flags=[],
                    )
                )

        if list_collector:
            body_section.children.append(_finalize_list(ids, list_collector, list_marker))

        # Tables (linearly after paragraphs is acceptable for the v1 flow).
        for table in doc.tables:
            body_section.children.append(_table_to_node(table, ids))

        # Ensure unique ids.
        raw_metadata = {
            "filename": path.name,
            "title": title,
            "language": language,
            "table_count": len(doc.tables),
        }
        return ParserResult(
            document_id=path.stem or "doc",
            format="docx",
            tree=AccessibilityTree(root=root, metadata=raw_metadata),
            raw_metadata=raw_metadata,
        )


# ----- DOCX → AccessibilityTree helpers --------------------------------------


_DOCX_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DOCX_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_DRAWING_NS = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_DRAWINGML_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_PIC_NS = "{http://schemas.openxmlformats.org/drawingml/2006/picture}"


def _explicit_run_colors(paragraph) -> List[Dict[str, Any]]:
    """Per-run explicit sRGB colours (for contrast analysis).

    Only runs with a concrete RGB colour are returned; theme/auto/inherited
    colours yield ``None`` from python-docx and are skipped, so we never guess.
    """
    out: List[Dict[str, Any]] = []
    for run in getattr(paragraph, "runs", []) or []:
        if not (run.text or "").strip():
            continue
        try:
            rgb = run.font.color.rgb  # RGBColor only when explicitly RGB
        except Exception:
            rgb = None
        if rgb is None:
            continue
        size_pt = None
        try:
            if run.font.size is not None:
                size_pt = float(run.font.size.pt)
        except Exception:
            size_pt = None
        out.append({"c": str(rgb), "sz": size_pt, "b": bool(run.font.bold) if run.font.bold is not None else False})
    return out


def _paragraph_bg(paragraph) -> Optional[str]:
    """Explicit paragraph shading fill (``w:shd@w:fill``) if a real colour."""
    try:
        shd = paragraph._p.find(f"{_DOCX_NS}pPr/{_DOCX_NS}shd")
        if shd is not None:
            fill = shd.get(f"{_DOCX_NS}fill")
            if fill and fill.lower() not in ("auto",):
                return fill
    except Exception:
        pass
    return None


def _text_color_props(paragraph) -> Dict[str, Any]:
    """Build the ``metadata.properties`` carrying contrast inputs (or empty)."""
    colors = _explicit_run_colors(paragraph)
    if not colors:
        return {}
    props: Dict[str, Any] = {"explicit_text_colors": colors}
    bg = _paragraph_bg(paragraph)
    if bg:
        props["bg_color"] = bg
    return props
_REL_IMAGE_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


class _IdCounter:
    def __init__(self) -> None:
        self._counts: Dict[str, int] = {}

    def __call__(self, prefix: str) -> str:
        i = self._counts.get(prefix, 0) + 1
        self._counts[prefix] = i
        return f"{prefix}-{i}"


def _heading_level_from_style(style: str) -> int:
    if not style:
        return 0
    if not style.lower().startswith("heading"):
        return 0
    parts = style.split()
    if len(parts) < 2:
        return 1
    try:
        return max(1, min(6, int(parts[1])))
    except Exception:
        return 1


def _is_list_paragraph(paragraph) -> bool:
    pPr = paragraph._p.find(f"{_DOCX_NS}pPr")
    if pPr is None:
        return False
    return pPr.find(f"{_DOCX_NS}numPr") is not None


def _detect_list_marker(paragraph) -> str:
    pPr = paragraph._p.find(f"{_DOCX_NS}pPr")
    if pPr is None:
        return "bullet"
    numPr = pPr.find(f"{_DOCX_NS}numPr")
    if numPr is None:
        return "bullet"
    numId = numPr.find(f"{_DOCX_NS}numId")
    return f"num-{numId.get(f'{_DOCX_NS}val')}" if numId is not None else "bullet"


def _finalize_list(ids: _IdCounter, items: List[ListItemNode], marker: Optional[str]) -> ListNode:
    return ListNode(
        id=ids("docx-list"),
        ordered=bool(marker and marker.startswith("num")),
        marker=marker,
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(source_format="docx"),
        children=list(items),
        accessibility_flags=[],
    )


def _collect_image_blobs(doc) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
    """Return ``{rId: (base64, mime)}`` for embedded images."""

    blobs: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    for rel_id, rel in doc.part.rels.items():
        try:
            if "image" not in str(rel.reltype):
                continue
            blob = rel.target_part.blob
        except Exception:
            continue
        if not blob or len(blob) > 2_000_000:
            continue
        target = str(getattr(rel, "target_ref", "") or "").lower()
        if target.endswith(".jpg") or target.endswith(".jpeg"):
            mime = "image/jpeg"
        elif target.endswith(".gif"):
            mime = "image/gif"
        elif target.endswith(".bmp"):
            mime = "image/bmp"
        else:
            mime = "image/png"
        blobs[rel_id] = (base64.b64encode(blob).decode("ascii"), mime)
    return blobs


def _hyperlink_nodes_in_paragraph(paragraph, ids: _IdCounter) -> List[LinkNode]:
    nodes: List[LinkNode] = []
    for hyperlink in paragraph._p.iterfind(f".//{_DOCX_NS}hyperlink"):
        runs = hyperlink.iterfind(f".//{_DOCX_NS}t")
        text = "".join((node.text or "") for node in runs).strip()
        if not text:
            continue
        rid = hyperlink.get(f"{_DOCX_REL_NS}id")
        target = ""
        if rid and rid in paragraph.part.rels:
            target = str(paragraph.part.rels[rid].target_ref or "")
        nodes.append(
            LinkNode(
                id=ids("docx-link"),
                target=target or None,
                content=NodeContent(kind=ContentKind.TEXT, text=text),
                metadata=NodeMetadata(source_format="docx"),
                children=[],
                accessibility_flags=[],
            )
        )
    return nodes


def _inline_images_in_paragraph(
    paragraph, blobs: Dict[str, Tuple[Optional[str], Optional[str]]], ids: _IdCounter
) -> List[ImageNode]:
    images: List[ImageNode] = []
    for drawing in paragraph._p.iterfind(f".//{_DRAWING_NS}*"):
        # We look for a:blip references, regardless of whether the drawing is
        # inline or anchored.  python-docx exposes the ElementTree directly.
        # NOTE: lxml elements with no children are falsy, so `a or b` silently
        # discards a found-but-childless <a:blip>.  Use explicit `is None`.
        blip = drawing.find(f".//{_DRAWINGML_NS}blip")
        if blip is None:
            blip = drawing.find(f".//{_PIC_NS}blip")
        if blip is None:
            continue
        rid = blip.get(f"{_REL_IMAGE_NS}embed") or blip.get(f"{_REL_IMAGE_NS}link")
        if not rid:
            continue
        alt = drawing.find(f".//{_DRAWING_NS}docPr")
        if alt is None:
            alt = drawing.find(f".//{_DRAWINGML_NS}docPr")
        alt_text = ""
        is_decorative = False
        if alt is not None:
            alt_text = (alt.get("descr") or alt.get("title") or "").strip()
            decorative_attr = alt.get("hidden") or ""
            if decorative_attr.lower() in {"1", "true"}:
                is_decorative = True
        b64, mime = blobs.get(rid, (None, None))
        properties: Dict[str, Any] = {"image_rid": rid}
        if b64:
            properties["image_b64"] = b64
            properties["image_mime"] = mime or "image/png"
        node_id = ids("docx-img")
        if is_decorative and alt_text:
            images.append(
                ImageNode.model_construct(
                    id=node_id,
                    node_type=ImageNode.type_value(),
                    content=NodeContent(kind=ContentKind.NONE),
                    metadata=NodeMetadata(source_format="docx", properties=properties),
                    children=[],
                    accessibility_flags=[],
                    is_decorative=True,
                    alt_text=alt_text,
                )
            )
        else:
            images.append(
                ImageNode(
                    id=node_id,
                    content=NodeContent(kind=ContentKind.NONE),
                    metadata=NodeMetadata(source_format="docx", properties=properties),
                    children=[],
                    accessibility_flags=[],
                    is_decorative=is_decorative,
                    alt_text=alt_text or None,
                )
            )
    return images


def _table_to_node(table, ids: _IdCounter) -> TableNode:
    rows: List[TableRowNode] = []
    for row_index, row in enumerate(table.rows):
        cells: List[TableCellNode] = []
        for cell in row.cells:
            text = (cell.text or "").strip()
            cell_type = TableCellType.HEADER if row_index == 0 and text else TableCellType.DATA
            cells.append(
                TableCellNode(
                    id=ids("docx-cell"),
                    cell_type=cell_type,
                    header_scope=TableHeaderScope.COLUMN if cell_type == TableCellType.HEADER else TableHeaderScope.NONE,
                    content=NodeContent(kind=ContentKind.TEXT, text=text or " "),
                    metadata=NodeMetadata(source_format="docx"),
                    children=[],
                    accessibility_flags=[],
                )
            )
        rows.append(
            TableRowNode(
                id=ids("docx-row"),
                content=NodeContent(kind=ContentKind.NONE),
                metadata=NodeMetadata(source_format="docx"),
                children=cells,
                accessibility_flags=[],
            )
        )
    return TableNode(
        id=ids("docx-table"),
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(source_format="docx"),
        children=rows,
        accessibility_flags=[],
    )


def _invalid_link_reason(target: str) -> str:
    value = (target or "").strip()
    if not value:
        return "missing_target"
    if value.startswith("#"):
        return ""
    lowered = value.lower()
    if lowered in {"http://", "https://", "www.", "mailto:"}:
        return "placeholder_target"
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https", "mailto"}:
        if parsed.scheme in {"http", "https"} and not parsed.netloc:
            return "missing_host"
        return ""
    if parsed.scheme == "" and parsed.path:
        return ""
    return "unsupported_scheme"
