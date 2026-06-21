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
    - Contrast is populated ONLY from *inline* styles, and ONLY when BOTH the
      text colour and an effective background resolve to a concrete sRGB value
      (the element's own ``background`` or the nearest inline-styled ancestor's).
      We never assume a white page background or read class/stylesheet rules, so
      LOW_CONTRAST_TEXT can only fire on colours we actually determined — no
      false positives. (This catches the very common case of HTML *exported
      from* Word / Google Docs, which is saturated with inline colour styling.)
    - Form fields are DETECTED (count) but ``form_fields_derivable`` is 0, so
      the labeling executor honestly skips — auto-labeling HTML controls
      correctly is deferred to v2 (a wrong label is worse than none).
    - Link text is only treated as analyzable when the ``<a>`` is pure text
      (no child elements), so the IMPROVE_LINK_TEXT writer can always safely
      replace it without destroying nested markup.
"""

from __future__ import annotations

import logging
import re
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

# ---------------------------------------------------------------------------
# Inline-style colour / contrast parsing (conservative; exact values only)
# ---------------------------------------------------------------------------

# The 16 basic HTML colour keywords + a handful that turn up constantly in real
# documents. Exact mappings — no guessing. Anything not here (hsl(), other named
# colours, currentColor, …) is left unresolved so it is simply not flagged.
_NAMED_COLORS: Dict[str, str] = {
    "black": "000000", "silver": "C0C0C0", "gray": "808080", "grey": "808080",
    "white": "FFFFFF", "maroon": "800000", "red": "FF0000", "purple": "800080",
    "fuchsia": "FF00FF", "magenta": "FF00FF", "green": "008000", "lime": "00FF00",
    "olive": "808000", "yellow": "FFFF00", "navy": "000080", "blue": "0000FF",
    "teal": "008080", "aqua": "00FFFF", "cyan": "00FFFF", "orange": "FFA500",
    "lightgray": "D3D3D3", "lightgrey": "D3D3D3", "darkgray": "A9A9A9",
    "darkgrey": "A9A9A9", "gold": "FFD700", "pink": "FFC0CB", "whitesmoke": "F5F5F5",
    "gainsboro": "DCDCDC", "dimgray": "696969", "dimgrey": "696969",
}

_HEX_RE = re.compile(r"#([0-9a-fA-F]{3,8})")
_RGB_RE = re.compile(r"rgba?\(([^)]*)\)", re.IGNORECASE)
_URL_RE = re.compile(r"url\([^)]*\)", re.IGNORECASE)
_IMPORTANT_RE = re.compile(r"\s*!\s*important\s*$", re.IGNORECASE)
# Matches the `color` *property* (anchored at start/`;`) so it never matches
# `background-color` / `border-color` / `caret-color` etc.
_COLOR_DECL_RE = re.compile(r"(?:^|;)\s*color\s*:", re.IGNORECASE)

# Style-context carried down the tree. ``color``/``sz``/``b`` inherit per CSS;
# ``bg`` is the nearest ancestor's non-transparent background (what the text
# visually sits on, since unstyled elements have transparent backgrounds).
_EMPTY_CTX: Dict[str, Any] = {"color": None, "bg": None, "sz": None, "b": False}


def _hex_token_to_hex(tok: str) -> Optional[str]:
    """Normalise the digits after ``#`` (3/4/6/8) to ``RRGGBB``.

    4- and 8-digit forms carry alpha; if the colour is not fully opaque we
    return None (a blended colour can't be scored without the backdrop, so we
    decline rather than guess)."""
    t = tok.lower()
    if len(t) == 3:
        return "".join(ch * 2 for ch in t).upper()
    if len(t) == 4:
        if t[3] != "f":  # alpha nibble not opaque
            return None
        return "".join(ch * 2 for ch in t[:3]).upper()
    if len(t) == 6:
        return t.upper()
    if len(t) == 8:
        if t[6:8] != "ff":
            return None
        return t[:6].upper()
    return None


def _rgb_to_hex(inner: str) -> Optional[str]:
    """``r,g,b`` / ``r,g,b,a`` (ints or %) -> ``RRGGBB``; None if translucent."""
    # Accept comma- or whitespace/slash-separated components (modern + legacy).
    parts = [p for p in re.split(r"[,/\s]+", inner.strip()) if p]
    if len(parts) < 3:
        return None
    try:
        comps = []
        for p in parts[:3]:
            if p.endswith("%"):
                comps.append(round(float(p[:-1]) / 100.0 * 255.0))
            else:
                comps.append(int(round(float(p))))
        if len(parts) >= 4:
            a = parts[3]
            alpha = float(a[:-1]) / 100.0 if a.endswith("%") else float(a)
            if alpha < 1.0:
                return None  # translucent -> can't score against unknown backdrop
    except ValueError:
        return None
    comps = [max(0, min(255, c)) for c in comps]
    return "".join(f"{c:02X}" for c in comps)


def _css_color_to_hex(value: str) -> Optional[str]:
    """Resolve a single CSS colour value to ``RRGGBB``, or None if we can't be
    certain (named-but-unknown, hsl, transparent, currentColor, keywords)."""
    v = value.strip().lower()
    if not v or v in {"transparent", "inherit", "initial", "unset", "currentcolor", "none"}:
        return None
    if v.startswith("#"):
        return _hex_token_to_hex(v[1:])
    m = _RGB_RE.match(v)
    if m:
        return _rgb_to_hex(m.group(1))
    return _NAMED_COLORS.get(v)


def _extract_bg_color(value: str) -> Optional[str]:
    """Pull a concrete colour out of a ``background`` shorthand (which may also
    carry images/position/repeat), or None if none is resolvable."""
    # Drop any url(...) first so a colour-like fragment id (e.g.
    # url(sprite.svg#a0f0c0)) is never mistaken for a background colour.
    value = _URL_RE.sub(" ", value)
    m = _HEX_RE.search(value)
    if m:
        h = _hex_token_to_hex(m.group(1))
        if h:
            return h
    m = _RGB_RE.search(value)
    if m:
        h = _rgb_to_hex(m.group(1))
        if h:
            return h
    for tok in re.split(r"\s+", value.strip().lower()):
        if tok in _NAMED_COLORS:
            return _NAMED_COLORS[tok]
    return None


def _font_size_to_pt(value: str) -> Optional[float]:
    """Absolute font-size to points: ``px`` (×0.75) or ``pt``. Relative units
    (em/rem/%) and keywords are left unresolved (None) — we never guess a base
    size, so the analyzer just treats size as unknown for those runs."""
    v = value.strip().lower()
    try:
        if v.endswith("px"):
            return float(v[:-2]) * 0.75
        if v.endswith("pt"):
            return float(v[:-2])
    except ValueError:
        return None
    return None


def _is_bold_weight(value: str) -> bool:
    v = value.strip().lower()
    if v in {"bold", "bolder"}:
        return True
    try:
        return float(v) >= 700.0
    except ValueError:
        return False


def _inline_style(el: Any) -> Dict[str, Any]:
    """Parse the colour-relevant declarations from an element's ``style`` attr.

    Only keys that resolve to a concrete value are returned, so a caller's
    ``dict.get(k, inherited)`` merge naturally inherits everything else."""
    raw = el.get("style")
    out: Dict[str, Any] = {}
    if not raw:
        return out
    decls: Dict[str, str] = {}
    for part in raw.split(";"):
        if ":" not in part:
            continue
        k, _, val = part.partition(":")
        decls[k.strip().lower()] = _IMPORTANT_RE.sub("", val.strip())

    if "color" in decls:
        c = _css_color_to_hex(decls["color"])
        if c:
            out["color"] = c
    if "background-color" in decls:
        b = _css_color_to_hex(decls["background-color"])
        if b:
            out["bg"] = b
    elif "background" in decls:
        b = _extract_bg_color(decls["background"])
        if b:
            out["bg"] = b
    if "font-size" in decls:
        sz = _font_size_to_pt(decls["font-size"])
        if sz is not None:
            out["sz"] = sz
    if "font-weight" in decls:
        out["b"] = _is_bold_weight(decls["font-weight"])
    return out


def _merge_ctx(parent: Dict[str, Any], own: Dict[str, Any]) -> Dict[str, Any]:
    """Layer an element's own inline style over the inherited context."""
    return {
        "color": own.get("color", parent.get("color")),
        "bg": own.get("bg", parent.get("bg")),
        "sz": own.get("sz", parent.get("sz")),
        "b": own.get("b", parent.get("b", False)),
    }


def _contrast_props(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Contrast metadata for a text node — only when BOTH the resolved text
    colour AND an effective background are known (else empty: never assume)."""
    color = ctx.get("color")
    bg = ctx.get("bg")
    if not color or not bg:
        return {}
    return {
        "explicit_text_colors": [{"c": color, "sz": ctx.get("sz"), "b": bool(ctx.get("b"))}],
        "bg_color": bg,
    }


def _declares_color(el: Any) -> bool:
    raw = el.get("style")
    return bool(raw and _COLOR_DECL_RE.search(raw))


def _has_conflicting_child_color(el: Any, resolved_color: str) -> bool:
    """True if any descendant re-declares the ``color`` property to something
    other than ``resolved_color`` (a different hex, or a value we can't resolve).

    Because we score a text block with a single colour, such an override means
    part of the visible text is actually a *different* colour than the one we'd
    record — so we must decline rather than risk a false "fails contrast"."""
    for desc in el.iterdescendants():
        if not isinstance(desc.tag, str):
            continue
        if _tag(desc) in _SKIP_TAGS:
            continue
        if not _declares_color(desc):
            continue
        if _inline_style(desc).get("color") != resolved_color:
            return True
    return False


def _emit_ctx(el: Any, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The style context to record on a text node, or None when contrast can't
    be determined unambiguously (no resolved fg+bg, or a child overrides fg)."""
    if not ctx.get("color") or not ctx.get("bg"):
        return None
    if _has_conflicting_child_color(el, ctx["color"]):
        return None
    return ctx


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
        # Seed the inherited style context from <html> then <body> so a
        # page-level inline background/colour (common in exported-doc HTML and
        # email) flows down to the content.
        root_ctx = _merge_ctx(_EMPTY_CTX, _inline_style(doc))
        if body is not None:
            root_ctx = _merge_ctx(root_ctx, _inline_style(body))

        try:
            children = _build_children(content_root, ids, roottree, root_ctx)
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


def _meta(el: Any, roottree: Any, ctx: Optional[Dict[str, Any]] = None) -> NodeMetadata:
    """Node metadata with the writer's xpath locator, plus contrast colours when
    a style context is supplied for a text-bearing node."""
    props: Dict[str, Any] = {"__xpath": roottree.getpath(el)}
    if ctx is not None:
        props.update(_contrast_props(ctx))
    return NodeMetadata(source_format="html", properties=props)


def _build_children(el: Any, ids: _Ids, roottree: Any, ctx: Dict[str, Any]) -> List[Any]:
    out: List[Any] = []
    for child in el:
        tag = _tag(child)
        if tag is None or tag in _SKIP_TAGS:
            continue
        child_ctx = _merge_ctx(ctx, _inline_style(child))
        node = _build_node(child, tag, ids, roottree, child_ctx)
        if node is not None:
            out.append(node)
        else:
            # Transparent wrapper (span, strong, label, etc.): inline any
            # accessibility-relevant descendants so inline <img>/<a> are seen,
            # carrying the wrapper's style down to them.
            out.extend(_build_children(child, ids, roottree, child_ctx))
    return out


def _build_node(el: Any, tag: str, ids: _Ids, roottree: Any, ctx: Dict[str, Any]) -> Optional[Any]:
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
            metadata=_meta(el, roottree, _emit_ctx(el, ctx) if text else None),
            children=_build_children(el, ids, roottree, ctx),
            accessibility_flags=[],
        )

    if tag == "img":
        return _build_image(el, ids, roottree)

    if tag == "a" and el.get("href") is not None:
        text = _text(el)
        if text and not _has_element_children(el):
            content = NodeContent(kind=ContentKind.TEXT, text=text)
            link_ctx: Optional[Dict[str, Any]] = _emit_ctx(el, ctx)
        else:
            # Links wrapping elements (e.g. <a><img></a>) get their name from
            # the child; we don't analyze/rewrite their text in v1.
            content = NodeContent(kind=ContentKind.NONE)
            link_ctx = None
        return LinkNode(
            id=ids("html-link"),
            target=(el.get("href") or None),
            content=content,
            metadata=_meta(el, roottree, link_ctx),
            children=_build_children(el, ids, roottree, ctx),
            accessibility_flags=[],
        )

    if tag in {"ul", "ol"}:
        return _build_list(el, tag, ids, roottree, ctx)

    if tag == "table":
        return _build_table(el, ids, roottree, ctx)

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
            metadata=_meta(el, roottree, _emit_ctx(el, ctx) if text else None),
            children=_build_children(el, ids, roottree, ctx),
            accessibility_flags=[],
        )

    if tag in _SECTION_TAGS:
        children = _build_children(el, ids, roottree, ctx)
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


def _build_list(el: Any, tag: str, ids: _Ids, roottree: Any, ctx: Dict[str, Any]) -> ListNode:
    items: List[Any] = []
    for li in el:
        if _tag(li) != "li":
            continue
        li_ctx = _merge_ctx(ctx, _inline_style(li))
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
                metadata=_meta(li, roottree, _emit_ctx(li, li_ctx) if text else None),
                children=_build_children(li, ids, roottree, li_ctx),
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


def _build_table(el: Any, ids: _Ids, roottree: Any, ctx: Dict[str, Any]) -> TableNode:
    rows_nodes: List[Any] = []
    for tr in _table_rows(el):
        tr_ctx = _merge_ctx(ctx, _inline_style(tr))
        cells: List[Any] = []
        for cell_el in tr:
            ct = _tag(cell_el)
            if ct not in {"td", "th"}:
                continue
            cell_ctx = _merge_ctx(tr_ctx, _inline_style(cell_el))
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
                    metadata=_meta(cell_el, roottree, _emit_ctx(cell_el, cell_ctx) if text else None),
                    children=_build_children(cell_el, ids, roottree, cell_ctx),
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
