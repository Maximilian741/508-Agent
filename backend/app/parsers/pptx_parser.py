"""PPTX extraction helpers used by scan/fix routes.

Same dual-API pattern as :mod:`app.parsers.docx_parser`: a legacy
dict-returning :meth:`PPTXParser.parse` plus a new
:meth:`PPTXParser.parse_to_tree` that emits an :class:`AccessibilityTree`.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

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
    TableCellNode,
    TableCellType,
    TableHeaderScope,
    TableNode,
    TableRowNode,
)


class PPTXParser:
    def parse(self, file_path: str) -> Dict[str, object]:
        prs = Presentation(file_path)
        slide_details: List[Dict[str, object]] = []
        slides_missing_titles: List[int] = []
        total_images = 0
        missing_alt = 0
        total_tables = 0
        tables_missing_headers: List[Dict[str, object]] = []
        table_header_scope_flags: List[Dict[str, object]] = []
        hyperlink_count = 0
        generic_links: List[Dict[str, object]] = []
        invalid_links: List[Dict[str, object]] = []
        reading_order_warnings: List[Dict[str, object]] = []
        generic_link_labels = {
            "click here",
            "here",
            "read more",
            "learn more",
            "more",
            "link",
            "this",
        }
        generic_headers = {"column", "column 1", "column 2", "header", "n/a", "na", "value"}

        for idx, slide in enumerate(prs.slides, start=1):
            title = slide.shapes.title.text.strip() if slide.shapes.title and slide.shapes.title.text else ""
            if not title:
                slides_missing_titles.append(idx)
            slide_images = 0
            slide_tables = 0
            shape_positions: List[tuple[float, float]] = []
            for shape in slide.shapes:
                left = float(getattr(shape, "left", 0) or 0)
                top = float(getattr(shape, "top", 0) or 0)
                shape_positions.append((top, left))
                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    slide_images += 1
                    alt_text = (getattr(shape, "alternative_text", None) or "").strip()
                    if not alt_text:
                        missing_alt += 1
                if shape.has_table:
                    slide_tables += 1
                    table = shape.table
                    if len(table.rows) == 0:
                        tables_missing_headers.append({"slide": idx, "table": slide_tables})
                    else:
                        first_row = table.rows[0]
                        header_text = [(cell.text or "").strip() for cell in first_row.cells]
                        if not any(header_text):
                            tables_missing_headers.append({"slide": idx, "table": slide_tables})
                        else:
                            normalized_headers = [text.lower() for text in header_text if text]
                            unique_headers = set(normalized_headers)
                            if len(header_text) > 1 and len(unique_headers) <= 1:
                                table_header_scope_flags.append(
                                    {
                                        "slide": idx,
                                        "table": slide_tables,
                                        "reason": "duplicate_or_single_header_label",
                                        "headers": header_text[:10],
                                    }
                                )
                            elif any(text.lower() in generic_headers for text in header_text if text):
                                table_header_scope_flags.append(
                                    {
                                        "slide": idx,
                                        "table": slide_tables,
                                        "reason": "generic_header_labels",
                                        "headers": header_text[:10],
                                    }
                                )
                link_text = (getattr(shape, "text", None) or "").strip()
                shape_link = None
                try:
                    shape_link = getattr(shape.click_action.hyperlink, "address", None)
                except Exception:
                    shape_link = None
                if shape_link:
                    hyperlink_count += 1
                    if link_text and link_text.lower().strip() in generic_link_labels:
                        generic_links.append({"slide": idx, "text": link_text})
                    invalid_reason = _invalid_link_reason(str(shape_link))
                    if invalid_reason:
                        invalid_links.append(
                            {"slide": idx, "text": link_text or "(shape link)", "target": str(shape_link), "reason": invalid_reason}
                        )
                if hasattr(shape, "text_frame") and shape.text_frame:
                    for paragraph in shape.text_frame.paragraphs:
                        for run in paragraph.runs:
                            run_link = getattr(getattr(run, "hyperlink", None), "address", None)
                            if not run_link:
                                continue
                            hyperlink_count += 1
                            run_text = (run.text or "").strip()
                            if run_text and run_text.lower().strip() in generic_link_labels:
                                generic_links.append({"slide": idx, "text": run_text})
                            invalid_reason = _invalid_link_reason(str(run_link))
                            if invalid_reason:
                                invalid_links.append(
                                    {"slide": idx, "text": run_text or "(run link)", "target": str(run_link), "reason": invalid_reason}
                                )
            total_images += slide_images
            total_tables += slide_tables
            if len(shape_positions) > 2:
                disorder = 0
                for i in range(1, len(shape_positions)):
                    if shape_positions[i][0] < shape_positions[i - 1][0]:
                        disorder += 1
                if disorder > 0:
                    reading_order_warnings.append({"slide": idx, "disorderCount": disorder})
            slide_details.append(
                {
                    "slide": idx,
                    "title": title,
                    "images": slide_images,
                    "tables": slide_tables,
                }
            )

        title = (prs.core_properties.title or "").strip()
        language = (getattr(prs.core_properties, "language", None) or "").strip()
        headings = [{"level": 1, "text": s["title"], "slide": s["slide"]} for s in slide_details if s["title"]]
        return {
            "documentType": "pptx",
            "title": title,
            "language": language,
            "slideCount": len(prs.slides),
            "slides": slide_details,
            "slidesMissingTitles": slides_missing_titles,
            "headings": headings,
            "imageCount": total_images,
            "missingAltCount": missing_alt,
            "hyperlinkCount": hyperlink_count,
            "genericLinks": generic_links,
            "invalidLinks": invalid_links,
            "tables": total_tables,
            "tablesMissingHeaders": tables_missing_headers,
            "tableHeaderScopeFlags": table_header_scope_flags,
            "readingOrderWarnings": reading_order_warnings,
        }


    def parse_to_tree(self, file_path: str) -> ParserResult:
        path = Path(file_path)
        prs = Presentation(file_path)
        title = (prs.core_properties.title or "").strip()
        language = (getattr(prs.core_properties, "language", None) or "").strip()
        properties: Dict[str, Any] = {"filename": path.name}
        if title:
            properties["title"] = title
        root = DocumentNode(
            id="doc-1",
            content=NodeContent(kind=ContentKind.NONE),
            metadata=NodeMetadata(
                language=language or None,
                source_format="pptx",
                properties=properties,
            ),
            children=[],
            accessibility_flags=[],
        )
        ids = _IdCounter()

        for slide_index, slide in enumerate(prs.slides, start=1):
            slide_title = ""
            try:
                if slide.shapes.title and slide.shapes.title.text:
                    slide_title = slide.shapes.title.text.strip()
            except Exception:
                slide_title = ""

            section = SectionNode(
                id=ids(f"slide-{slide_index}-section"),
                content=NodeContent(kind=ContentKind.TEXT, text=slide_title or f"Slide {slide_index}"),
                metadata=NodeMetadata(
                    page=slide_index,
                    source_format="pptx",
                    properties={"slide_number": slide_index},
                ),
                children=[],
                accessibility_flags=[],
            )
            root.children.append(section)

            if slide_title:
                section.children.append(
                    HeadingNode(
                        id=ids(f"slide-{slide_index}-h"),
                        level=1,
                        content=NodeContent(kind=ContentKind.TEXT, text=slide_title),
                        metadata=NodeMetadata(page=slide_index, source_format="pptx"),
                        children=[],
                        accessibility_flags=[],
                    )
                )

            for shape in slide.shapes:
                top = float(getattr(shape, "top", 0) or 0)
                left = float(getattr(shape, "left", 0) or 0)
                shape_meta_props = {"order_hint": (top, left)}
                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    section.children.append(_picture_to_image_node(shape, slide_index, ids, shape_meta_props))
                    continue
                if shape.has_table:
                    section.children.append(_table_to_node_pptx(shape.table, slide_index, ids))
                    continue
                if hasattr(shape, "text_frame") and shape.text_frame:
                    text = (shape.text or "").strip()
                    if not text:
                        continue
                    section.children.append(
                        ParagraphNode(
                            id=ids(f"slide-{slide_index}-p"),
                            content=NodeContent(kind=ContentKind.TEXT, text=text),
                            metadata=NodeMetadata(
                                page=slide_index,
                                source_format="pptx",
                                properties=shape_meta_props,
                            ),
                            children=[],
                            accessibility_flags=[],
                        )
                    )
                    # Hyperlinks within runs get their own LinkNode.
                    for paragraph in shape.text_frame.paragraphs:
                        for run in paragraph.runs:
                            try:
                                href = run.hyperlink.address
                            except Exception:
                                href = None
                            if not href:
                                continue
                            section.children.append(
                                LinkNode(
                                    id=ids(f"slide-{slide_index}-link"),
                                    target=str(href),
                                    content=NodeContent(kind=ContentKind.TEXT, text=(run.text or "").strip() or "link"),
                                    metadata=NodeMetadata(page=slide_index, source_format="pptx"),
                                    children=[],
                                    accessibility_flags=[],
                                )
                            )

        raw_metadata = {
            "filename": path.name,
            "title": title,
            "language": language,
            "slide_count": len(prs.slides),
        }
        return ParserResult(
            document_id=path.stem or "doc",
            format="pptx",
            tree=AccessibilityTree(root=root, metadata=raw_metadata),
            raw_metadata=raw_metadata,
        )


# ----- PPTX → AccessibilityTree helpers --------------------------------------


class _IdCounter:
    def __init__(self) -> None:
        self._counts: Dict[str, int] = {}

    def __call__(self, prefix: str) -> str:
        i = self._counts.get(prefix, 0) + 1
        self._counts[prefix] = i
        return f"{prefix}-{i}"


def _picture_to_image_node(shape, slide_index: int, ids: "_IdCounter", extra_props: Dict[str, Any]) -> ImageNode:
    alt_text = (getattr(shape, "alternative_text", None) or "").strip()
    is_decorative = False
    image_b64: Optional[str] = None
    image_mime: Optional[str] = None
    try:
        blob = shape.image.blob
        if blob and len(blob) <= 2_000_000:
            image_b64 = base64.b64encode(blob).decode("ascii")
            content_type = getattr(shape.image, "content_type", None)
            image_mime = str(content_type) if content_type else "image/png"
    except Exception:
        pass

    properties: Dict[str, Any] = dict(extra_props)
    if image_b64:
        properties["image_b64"] = image_b64
        properties["image_mime"] = image_mime or "image/png"
    properties["shape_id"] = getattr(shape, "shape_id", None)
    metadata = NodeMetadata(page=slide_index, source_format="pptx", properties=properties)
    if is_decorative and alt_text:
        return ImageNode.model_construct(
            id=ids(f"slide-{slide_index}-img"),
            node_type=ImageNode.type_value(),
            content=NodeContent(kind=ContentKind.NONE),
            metadata=metadata,
            children=[],
            accessibility_flags=[],
            is_decorative=True,
            alt_text=alt_text,
        )
    return ImageNode(
        id=ids(f"slide-{slide_index}-img"),
        content=NodeContent(kind=ContentKind.NONE),
        metadata=metadata,
        children=[],
        accessibility_flags=[],
        is_decorative=is_decorative,
        alt_text=alt_text or None,
    )


def _table_to_node_pptx(table, slide_index: int, ids: "_IdCounter") -> TableNode:
    rows: List[TableRowNode] = []
    row_objects = list(table.rows)
    for row_index, row in enumerate(row_objects):
        cells: List[TableCellNode] = []
        for cell in row.cells:
            text = (cell.text or "").strip()
            cell_type = TableCellType.HEADER if row_index == 0 and text else TableCellType.DATA
            cells.append(
                TableCellNode(
                    id=ids(f"slide-{slide_index}-cell"),
                    cell_type=cell_type,
                    header_scope=TableHeaderScope.COLUMN if cell_type == TableCellType.HEADER else TableHeaderScope.NONE,
                    content=NodeContent(kind=ContentKind.TEXT, text=text or " "),
                    metadata=NodeMetadata(page=slide_index, source_format="pptx"),
                    children=[],
                    accessibility_flags=[],
                )
            )
        rows.append(
            TableRowNode(
                id=ids(f"slide-{slide_index}-row"),
                content=NodeContent(kind=ContentKind.NONE),
                metadata=NodeMetadata(page=slide_index, source_format="pptx"),
                children=cells,
                accessibility_flags=[],
            )
        )
    return TableNode(
        id=ids(f"slide-{slide_index}-table"),
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(page=slide_index, source_format="pptx"),
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
