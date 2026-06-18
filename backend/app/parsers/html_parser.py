"""HTML accessibility parser.

Parses an ``.html`` file into the shared :class:`AccessibilityTree` so the
existing (format-agnostic) analyzers fire on web pages, and records an xpath
locator on every node so :mod:`app.writers.html_writer` can find the exact
source element to edit.

Design notes
------------
* We parse with ``lxml.html`` (recovering), so malformed / partial HTML never
  crashes — the parser is best-effort and the analyzers grade what we found.
* Each node that the writer may edit carries
  ``metadata.properties["__xpath"]`` = ``getroottree().getpath(element)``. The
  writer re-parses the *same bytes* (deterministic) and resolves that xpath, so
  there is no fragile parser/writer id-counter to keep in lockstep — the locator
  is an absolute address into an identical tree.
* v1 is deliberately conservative about what it *claims*:
    - Contrast is NOT populated (we never assume colours from classes /
      stylesheets), so no LOW_CONTRAST_TEXT false positives.
    - Form fields are DETECTED (count) but ``form_fields_derivable`` is 0, so
      the labeling executor honestly skips — auto-labeling HTML controls
      correctly is deferred to v2 (a wrong label is worse than none).
    - Link text is only treated as analyzable when the ``<a>`` is pure text
      (no child elements), so the IMPROVE_LINK_TEXT writer can always safely
      replace it without destroying nested markup.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree
from lxml import html as lxml_html

logger = logging.getLogger(__name__)

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

_HEADING_TAGS: Dict[str, int] = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_SECTION_TAGS = {"section", "article", "main", "nav", "aside", "header", "footer", "div", "body"}
# Content we never descend into for accessibility nodes.
_SKIP_TAGS = {"script", "style", "template", "noscript", "head", "svg", "math"}
_FORM_CONTROL_TAGS = ("input", "select", "textarea")
# <input> types that are not labelable text controls.
_NONLABELABLE_INPUT_TYPES = {"hidden", "submit", "button", "reset", "image"}

_SCOPE_FROM_ATTR = {
    "col": TableHeaderScope.COLUMN,
    "colgroup": TableHeaderScope.COLUMN,
    "row": TableHeaderScope.ROW,
    "rowgroup": TableHeaderScope.ROW,
}


class _Ids:
    """Deterministic unique id minter (uniqueness only; the writer locates
    elements by xpath, not by id, so ordering parity is not required)."""

    def __init__(self) -> None:
        self._counts: Dict[str, int] = {}

    def __call__(self, prefix: str) -> str:
        n = self._counts.get(prefix, 0) + 1
        self._counts[prefix] = n
        return f"{prefix}-{n}"


def _tag(el: Any) -> Optional[str]:
    """Return the lowercased tag name, or None for comments / PIs."""
    t = el.tag
    if not isinstance(t, str):
        return None
    return t.lower()


def _text(el: Any) -> str:
    try:
        return (el.text_content() or "").strip()
    except Exception:
        return "".join(el.itertext()).strip()


def _has_element_children(el: Any) -> bool:
    return any(isinstance(child.tag, str) for child in el)


class HTMLParser:
    """Parse an HTML document into an :class:`AccessibilityTree`."""

    def parse_to_tree(self, file_path: str) -> ParserResult:
        path = Path(file_path)
        data = path.read_bytes()
        doc = _parse_document(data)
        roottree = doc.getroottree()
        ids = _Ids()

        body = doc.find("body")
        content_root = body if body is not None else doc
        try:
            children = _build_children(content_root, ids, roottree)
        except RecursionError:
            # Pathologically deep markup (e.g. thousands of nested <div>s) would
            # otherwise exhaust the stack. Degrade gracefully to a minimal tree
            # rather than 500 — the analyzers still grade the document-level
            # signals (title/language) we already collected.
            logger.warning("html_parser: document too deeply nested; structure truncated")
            children = []

        # Document-level signals the analyzers read off the root.
        title_el = doc.find(".//title")
        title = _text(title_el) if title_el is not None else ""
        language = (doc.get("lang") or "").strip() or None

        ff_total, ff_unlabeled = _count_form_fields(doc)

        properties: Dict[str, Any] = {"filename": path.name}
        if title:
            properties["title"] = title
        # Locators the writer uses for document-level edits.
        properties["__html_xpath"] = roottree.getpath(doc)
        if title_el is not None:
            properties["__title_xpath"] = roottree.getpath(title_el)
        if ff_total:
            properties["form_fields_total"] = ff_total
            properties["form_fields_unlabeled"] = ff_unlabeled
            # v1: detect only — we do not auto-derive HTML labels (a wrong
            # accessible name is worse than none), so the executor honestly
            # skips and FILL_FORM_FIELD_LABELS is not in _PERSISTED_ACTIONS.
            properties["form_fields_derivable"] = 0

        root = DocumentNode(
            id="doc-1",
            content=NodeContent(kind=ContentKind.NONE),
            metadata=NodeMetadata(
                language=language,
                source_format="html",
                properties=properties,
            ),
            children=children,
            accessibility_flags=[],
        )

        raw_metadata = {
            "filename": path.name,
            "title": title,
            "language": language or "",
        }
        return ParserResult(
            document_id=path.stem or "doc",
            format="html",
            tree=AccessibilityTree(root=root, metadata=raw_metadata),
            raw_metadata=raw_metadata,
        )


def _parse_document(data: bytes):
    """Parse bytes into an ``<html>`` root, tolerating malformed/partial input.

    Uses lxml.html's default parser, which (unlike the XML parser) does not
    expand custom/external entities, does not fetch external DTDs, and runs with
    ``no_network=True`` — so there is no XXE/SSRF surface, and parsing never
    fetches the resources an HTML document references. A fresh parser per call
    keeps this threadpool-safe.
    """
    blob = data if data and data.strip() else b"<html><head></head><body></body></html>"
    try:
        return lxml_html.document_fromstring(blob)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError):
        return lxml_html.document_fromstring(b"<html><head></head><body></body></html>")


def _meta(el: Any, roottree: Any) -> NodeMetadata:
    return NodeMetadata(source_format="html", properties={"__xpath": roottree.getpath(el)})


def _build_children(el: Any, ids: _Ids, roottree: Any) -> List[Any]:
    out: List[Any] = []
    for child in el:
        tag = _tag(child)
        if tag is None or tag in _SKIP_TAGS:
            continue
        node = _build_node(child, tag, ids, roottree)
        if node is not None:
            out.append(node)
        else:
            # Transparent wrapper (span, strong, label, etc.): inline any
            # accessibility-relevant descendants so inline <img>/<a> are seen.
            out.extend(_build_children(child, ids, roottree))
    return out


def _build_node(el: Any, tag: str, ids: _Ids, roottree: Any) -> Optional[Any]:
    if tag in _HEADING_TAGS:
        text = _text(el)
        content = (
            NodeContent(kind=ContentKind.TEXT, text=text)
            if text
            else NodeContent(kind=ContentKind.NONE)
        )
        return HeadingNode(
            id=ids("html-h"),
            level=_HEADING_TAGS[tag],
            content=content,
            metadata=_meta(el, roottree),
            children=_build_children(el, ids, roottree),
            accessibility_flags=[],
        )

    if tag == "img":
        return _build_image(el, ids, roottree)

    if tag == "a" and el.get("href") is not None:
        text = _text(el)
        if text and not _has_element_children(el):
            content = NodeContent(kind=ContentKind.TEXT, text=text)
        else:
            # Links wrapping elements (e.g. <a><img></a>) get their name from
            # the child; we don't analyze/rewrite their text in v1.
            content = NodeContent(kind=ContentKind.NONE)
        return LinkNode(
            id=ids("html-link"),
            target=(el.get("href") or None),
            content=content,
            metadata=_meta(el, roottree),
            children=_build_children(el, ids, roottree),
            accessibility_flags=[],
        )

    if tag in {"ul", "ol"}:
        return _build_list(el, tag, ids, roottree)

    if tag == "table":
        return _build_table(el, ids, roottree)

    if tag == "p":
        text = _text(el)
        content = (
            NodeContent(kind=ContentKind.TEXT, text=text)
            if text
            else NodeContent(kind=ContentKind.NONE)
        )
        return ParagraphNode(
            id=ids("html-p"),
            content=content,
            metadata=_meta(el, roottree),
            children=_build_children(el, ids, roottree),
            accessibility_flags=[],
        )

    if tag in _SECTION_TAGS:
        children = _build_children(el, ids, roottree)
        if not children:
            return None
        return SectionNode(
            id=ids("html-section"),
            content=NodeContent(kind=ContentKind.NONE),
            metadata=_meta(el, roottree),
            children=children,
            accessibility_flags=[],
        )

    # Unrecognized / inline element: not a node itself — caller inlines its
    # descendants.
    return None


def _build_image(el: Any, ids: _Ids, roottree: Any) -> ImageNode:
    alt = el.get("alt")  # None = attribute absent; "" = explicitly decorative
    role = (el.get("role") or "").strip().lower()
    aria_hidden = (el.get("aria-hidden") or "").strip().lower() == "true"
    # An image is decorative when it carries an empty alt OR is hidden from the
    # a11y tree by role/aria-hidden. Decorative images get no alt_text, so they
    # raise no flags (correct: a properly-marked decorative image is fine).
    is_decorative = (alt == "") or role in {"presentation", "none"} or aria_hidden
    node_id = ids("html-img")
    meta = _meta(el, roottree)

    if is_decorative:
        return ImageNode(
            id=node_id,
            content=NodeContent(kind=ContentKind.NONE),
            metadata=meta,
            children=[],
            accessibility_flags=[],
            is_decorative=True,
            alt_text=None,
        )

    # Normal image: missing alt -> MISSING_ALT_TEXT; filename/placeholder alt ->
    # ALT_TEXT_NOT_DESCRIPTIVE; good alt -> no flag.
    return ImageNode(
        id=node_id,
        content=NodeContent(kind=ContentKind.NONE),
        metadata=meta,
        children=[],
        accessibility_flags=[],
        is_decorative=False,
        alt_text=(alt.strip() if isinstance(alt, str) and alt.strip() else None),
    )


def _build_list(el: Any, tag: str, ids: _Ids, roottree: Any) -> ListNode:
    items: List[Any] = []
    for li in el:
        if _tag(li) != "li":
            continue
        text = _text(li)
        content = (
            NodeContent(kind=ContentKind.TEXT, text=text)
            if text
            else NodeContent(kind=ContentKind.NONE)
        )
        items.append(
            ListItemNode(
                id=ids("html-li"),
                content=content,
                metadata=_meta(li, roottree),
                children=_build_children(li, ids, roottree),
                accessibility_flags=[],
            )
        )
    return ListNode(
        id=ids("html-list"),
        ordered=(tag == "ol"),
        content=NodeContent(kind=ContentKind.NONE),
        metadata=_meta(el, roottree),
        children=items,
        accessibility_flags=[],
    )


def _table_rows(table_el: Any) -> List[Any]:
    """Direct <tr> of this table (including those under thead/tbody/tfoot),
    NOT rows of nested tables."""
    rows: List[Any] = []
    for child in table_el:
        t = _tag(child)
        if t == "tr":
            rows.append(child)
        elif t in {"thead", "tbody", "tfoot"}:
            for sub in child:
                if _tag(sub) == "tr":
                    rows.append(sub)
    return rows


def _build_table(el: Any, ids: _Ids, roottree: Any) -> TableNode:
    rows_nodes: List[Any] = []
    for tr in _table_rows(el):
        cells: List[Any] = []
        for cell_el in tr:
            ct = _tag(cell_el)
            if ct not in {"td", "th"}:
                continue
            is_header = ct == "th"
            text = _text(cell_el)
            if is_header:
                scope_attr = (cell_el.get("scope") or "").strip().lower()
                scope = _SCOPE_FROM_ATTR.get(scope_attr, TableHeaderScope.COLUMN)
            else:
                scope = TableHeaderScope.NONE
            content = (
                NodeContent(kind=ContentKind.TEXT, text=text)
                if text
                else NodeContent(kind=ContentKind.NONE)
            )
            cells.append(
                TableCellNode(
                    id=ids("html-cell"),
                    cell_type=TableCellType.HEADER if is_header else TableCellType.DATA,
                    header_scope=scope,
                    content=content,
                    metadata=_meta(cell_el, roottree),
                    children=_build_children(cell_el, ids, roottree),
                    accessibility_flags=[],
                )
            )
        rows_nodes.append(
            TableRowNode(
                id=ids("html-row"),
                content=NodeContent(kind=ContentKind.NONE),
                metadata=_meta(tr, roottree),
                children=cells,
                accessibility_flags=[],
            )
        )

    meta = _meta(el, roottree)
    caption_el = el.find("caption")
    if caption_el is not None:
        cap = _text(caption_el)
        if cap:
            meta.properties["caption"] = cap

    return TableNode(
        id=ids("html-table"),
        content=NodeContent(kind=ContentKind.NONE),
        metadata=meta,
        children=rows_nodes,
        accessibility_flags=[],
    )


def _count_form_fields(doc: Any) -> Tuple[int, int]:
    """Return (total, unlabeled) labelable form controls."""
    labels_for: set = set()
    for lbl in doc.iter("label"):
        target = lbl.get("for")
        if target:
            labels_for.add(target)

    total = 0
    unlabeled = 0
    for tag in _FORM_CONTROL_TAGS:
        for ctrl in doc.iter(tag):
            if tag == "input":
                itype = (ctrl.get("type") or "text").strip().lower()
                if itype in _NONLABELABLE_INPUT_TYPES:
                    continue
            total += 1
            if not _control_has_accessible_name(ctrl, labels_for):
                unlabeled += 1
    return total, unlabeled


def _control_has_accessible_name(ctrl: Any, labels_for: set) -> bool:
    if (ctrl.get("aria-label") or "").strip():
        return True
    if (ctrl.get("aria-labelledby") or "").strip():
        return True
    if (ctrl.get("title") or "").strip():
        return True
    cid = ctrl.get("id")
    if cid and cid in labels_for:
        return True
    # Wrapped by a <label> ancestor.
    parent = ctrl.getparent()
    while parent is not None:
        if _tag(parent) == "label":
            return True
        parent = parent.getparent()
    return False


__all__ = ["HTMLParser"]
