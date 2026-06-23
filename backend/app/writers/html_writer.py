"""Writer that materializes :class:`AccessibilityTree` mutations back into an
HTML file.

Two-phase design (the important correctness property)
-----------------------------------------------------
1. RESOLVE every node's stored ``__xpath`` against the *pristine* re-parsed DOM
   and hold the live lxml element references.
2. MUTATE via those held references.

This ordering matters: lxml's ``getpath()`` produces TAG-INDEXED paths
(``td[1]``, ``td[2]``, ``h3[1]`` …). The moment we rename one element's tag
(``<td>``→``<th>``, ``<h3>``→``<h2>``) or insert a sibling, the positional index
of later same-tag siblings shifts, so re-resolving their xpath *after* a mutation
would silently fail. Resolving everything up front — before any mutation — and
then editing through the held references (which survive retag / attribute / sibling
changes) avoids that entirely. That keeps the HONESTY INVARIANT intact: every
action the executor succeeds on is actually persisted to the bytes, so the
score in pipeline._build_score (which credits executor-success ∩ _PERSISTED_ACTIONS)
never overclaims.

Honesty: the set of actions credited for HTML lives in
``pipeline._PERSISTED_ACTIONS["html"]`` = {SET_DOCUMENT_TITLE,
SET_DOCUMENT_LANGUAGE, GENERATE_ALT_TEXT, NORMALIZE_HEADING_LEVEL,
IMPROVE_LINK_TEXT, ADD_TABLE_HEADERS}. Every one is persisted here. A locator
that unexpectedly fails to resolve is logged and recorded in ``skipped`` (it
should never happen for parser-produced nodes on a same-bytes re-parse).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from lxml import etree
from lxml import html as lxml_html

from app.models.accessibility import (
    AccessibilityTree,
    ContentKind,
    HeadingNode,
    ImageNode,
    LinkNode,
    TableCellNode,
    TableCellType,
    TableHeaderScope,
    TableNode,
    TableRowNode,
    iter_reading_order,
)
from app.parsers.html_parser import _parse_document

logger = logging.getLogger(__name__)

_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_SCOPE_ATTR = {
    TableHeaderScope.COLUMN: "col",
    TableHeaderScope.ROW: "row",
}
# Nodes the writer may need a source element for.
_LOCATABLE = (ImageNode, HeadingNode, LinkNode, TableNode, TableCellNode)


def write_remediated_html(
    source_path: Path,
    tree: AccessibilityTree,
    output_path: Path,
) -> Dict[str, Any]:
    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    # Always leave a deliverable copy in place first (writers must never edit
    # the source). If anything below fails we still have the original bytes.
    try:
        shutil.copyfile(source_path, output_path)
    except Exception as exc:  # pragma: no cover - filesystem failure
        logger.exception("html_writer copy failed: %s", exc)
        return {"applied": [], "skipped": [{"target_id": str(source_path), "reason": "copy_failed"}]}

    try:
        data = Path(source_path).read_bytes()
        doc = _parse_document(data)
    except Exception as exc:
        logger.exception("html_writer parse failed: %s", exc)
        # Signal a hard failure so the pipeline cleans up and does NOT charge.
        return {"applied": [], "skipped": [{"target_id": str(source_path), "reason": "failed_to_open: html"}]}

    def resolve(xpath: Optional[str]) -> Optional[Any]:
        if not xpath:
            return None
        try:
            found = doc.xpath(xpath)
        except Exception:
            return None
        return found[0] if len(found) == 1 else None

    root = tree.root
    props = root.metadata.properties or {}

    # ---- PHASE 1: resolve all locators on the PRISTINE DOM (before mutating) ----
    elements: Dict[str, Any] = {}
    for node in iter_reading_order(root):
        if not isinstance(node, _LOCATABLE):
            continue
        nprops = node.metadata.properties or {}
        if nprops.get("synthesized"):
            continue  # synthetic nodes have no source element by design
        xpath = nprops.get("__xpath")
        el = resolve(xpath)
        elements[node.id] = el
        if el is None and xpath:
            logger.warning("html_writer: could not resolve %s for node %s", xpath, node.id)
            skipped.append({"target_id": node.id, "reason": "element_not_resolved"})

    # Contrast fixes can target any text node — paragraphs and list items are
    # not in _LOCATABLE — so resolve those separately on the pristine DOM. The
    # executor sets contrast_fix_fg only on nodes whose recolour was approved.
    contrast_els: Dict[str, Any] = {}
    for node in iter_reading_order(root):
        nprops = node.metadata.properties or {}
        if not nprops.get("contrast_fix_fg") or nprops.get("synthesized"):
            continue
        el = elements.get(node.id)
        contrast_els[node.id] = el if el is not None else resolve(nprops.get("__xpath"))

    html_el = resolve(props.get("__html_xpath"))
    if html_el is None:
        html_el = doc
    title_el = resolve(props.get("__title_xpath"))

    # ---- PHASE 2: mutate via the held references ----
    language = root.metadata.language
    if isinstance(language, str) and language.strip() and html_el is not None:
        if (html_el.get("lang") or None) != language:
            html_el.set("lang", language)
            applied.append({"action": "SET_DOCUMENT_LANGUAGE", "target_id": root.id})

    title = props.get("title")
    if isinstance(title, str) and title.strip():
        if title_el is None:
            title_el = _ensure_title_element(doc)
        if title_el is not None and (title_el.text or "") != title:
            title_el.text = title
            applied.append({"action": "SET_DOCUMENT_TITLE", "target_id": root.id})

    for node in iter_reading_order(root):
        if isinstance(node, ImageNode):
            _apply_image(node, elements.get(node.id), applied)
        elif isinstance(node, HeadingNode):
            _apply_heading(node, elements.get(node.id), applied)
        elif isinstance(node, LinkNode):
            _apply_link(node, elements.get(node.id), applied)
        elif isinstance(node, TableNode):
            _apply_table(node, elements, applied)
        # TableRow/TableCell handled inside _apply_table.

    # Contrast recolour: write the analyzer's AA-passing colour as an inline
    # style (inline wins over inherited/class colours) on each approved node.
    for node in iter_reading_order(root):
        nprops = node.metadata.properties or {}
        fg = nprops.get("contrast_fix_fg")
        if not fg:
            continue
        el = contrast_els.get(node.id)
        if el is None:
            # Record the miss so the caller never credits/charges a recolour
            # that did not reach the output bytes (honesty invariant).
            skipped.append({"target_id": node.id, "reason": "contrast_element_not_resolved"})
            continue
        if _apply_contrast(el, fg):
            applied.append({"action": "FIX_CONTRAST", "target_id": node.id})

    try:
        out_bytes = _serialize(doc)
        Path(output_path).write_bytes(out_bytes)
    except Exception as exc:
        logger.exception("html_writer serialize failed: %s", exc)
        return {"applied": [], "skipped": [{"target_id": str(source_path), "reason": "failed_to_open: serialize"}]}

    return {"applied": applied, "skipped": skipped}


def _apply_contrast(el: Any, fg: str) -> bool:
    """Set/replace the element's inline ``color`` with ``#fg``.

    Writing the colour inline guarantees it wins over an inherited or class
    colour (inline is the highest-specificity non-!important source), so the fix
    is effective even when the failing colour came from a parent or a stylesheet.
    """
    new_color = "#" + str(fg).lstrip("#")
    raw = el.get("style") or ""
    out: List[str] = []
    replaced = False
    for decl in (d.strip() for d in raw.split(";") if d.strip()):
        prop, sep, _val = decl.partition(":")
        if sep and prop.strip().lower() == "color":
            # Preserve an !important flag so the recolour keeps the same cascade
            # weight the failing colour had (else a stylesheet could re-override
            # it in the browser even though our re-parse sees the fixed colour).
            important = " !important" if "!important" in _val.lower() else ""
            out.append(f"color: {new_color}{important}")
            replaced = True
        else:
            out.append(decl)
    if not replaced:
        out.append(f"color: {new_color}")
    el.set("style", "; ".join(out))
    return True


def _apply_image(node: ImageNode, el: Any, applied: List[Dict[str, Any]]) -> None:
    # Only GENERATE_ALT_TEXT is a supported HTML image fix in v1. Decorative
    # images are left untouched (a properly empty alt is already correct, and we
    # never edit an image the user didn't approve a fix for).
    if node.is_decorative or not node.alt_text or el is None:
        return
    if el.get("alt") != node.alt_text:
        el.set("alt", node.alt_text)
        applied.append({"action": "GENERATE_ALT_TEXT", "target_id": node.id})


def _apply_heading(node: HeadingNode, el: Any, applied: List[Dict[str, Any]]) -> None:
    if el is None or not isinstance(el.tag, str):
        return
    current = el.tag.lower()
    want = f"h{node.level}"
    if current in _HEADING_TAGS and current != want:
        el.tag = want
        applied.append({"action": "NORMALIZE_HEADING_LEVEL", "target_id": node.id})


def _apply_link(node: LinkNode, el: Any, applied: List[Dict[str, Any]]) -> None:
    if node.content.kind != ContentKind.TEXT or not node.content.text or el is None:
        return
    # Parser only assigns TEXT content to links with no element children, so
    # replacing the text destroys nothing.
    if any(isinstance(c.tag, str) for c in el):
        return
    if (el.text_content() or "").strip() != node.content.text:
        el.text = node.content.text
        applied.append({"action": "IMPROVE_LINK_TEXT", "target_id": node.id})


def _apply_table(node: TableNode, elements: Dict[str, Any], applied: List[Dict[str, Any]]) -> None:
    table_el = elements.get(node.id)
    if table_el is None:
        return
    for row_node in node.children:
        if not isinstance(row_node, TableRowNode):
            continue
        if (row_node.metadata.properties or {}).get("synthesized"):
            if _insert_synthetic_header_row(table_el, row_node):
                applied.append({"action": "ADD_TABLE_HEADERS", "target_id": node.id})
            continue
        for cell_node in row_node.children:
            if not isinstance(cell_node, TableCellNode):
                continue
            if cell_node.cell_type != TableCellType.HEADER:
                continue
            cell_el = elements.get(cell_node.id)
            if cell_el is None or not isinstance(cell_el.tag, str):
                continue
            # Only PROMOTE an existing data cell (td -> th). Pre-existing <th>
            # cells are left untouched so we never modify what wasn't approved.
            if cell_el.tag.lower() == "td":
                cell_el.tag = "th"
                cell_el.set("scope", _SCOPE_ATTR.get(cell_node.header_scope, "col"))
                applied.append({"action": "ADD_TABLE_HEADERS", "target_id": node.id})


def _insert_synthetic_header_row(table_el: Any, row_node: TableRowNode) -> bool:
    tr = etree.Element("tr")
    for cell_node in row_node.children:
        if not isinstance(cell_node, TableCellNode):
            continue
        th = etree.SubElement(tr, "th")
        th.set("scope", "col")
        if cell_node.content and cell_node.content.kind == ContentKind.TEXT and cell_node.content.text:
            th.text = cell_node.content.text
    if len(tr) == 0:
        return False
    # Prefer placing the header row in a <thead> for correct table semantics.
    thead = table_el.find("thead")
    if thead is not None:
        thead.insert(0, tr)
        return True
    first_tr = next((c for c in table_el.iter("tr")), None)
    if first_tr is not None:
        # Create a real <thead> as the table's first child and put the row there.
        thead = etree.Element("thead")
        thead.append(tr)
        table_el.insert(0, thead)
        return True
    table_el.append(tr)
    return True


def _ensure_title_element(doc: Any) -> Optional[Any]:
    head = doc.find("head")
    if head is None:
        head = etree.Element("head")
        doc.insert(0, head)  # first child of <html>
    title_el = head.find("title")
    if title_el is None:
        title_el = etree.SubElement(head, "title")
    return title_el


def _serialize(doc: Any) -> bytes:
    # We always emit UTF-8 bytes, so any existing charset declaration must say
    # utf-8 or a browser will mis-decode a document we re-encoded. This only
    # rewrites an EXISTING meta (never injects one) — required for correctness,
    # not a gratuitous edit.
    for meta in doc.iter("meta"):
        if meta.get("charset") is not None:
            meta.set("charset", "utf-8")
        elif (meta.get("http-equiv") or "").lower() == "content-type":
            meta.set("content", "text/html; charset=utf-8")
    doctype = None
    try:
        doctype = doc.getroottree().docinfo.doctype or None
    except Exception:
        doctype = None
    return lxml_html.tostring(
        doc,
        encoding="utf-8",
        method="html",
        doctype=doctype,
        include_meta_content_type=False,
    )


__all__ = ["write_remediated_html"]
