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
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from docx import Document
from docx.oxml.ns import qn
from lxml import etree

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
        theme_colors = _docx_theme_colors(file_path)
        core = doc.core_properties

        title = (core.title or "").strip()
        language = (getattr(core, "language", None) or "").strip()
        properties: Dict[str, Any] = {"filename": path.name}
        if title:
            properties["title"] = title
        ff_total, ff_unlabeled = _docx_form_field_counts(doc)
        if ff_total:
            properties["form_fields_total"] = ff_total
            properties["form_fields_unlabeled"] = ff_unlabeled
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
                        metadata=NodeMetadata(source_format="docx", properties=_text_color_props(paragraph, theme_colors)),
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
                        metadata=NodeMetadata(source_format="docx", properties=_text_color_props(paragraph, theme_colors)),
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
                para_props = _text_color_props(paragraph, theme_colors)
                if _looks_like_fake_heading(paragraph, style_name, text):
                    # Visually a heading (Title/Subtitle style, or short
                    # all-bold large text) but NOT a real Heading style —
                    # invisible to screen-reader navigation. Flagged by
                    # TextStyledAsHeadingAnalyzer.
                    para_props = dict(para_props or {})
                    para_props["looks_like_heading"] = True
                fake_sig = _fake_list_signature(text)
                if fake_sig:
                    kind, char, ordinal = fake_sig
                    para_props = dict(para_props or {})
                    para_props["fake_list_kind"] = kind
                    para_props["fake_list_char"] = char
                    if ordinal is not None:
                        para_props["fake_list_ordinal"] = ordinal
                body_section.children.append(
                    ParagraphNode(
                        id=ids("docx-p"),
                        content=NodeContent(kind=ContentKind.TEXT, text=text),
                        metadata=NodeMetadata(source_format="docx", properties=para_props),
                        children=[],
                        accessibility_flags=[],
                    )
                )

        if list_collector:
            body_section.children.append(_finalize_list(ids, list_collector, list_marker))

        # Group consecutive fake-list paragraphs into runs (one flag each).
        _group_fake_list_runs(body_section.children)

        # Text boxes: w:txbxContent is invisible to doc.paragraphs, so
        # sidebar/callout content (very common in government documents) would
        # otherwise never be analyzed. Paragraphs mint a distinct docx-tbp /
        # docx-tblink id space so the body's docx-p / docx-link counters (and
        # the writer's id-alignment) are untouched. Links here ARE remediable
        # — the writer indexes docx-tblink ids through the same shared walk.
        for tb_p in _iter_text_box_paragraphs(doc.element.body):
            tb_links = _link_nodes_from_p(
                tb_p, doc.part, ids, prefix="docx-tblink", extra_props={"in_text_box": True}
            )
            body_section.children.extend(tb_links)
            tb_text = "".join(
                (t.text or "") for t in tb_p.iterfind(f".//{_DOCX_NS}t")
            ).strip()
            if tb_text and not tb_links:
                body_section.children.append(
                    ParagraphNode(
                        id=ids("docx-tbp"),
                        content=NodeContent(kind=ContentKind.TEXT, text=tb_text),
                        metadata=NodeMetadata(
                            source_format="docx",
                            properties={"in_text_box": True},
                        ),
                        children=[],
                        accessibility_flags=[],
                    )
                )

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


# w:themeColor attribute value -> clrScheme element name.
_THEME_COLOR_MAP = {
    "dark1": "dk1", "text1": "dk1",
    "light1": "lt1", "background1": "lt1",
    "dark2": "dk2", "text2": "dk2",
    "light2": "lt2", "background2": "lt2",
    "accent1": "accent1", "accent2": "accent2", "accent3": "accent3",
    "accent4": "accent4", "accent5": "accent5", "accent6": "accent6",
    "hyperlink": "hlink", "followedHyperlink": "folHlink",
}


def _docx_theme_colors(file_path: str) -> Dict[str, str]:
    """Map theme colour-scheme names (dk1, lt1, accent1…) to RGB hex."""
    out: Dict[str, str] = {}
    try:
        with zipfile.ZipFile(file_path) as z:
            names = [n for n in z.namelist() if n.startswith("word/theme/theme") and n.endswith(".xml")]
            if not names:
                return {}
            xml = z.read(sorted(names)[0])
        root = etree.fromstring(xml)
        a = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
        scheme = root.find(f".//{a}clrScheme")
        if scheme is None:
            return {}
        for child in scheme:
            name = etree.QName(child).localname
            srgb = child.find(f"{a}srgbClr")
            sysclr = child.find(f"{a}sysClr")
            if srgb is not None and srgb.get("val"):
                out[name] = srgb.get("val").upper()
            elif sysclr is not None and sysclr.get("lastClr"):
                out[name] = sysclr.get("lastClr").upper()
    except Exception:
        return {}
    return out


def _apply_tint_shade(hex_color: str, tint_hex: Optional[str], shade_hex: Optional[str]) -> str:
    """Apply WordprocessingML w:themeTint / w:themeShade to a base RGB hex.

    themeTint blends toward white (keep ``tint/255`` of the colour); themeShade
    blends toward black (multiply by ``shade/255``).
    """
    try:
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
    except (ValueError, IndexError):
        return hex_color
    if tint_hex:
        try:
            t = int(tint_hex, 16) / 255.0
            r = round(r * t + 255 * (1 - t)); g = round(g * t + 255 * (1 - t)); b = round(b * t + 255 * (1 - t))
        except ValueError:
            pass
    if shade_hex:
        try:
            s = int(shade_hex, 16) / 255.0
            r = round(r * s); g = round(g * s); b = round(b * s)
        except ValueError:
            pass
    return f"{max(0, min(255, r)):02X}{max(0, min(255, g)):02X}{max(0, min(255, b)):02X}"


def _run_color_hex(run, theme_colors: Dict[str, str]) -> Optional[str]:
    """The run's effective text colour as RGB hex: explicit sRGB, or a resolved
    theme colour (with tint/shade). ``None`` for auto/inherited/unknown."""
    try:
        rgb = run.font.color.rgb  # RGBColor only when explicitly sRGB
    except Exception:
        rgb = None
    if rgb is not None:
        return str(rgb)
    # Theme colour via the underlying w:color element.
    try:
        color_el = run._element.find(f".//{_DOCX_NS}rPr/{_DOCX_NS}color")
        if color_el is None:
            return None
        tc = color_el.get(f"{_DOCX_NS}themeColor")
        if not tc or not theme_colors:
            return None
        base = theme_colors.get(_THEME_COLOR_MAP.get(tc, tc))
        if not base:
            return None
        return _apply_tint_shade(
            base,
            color_el.get(f"{_DOCX_NS}themeTint"),
            color_el.get(f"{_DOCX_NS}themeShade"),
        )
    except Exception:
        return None


def _explicit_run_colors(paragraph, theme_colors: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Per-run text colours (for contrast analysis): explicit sRGB *and* resolved
    theme colours. Auto/inherited/unknown colours are skipped, never guessed."""
    theme_colors = theme_colors or {}
    out: List[Dict[str, Any]] = []
    for run in getattr(paragraph, "runs", []) or []:
        if not (run.text or "").strip():
            continue
        hex6 = _run_color_hex(run, theme_colors)
        if not hex6:
            continue
        size_pt = None
        try:
            if run.font.size is not None:
                size_pt = float(run.font.size.pt)
        except Exception:
            size_pt = None
        out.append({"c": hex6, "sz": size_pt, "b": bool(run.font.bold) if run.font.bold is not None else False})
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


def _text_color_props(paragraph, theme_colors: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Build the ``metadata.properties`` carrying contrast inputs (or empty)."""
    colors = _explicit_run_colors(paragraph, theme_colors)
    if not colors:
        return {}
    props: Dict[str, Any] = {"explicit_text_colors": colors}
    bg = _paragraph_bg(paragraph)
    if bg:
        props["bg_color"] = bg
    return props


def _docx_form_field_counts(doc) -> "tuple[int, int]":
    """Return ``(total, unlabeled)`` content controls (``w:sdt``).

    A content control's accessible label is its ``w:alias`` (the title). One
    with no alias has no accessible name. Reuses the same root-metadata keys as
    the PDF AcroForm check so a single analyzer flags both.
    """
    total = 0
    unlabeled = 0
    try:
        body = doc.element.body
        for sdt in body.iter(f"{_DOCX_NS}sdt"):
            total += 1
            alias = sdt.find(f"{_DOCX_NS}sdtPr/{_DOCX_NS}alias")
            val = alias.get(f"{_DOCX_NS}val") if alias is not None else None
            if not val or not str(val).strip():
                unlabeled += 1
    except Exception:
        return (total, unlabeled)
    return (total, unlabeled)
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


_FLD_HYPERLINK_RE = re.compile(r"HYPERLINK\s+(?:\"([^\"]+)\"|(\S+))")


def _link_elements_in_paragraph(p) -> List[Tuple[str, Any]]:
    """Return ``[(kind, element), …]`` for every link the parser emits from a
    ``<w:p>``: ``w:hyperlink`` with visible text, plus HYPERLINK
    ``w:fldSimple`` fields with visible text — in document order.

    This is the single source of truth for "what counts as a link"; the docx
    writer indexes links (and decides which paragraphs consumed a ``docx-p``
    id) through this same helper, so parser/writer id minting cannot drift.
    """

    out: List[Tuple[str, Any]] = []
    for el in p.iter(f"{_DOCX_NS}hyperlink", f"{_DOCX_NS}fldSimple"):
        # A link nested inside a TEXT BOX below this paragraph belongs to the
        # text-box walk (docx-tblink id space) — emitting it here too would
        # double-count it and desync both id spaces. (When ``p`` itself lives
        # inside a text box, the ancestor walk stops at ``p`` before reaching
        # the txbxContent above it, so text-box paragraphs still match.)
        anc = el.getparent()
        nested_in_tb = False
        while anc is not None and anc is not p:
            if anc.tag == f"{_DOCX_NS}txbxContent":
                nested_in_tb = True
                break
            anc = anc.getparent()
        if nested_in_tb:
            continue
        text = "".join(
            (t.text or "") for t in el.iterfind(f".//{_DOCX_NS}t")
        ).strip()
        if not text:
            continue
        if el.tag == f"{_DOCX_NS}hyperlink":
            out.append(("hyperlink", el))
        else:
            instr = el.get(f"{_DOCX_NS}instr") or ""
            if "HYPERLINK" in instr:
                out.append(("fldsimple", el))
    return out


def _link_nodes_from_p(p_el, part, ids: _IdCounter, prefix: str = "docx-link", extra_props: Optional[Dict[str, Any]] = None) -> List[LinkNode]:
    """Build LinkNodes from a raw ``<w:p>`` element (body, cell or text box)."""
    nodes: List[LinkNode] = []
    for kind, el in _link_elements_in_paragraph(p_el):
        text = "".join(
            (t.text or "") for t in el.iterfind(f".//{_DOCX_NS}t")
        ).strip()
        if kind == "hyperlink":
            rid = el.get(f"{_DOCX_REL_NS}id")
            target = ""
            if rid and rid in part.rels:
                target = str(part.rels[rid].target_ref or "")
        else:
            m = _FLD_HYPERLINK_RE.search(el.get(f"{_DOCX_NS}instr") or "")
            target = (m.group(1) or m.group(2)) if m else ""
        props: Dict[str, Any] = {"link_kind": kind}
        if extra_props:
            props.update(extra_props)
        nodes.append(
            LinkNode(
                id=ids(prefix),
                target=target or None,
                content=NodeContent(kind=ContentKind.TEXT, text=text),
                metadata=NodeMetadata(source_format="docx", properties=props),
                children=[],
                accessibility_flags=[],
            )
        )
    return nodes


def _hyperlink_nodes_in_paragraph(paragraph, ids: _IdCounter) -> List[LinkNode]:
    return _link_nodes_from_p(paragraph._p, paragraph.part, ids)


def _iter_text_box_paragraphs(body_el) -> List[Any]:
    """All ``<w:p>`` elements living inside text boxes (``w:txbxContent``),
    in document order. Text boxes are invisible to ``doc.paragraphs`` —
    sidebars and callouts would otherwise never be analyzed. Shared with the
    docx writer so text-box link ids (``docx-tblink-N``) mint identically."""
    out: List[Any] = []
    for tx in body_el.iter(f"{_DOCX_NS}txbxContent"):
        out.extend(tx.iterfind(f"{_DOCX_NS}p"))
    return out


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


# ----- Fake-list detection ----------------------------------------------------
#
# Plain paragraphs typed as "- item" / "1. item" with no Word numbering are
# read by screen readers as disconnected prose — no list semantics, no item
# count, no nesting.  The parser marks candidates here; a post-pass groups
# consecutive candidates into runs; ListStructureAnalyzer flags each run; and
# the FIX_LIST_STRUCTURE executor + docx writer convert them into REAL Word
# lists (w:numPr + numbering.xml).  Precision over recall: en/em dashes are
# excluded (dialogue), alpha ordinals are excluded ("A. Smith"), and numbered
# runs must count 1, 2, 3… from 1.

_FAKE_BULLET_RE = re.compile(r"^([-*•·])\s+\S")
_FAKE_DECIMAL_RE = re.compile(r"^(\d{1,3})[.)]\s+\S")


def _fake_list_signature(text: str) -> Optional[Tuple[str, str, Optional[int]]]:
    """Return ``(kind, prefix_char, ordinal)`` when ``text`` is typed like a
    list item, else None. ``kind`` is "bullet" or "decimal"."""

    m = _FAKE_BULLET_RE.match(text)
    if m:
        return ("bullet", m.group(1), None)
    m = _FAKE_DECIMAL_RE.match(text)
    if m:
        return ("decimal", m.group(1), int(m.group(1)))
    return None


def strip_fake_list_prefix(text: str) -> str:
    """Remove the literal typed marker ("- ", "1. ", "2) "…) from ``text``.

    Shared with the executor and the docx writer so the three stay in
    lockstep about what counts as a marker.
    """

    out = re.sub(r"^[-*•·]\s+", "", text, count=1)
    if out != text:
        return out
    return re.sub(r"^\d{1,3}[.)]\s+", "", text, count=1)


def _group_fake_list_runs(body_children: List[Any]) -> None:
    """Mark runs of >=2 consecutive same-kind fake-list ParagraphNodes.

    The FIRST node of each run gets ``fake_list_run_ids`` (all member ids,
    itself included) — the analyzer flags that node, so one issue surfaces
    per typed list. Numbered runs must be sequential starting at 1, which
    keeps prose like "1986. It was…" or stray numbered sentences out.
    """

    run: List[Any] = []
    run_kind: Optional[str] = None
    run_char: Optional[str] = None
    next_ordinal: Optional[int] = None

    def flush() -> None:
        nonlocal run, run_kind, run_char, next_ordinal
        if len(run) >= 2:
            first = run[0]
            props = dict(first.metadata.properties or {})
            props["fake_list_run_ids"] = [n.id for n in run]
            first.metadata.properties = props
        run = []
        run_kind = None
        run_char = None
        next_ordinal = None

    for child in body_children:
        sig = None
        if isinstance(child, ParagraphNode):
            props = child.metadata.properties or {}
            kind = props.get("fake_list_kind")
            if kind:
                sig = (kind, props.get("fake_list_char"), props.get("fake_list_ordinal"))
        if sig is None:
            flush()
            continue
        kind, char, ordinal = sig
        if run and (kind != run_kind or (kind == "bullet" and char != run_char)):
            flush()
        if kind == "decimal":
            expected = 1 if not run else next_ordinal
            if ordinal != expected:
                flush()
                if ordinal != 1:
                    continue  # mid-sequence stray ("1986. …") — not a list start
            next_ordinal = (ordinal or 0) + 1
        if not run:
            run_kind, run_char = kind, char
        run.append(child)
    flush()


def _looks_like_fake_heading(paragraph, style_name: str, text: str) -> bool:
    """True when a plain paragraph is visually presented as a heading.

    The classic title-page failure: 24pt bold text typed as a normal paragraph
    (or Word's Title/Subtitle styles, which are NOT Heading 1-9 and don't enter
    the navigation outline). Conservative on purpose:

    * Title / Subtitle styles always count — they're unambiguous.
    * Otherwise the text must be SHORT (<= 60 chars, <= 8 words), not end like
      a sentence, have every visible run bold, AND carry an explicit font size
      >= 14pt on some run. Ordinary bold emphasis inside body text fails the
      size requirement; bold labels fail nothing else often enough that the
      size requirement is what keeps precision high.
    """
    sn = (style_name or "").strip().lower()
    if sn in ("title", "subtitle"):
        return True

    t = (text or "").strip()
    if not t or len(t) > 60 or len(t.split()) > 8:
        return False
    if t.endswith((".", "!", "?", ";", ":", ",")):
        return False

    saw_text_run = False
    max_size_pt = 0.0
    for run in paragraph.runs:
        if not (run.text or "").strip():
            continue
        saw_text_run = True
        if not run.bold:
            return False
        try:
            if run.font.size is not None:
                max_size_pt = max(max_size_pt, float(run.font.size.pt))
        except Exception:
            pass
    return saw_text_run and max_size_pt >= 14.0


def _cell_text_is_bold(cell) -> bool:
    """True when the cell has visible text and every run carrying it is bold —
    the common 'visual header' convention (a bold first row)."""
    saw_text = False
    for para in cell.paragraphs:
        for run in para.runs:
            if (run.text or "").strip():
                saw_text = True
                if not run.bold:
                    return False
    return saw_text


def _docx_row_is_header(row) -> bool:
    """A row is a genuine header row when Word marks it as one (``w:tblHeader``,
    the *Repeat as header row* property) or every populated cell is bold."""
    trPr = row._tr.find(qn("w:trPr"))
    if trPr is not None:
        th = trPr.find(qn("w:tblHeader"))
        if th is not None and th.get(qn("w:val")) not in ("0", "false", "off"):
            return True
    populated = [c for c in row.cells if (c.text or "").strip()]
    return bool(populated) and all(_cell_text_is_bold(c) for c in populated)


def _table_to_node(table, ids: _IdCounter) -> TableNode:
    # Decide once whether row 0 is a header. Word marks real headers with
    # w:tblHeader or styles them bold; if neither AND the table is a clear data
    # grid (>=3 rows, >=2 cols), type row 0 as DATA so TABLE_MISSING_HEADERS can
    # fire. Small/ambiguous tables keep the legacy header assumption to avoid
    # false positives on layout tables.
    all_rows = list(table.rows)
    n_cols = max((len(r.cells) for r in all_rows), default=0)
    looks_like_data_table = len(all_rows) >= 3 and n_cols >= 2
    first_is_header = _docx_row_is_header(all_rows[0]) if all_rows else False
    treat_row0_as_header = first_is_header or not looks_like_data_table

    # Merged cells repeat the same underlying <w:tc> across the grid; links
    # inside it must only be emitted once (the writer dedupes identically).
    seen_tc_ids: set = set()

    rows: List[TableRowNode] = []
    for row_index, row in enumerate(table.rows):
        cells: List[TableCellNode] = []
        for cell in row.cells:
            text = (cell.text or "").strip()
            is_header_cell = row_index == 0 and bool(text) and treat_row0_as_header
            cell_type = TableCellType.HEADER if is_header_cell else TableCellType.DATA
            cell_children: List[Any] = []
            tc_key = id(cell._tc)
            if tc_key not in seen_tc_ids:
                seen_tc_ids.add(tc_key)
                # Hyperlinks (incl. fldSimple fields) inside the cell get their
                # own LinkNodes so link-text analysis/remediation reaches them.
                for cell_paragraph in cell.paragraphs:
                    cell_children.extend(_hyperlink_nodes_in_paragraph(cell_paragraph, ids))
            cells.append(
                TableCellNode(
                    id=ids("docx-cell"),
                    cell_type=cell_type,
                    header_scope=TableHeaderScope.COLUMN if cell_type == TableCellType.HEADER else TableHeaderScope.NONE,
                    content=NodeContent(kind=ContentKind.TEXT, text=text or " "),
                    metadata=NodeMetadata(source_format="docx"),
                    children=cell_children,
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
