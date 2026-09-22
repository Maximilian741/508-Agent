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
    - Form fields are DETECTED (count) AND, when an unlabeled control has a
      CONFIDENT nearby label (an orphan ``<label>``, "Name: [input]" preceding
      text, or a table label cell), it is counted in ``form_fields_derivable``
      so the executor can auto-write an ``aria-label``. Ambiguous controls stay
      manual (a wrong accessible name is worse than none) — the same
      conservative rules the DOCX content-control deriver uses.
    - Link text is only treated as analyzable when the ``<a>`` is pure text
      (no child elements), so the IMPROVE_LINK_TEXT writer can always safely
      replace it without destroying nested markup.
"""

from __future__ import annotations

import codecs
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree
from lxml import html as lxml_html

logger = logging.getLogger(__name__)

# Reuse the DOCX fake-list rules so "what is a typed list" means the same thing
# in every format (the executor already shares strip_fake_list_prefix from here).
from app.parsers.docx_parser import (
    _fake_list_signature,
    _group_fake_list_runs,
    strip_fake_list_prefix,
)

from app.parsers.document_id import derive_document_id
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
# Content we never descend into for accessibility nodes. (<svg> is handled
# as a whole by _build_svg — it can be an image — and its children are never
# walked as page content.)
_SKIP_TAGS = {"script", "style", "template", "noscript", "head", "math"}
# Tree depth past which a plain wrapper (<div>/<section>/...) stops getting
# its own SectionNode and its children are spliced into the parent instead.
# Real pages never nest 64 wrappers deep; legacy <font>/<div> soup and
# hostile pages do, and a tree as deep as the DOM just moves the stack
# problem downstream. Headings, links, lists, tables and images are ALWAYS
# emitted — only the empty grouping level is dropped, so no finding is lost.
_MAX_SECTION_DEPTH = 64
# Tags _build_node may turn into a node (and so locate); <a> counts only with
# an href, checked separately.
_NODE_TAGS = frozenset(set(_HEADING_TAGS) | _SECTION_TAGS | {"img", "svg", "ul", "ol", "table", "p"})
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
    """Layer an element's own inline style over the inherited context.

    ``dd`` is the element's DOM depth (every parent->child step merges once),
    which the builder uses to meter locator cost — see :class:`_PathBudget`.
    """
    return {
        "color": own.get("color", parent.get("color")),
        "bg": own.get("bg", parent.get("bg")),
        "sz": own.get("sz", parent.get("sz")),
        "b": own.get("b", parent.get("b", False)),
        "dd": parent.get("dd", 0) + 1,
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


def _elements(root: Any) -> List[Any]:
    """``root`` and every node under it, in document order, as a LIST.

    Iterating lxml lazily lets each element proxy die as soon as the loop
    moves on, and on every release lxml walks from that node UP to the nearest
    node that still has a Python proxy to decide what it may free — in a deep
    tree, all the way to the document. That made one pass over a 20,000-deep
    page cost 1.3 s (O(depth^2)). Holding the proxies and dropping the list
    at once (CPython releases list items last-first, so each node's parent is
    still alive) keeps every release O(1).
    """
    return list(root.iter())


def _ancestors(el: Any) -> List[Any]:
    """``el``'s ancestors, OUTERMOST first — so releasing the list frees the
    innermost first, while its parent is still held (see :func:`_elements`)."""
    chain = list(el.iterancestors())
    chain.reverse()
    return chain


def _has_conflicting_child_color(el: Any, resolved_color: str) -> bool:
    """True if any descendant re-declares the ``color`` property to something
    other than ``resolved_color`` (a different hex, or a value we can't resolve).

    Because we score a text block with a single colour, such an override means
    part of the visible text is actually a *different* colour than the one we'd
    record — so we must decline rather than risk a false "fails contrast"."""
    for desc in _elements(el)[1:]:
        if not isinstance(desc.tag, str):
            continue
        if _tag(desc) in _SKIP_TAGS or _tag(desc) == "svg":
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


# Every node carries an absolute getpath() locator, which costs O(DOM depth)
# to build and store. For normal pages that is nothing; for a page that nests
# thousands of node-producing elements thousands deep (tables in tables,
# lists in lists — hostile or broken markup, and /scan-url fetches any public
# page) it is quadratic: 3000 nested tables took 52s. Each element that may
# become a node is charged its depth beyond _PATH_FREE_DEPTH when the walk
# STARTS it (rows/tables/sections are only located after their children, so
# charging at locate time would let the descent overshoot); once the budget
# is spent the walk stops emitting nodes and the document is flagged
# ANALYSIS_TRUNCATED, so the report never looks complete when it is not.
_PATH_FREE_DEPTH = 64
_PATH_DEPTH_BUDGET = 2_000_000


class _PathBudget:
    """Stands in for the ElementTree in the builder: same ``getpath``, plus a
    depth meter the builder charges as it descends.

    ``getpath`` returns exactly what ``ElementTree.getpath`` does, but in
    O(depth) with a per-parent step cache. libxml2 computes every step's
    ``[n]`` by counting the element's same-name siblings, so a page with N
    siblings cost O(N^2): 40,000 paragraphs spent 6.8 of 8.3 s in getpath and
    200,000 took 400 s — a long data table or a big export stalled a worker.
    """

    def __init__(self, roottree: Any, limit: int = _PATH_DEPTH_BUDGET) -> None:
        self._tree = roottree
        self._limit = limit
        self.spent = 0
        self.exhausted = False
        self._steps: Dict[Any, str] = {}
        self._indexed: set = set()

    def _index_children(self, parent: Any) -> None:
        """Record every element child's getpath() step: ``tag`` when it is
        the only child with that name, else ``tag[n]`` (1-based among
        same-name siblings) — libxml2's xmlGetNodePath rule."""
        self._indexed.add(parent)
        counts: Dict[str, int] = {}
        for child in parent:
            tag = child.tag
            if isinstance(tag, str):
                counts[tag] = counts.get(tag, 0) + 1
        seen: Dict[str, int] = {}
        for child in parent:
            tag = child.tag
            if not isinstance(tag, str) or tag.startswith("{"):
                continue  # namespaced steps ("*[n]", "p:x[n]") are left to lxml
            if counts[tag] > 1:
                n = seen.get(tag, 0) + 1
                seen[tag] = n
                self._steps[child] = f"{tag}[{n}]"
            else:
                self._steps[child] = tag

    def getpath(self, el: Any) -> str:
        parts: List[str] = []
        cur = el
        while True:
            parent = cur.getparent()
            if parent is None:
                tag = cur.tag
                if not isinstance(tag, str) or tag.startswith("{"):
                    return self._tree.getpath(el)
                parts.append(tag)
                break
            step = self._steps.get(cur)
            if step is None and parent not in self._indexed:
                self._index_children(parent)
                step = self._steps.get(cur)
            if step is None:
                return self._tree.getpath(el)
            parts.append(step)
            cur = parent
        parts.reverse()
        return "/" + "/".join(parts)

    def charge(self, dom_depth: Any) -> None:
        try:
            excess = int(dom_depth) - _PATH_FREE_DEPTH
        except (TypeError, ValueError):
            return
        if excess > 0:
            self.spent += excess
            if self.spent > self._limit:
                self.exhausted = True


def _charge(locator: Any, ctx: Dict[str, Any]) -> None:
    charge = getattr(locator, "charge", None)
    if charge is not None:
        charge(ctx.get("dd", 0))


def _budget_spent(locator: Any) -> bool:
    return bool(getattr(locator, "exhausted", False))


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


def _in_code_context(el: Any) -> bool:
    """True if ``el`` is inside a <pre>/<code>/<kbd>/<samp> ancestor — content
    where a leading "- " is a diff/code marker, not a list bullet."""
    parent = el.getparent()
    while parent is not None:
        if _tag(parent) in ("pre", "code", "kbd", "samp"):
            return True
        parent = parent.getparent()
    return False


class HTMLParser:
    """Parse an HTML document into an :class:`AccessibilityTree`."""

    def parse_to_tree(self, file_path: str) -> ParserResult:
        path = Path(file_path)
        data = path.read_bytes()
        doc, source = parse_html_source(data)
        if source.blank or source.fallback:
            # No markup or text at all (whitespace, a lone comment/doctype/PHP
            # tag). Analysing the stand-in empty page reported "title and
            # language missing" — a near-clean score for a file with nothing in
            # it — and /remediate then charged to wrap a title around nothing.
            raise ValueError("This HTML file has no page content to check.")
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

        # The walk is ITERATIVE (see _drive): it used to recurse two Python
        # frames per DOM level, so a page nested ~450 deep hit RecursionError
        # and the whole analysis collapsed to title/language — a page full of
        # unlabeled images reported as nearly clean. Depth is now bounded by
        # memory, not the interpreter stack, and every node is still emitted.
        locator = _PathBudget(roottree)
        children = _drive(_build_children(content_root, ids, locator, root_ctx, 0))

        # Detect FAKE lists — runs of plain <p> typed as "- item" / "1. item"
        # that should be a real <ul>/<ol>. Mirrors the DOCX/PPTX path: mark each
        # member's signature, then group consecutive same-kind runs (>=2). The
        # analyzer + executor are format-agnostic, and html_writer converts the
        # run to a real list under FIX_LIST_STRUCTURE.
        _mark_html_fake_lists(children)

        # Document-level signals the analyzers read off the root.
        title_el = doc.find(".//title")
        title = _text(title_el) if title_el is not None else ""
        language = (doc.get("lang") or "").strip() or None

        ff_total, ff_unlabeled, ff_derivable = _count_form_fields(doc)

        properties: Dict[str, Any] = {"filename": path.name}
        if title:
            properties["title"] = title
        if locator.exhausted:
            # Read by AnalysisTruncatedAnalyzer: the findings do not cover the
            # whole page, and the report must say so.
            logger.warning("html_parser: nesting too deep to analyze fully; structure truncated")
            properties["pages_truncated"] = True
            properties["analysis_truncated_reason"] = "nesting_depth"
        # Locators the writer uses for document-level edits.
        properties["__html_xpath"] = roottree.getpath(doc)
        if title_el is not None:
            properties["__title_xpath"] = roottree.getpath(title_el)
        if ff_total:
            properties["form_fields_total"] = ff_total
            properties["form_fields_unlabeled"] = ff_unlabeled
            # How many unlabeled controls have a CONFIDENT nearby label the
            # writer can turn into an aria-label. The writer re-derives with the
            # SAME helper on the same bytes, so this count == what gets written
            # (the honesty invariant) — see iter_derivable_form_labels.
            properties["form_fields_derivable"] = ff_derivable

        # Counted with the SAME iterators the writer applies, so what we claim is
        # exactly what gets written (see iter_autocomplete_candidates /
        # iter_positive_tabindex).
        ac_count = sum(1 for _ in iter_autocomplete_candidates(doc))
        if ac_count:
            properties["inputs_missing_autocomplete"] = ac_count
        tabindex_count = sum(1 for _ in iter_positive_tabindex(doc))
        if tabindex_count:
            properties["positive_tabindex_count"] = tabindex_count
        untitled_frames = count_untitled_iframes(doc)
        if untitled_frames:
            properties["iframes_missing_title"] = untitled_frames
        label_mismatches = count_label_in_name_mismatches(doc)
        if label_mismatches:
            properties["label_in_name_mismatches"] = label_mismatches

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
            document_id=derive_document_id(path),
            format="html",
            tree=AccessibilityTree(root=root, metadata=raw_metadata),
            raw_metadata=raw_metadata,
        )


_EMPTY_HTML = "<html><head></head><body></body></html>"


@dataclass(frozen=True)
class HtmlSource:
    """How a page's bytes were decoded — so the writer can put them back.

    ``encoding`` is the Python codec the text was decoded with; the writer
    re-encodes with the SAME codec (plus the same BOM and XML declaration), so
    every byte of text we did not change comes back exactly as it was, and the
    page drops back into whatever server/charset setup it came from.
    """

    encoding: str = "utf-8"
    bom: bytes = b""
    xml_decl: str = ""
    has_doctype: bool = False
    # True when lxml could not build a document at all and we substituted an
    # empty one — the writer must never ship that as the customer's page.
    fallback: bool = False
    # True when the bytes hold no markup or text at all (empty/whitespace):
    # there is no page to check, and nothing to "fix" into one.
    blank: bool = False
    # False when nothing declared the encoding (no BOM, <meta> or XML
    # declaration) and ``encoding`` is our guess.
    declared: bool = True
    # Every source byte was ASCII. With ``declared`` False that means a
    # browser may decode the page as ANY ASCII-compatible encoding, so the
    # writer keeps it pure ASCII (see encode_html_text).
    ascii_only: bool = False
    # The text carries _BYTE_ESCAPE stand-ins for raw bytes (NULs, sequences
    # invalid in the declared encoding) that encode_html_text puts back.
    escaped: bool = False


def _parse_document(data: bytes):
    """Parse bytes into an ``<html>`` root, tolerating malformed/partial input."""
    return parse_html_source(data)[0]


def parse_html_source(data: bytes, preserve_bytes: bool = False) -> Tuple[Any, HtmlSource]:
    """Parse bytes into ``(<html> root, HtmlSource)``.

    ``preserve_bytes`` (the writer) keeps bytes the text model can't carry — a
    NUL, or a byte sequence that is invalid in the page's declared encoding —
    as private-use stand-ins that :func:`encode_html_text` turns back into the
    SAME bytes, instead of deleting them (NUL) or replacing them with U+FFFD.
    The analysis path leaves it off and sees what a browser renders.

    Uses lxml.html's default parser, which (unlike the XML parser) does not
    expand custom/external entities, does not fetch external DTDs, and runs with
    ``no_network=True`` — so there is no XXE/SSRF surface, and parsing never
    fetches the resources an HTML document references. A fresh parser per call
    keeps this threadpool-safe.

    We DECODE the bytes ourselves (see :func:`decode_html_bytes`) and hand lxml
    a str. Leaving it to libxml2 was wrong whenever anything non-ASCII came
    before the ``<meta charset>`` — libxml2 had already committed to latin-1
    by then, so ``<title>Café — Menú</title><meta charset="utf-8">`` became
    "CafÃ© â€" Menú" in the analysis AND was re-serialised double-encoded
    across the whole delivered page (word counts matched, so the content-loss
    gate could not see it).
    """
    text, src = decode_html_bytes(data or b"", preserve_bytes=preserve_bytes)
    if not text.strip():
        doc = lxml_html.document_fromstring(_EMPTY_HTML, parser=lxml_html.HTMLParser(no_network=True))
        return doc, replace(src, blank=True)
    # huge_tree lifts libxml2's default 256-level depth clamp. Below that
    # limit lxml silently DROPS everything nested deeper — no error — and the
    # writer then serialized the truncated tree as the "fixed" file, deleting
    # content from a customer's page while reporting success. Legacy pages
    # with unclosed <font>/<div> chains reach 256 easily. Everything else in
    # the docstring still holds: no entity expansion, no network.
    parser = lxml_html.HTMLParser(huge_tree=True, no_network=True)
    try:
        return lxml_html.document_fromstring(text, parser=parser), src
    except (etree.ParserError, etree.XMLSyntaxError, ValueError):
        doc = lxml_html.document_fromstring(_EMPTY_HTML, parser=lxml_html.HTMLParser(no_network=True))
        return doc, replace(src, fallback=True)


# ---------------------------------------------------------------------------
# Encoding sniffing — what a browser would decode these bytes as.
# ---------------------------------------------------------------------------

_BOMS: Tuple[Tuple[bytes, str], ...] = (
    (b"\xef\xbb\xbf", "utf-8"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)

# WHATWG Encoding Standard label mappings that DIFFER from Python's codec of
# the same name. The big one: every latin-1/ascii label means windows-1252 on
# the web, so 0x80-0x9F are curly quotes, dashes and the euro sign — decoding
# them as ISO-8859-1 turns "—" into an invisible C1 control character.
_LABEL_OVERRIDES: Dict[str, str] = {
    **dict.fromkeys(
        ("iso-8859-1", "iso8859-1", "iso_8859-1", "iso88591", "latin1", "latin-1", "l1",
         "cp819", "ibm819", "iso-ir-100", "csisolatin1", "us-ascii", "ascii",
         "ansi_x3.4-1968", "iso-ir-6", "x-cp1252", "windows-1252", "cp1252", "x-user-defined"),
        "cp1252",
    ),
    **dict.fromkeys(("iso-8859-9", "iso8859-9", "iso_8859-9", "latin5", "l5", "csisolatin5"), "cp1254"),
    **dict.fromkeys(("iso-8859-11", "iso8859-11", "tis-620", "dos-874", "windows-874"), "cp874"),
    **dict.fromkeys(("iso-8859-8-i", "csiso88598i", "logical", "visual"), "iso8859-8"),
    **dict.fromkeys(
        ("gb2312", "gb_2312", "gb_2312-80", "chinese", "csgb2312", "csiso58gb231280",
         "iso-ir-58", "x-gbk", "gbk"),
        "gbk",
    ),
    **dict.fromkeys(
        ("shift_jis", "shift-jis", "sjis", "ms_kanji", "x-sjis", "windows-31j", "csshiftjis", "ms932"),
        "cp932",
    ),
    **dict.fromkeys(
        ("euc-kr", "ks_c_5601-1987", "ks_c_5601-1989", "ksc5601", "ksc_5601", "korean",
         "windows-949", "csksc56011987", "iso-ir-149", "cseuckr"),
        "cp949",
    ),
    **dict.fromkeys(("big5", "big5-hkscs", "cn-big5", "x-x-big5", "csbig5"), "big5hkscs"),
    **dict.fromkeys(("x-mac-roman", "macintosh", "mac", "csmacintosh"), "mac-roman"),
    **dict.fromkeys(("x-mac-cyrillic", "x-mac-ukrainian"), "mac-cyrillic"),
    # A <meta> is only readable if the bytes are ASCII-compatible, so WHATWG
    # treats a meta-declared UTF-16 as UTF-8 (a real UTF-16 file has a BOM).
    **dict.fromkeys(
        ("utf-16", "utf-16le", "utf-16be", "unicode", "ucs-2", "csunicode",
         "iso-10646-ucs-2", "unicodefeff", "unicodefffe"),
        "utf-8",
    ),
}

# Codecs a web page can legitimately be in (the WHATWG encoding list, by
# Python codec name). Anything else — utf-7, unicode_escape, idna, rot13,
# base64, a typo — is ignored as a declaration: decoding a page with
# unicode_escape would rewrite every backslash sequence in it.
_WEB_CODECS = frozenset({
    "utf-8", "cp866", "iso8859-2", "iso8859-3", "iso8859-4", "iso8859-5", "iso8859-6",
    "iso8859-7", "iso8859-8", "iso8859-10", "iso8859-13", "iso8859-14", "iso8859-15",
    "iso8859-16", "koi8-r", "koi8-u", "mac-roman", "mac-cyrillic", "cp874", "cp1250",
    "cp1251", "cp1252", "cp1253", "cp1254", "cp1255", "cp1256", "cp1257", "cp1258",
    "gbk", "gb18030", "big5hkscs", "euc_jp", "iso2022_jp", "cp932", "cp949",
    "utf-16-le", "utf-16-be",
})

# The five bytes windows-1252 leaves undefined. Browsers map each to the C1
# control of the same value (as latin-1 would); Python's strict cp1252 codec
# raises instead, so decode/encode them with the handlers below — a stray 0x81
# must round-trip as 0x81, not become U+FFFD or a character reference.
_CP1252_UNDEFINED = frozenset((0x81, 0x8D, 0x8F, 0x90, 0x9D))


def _cp1252_decode_errors(err: UnicodeError) -> Tuple[str, int]:
    chunk = err.object[err.start:err.end]  # type: ignore[attr-defined]
    return bytes(chunk).decode("latin-1"), err.end  # type: ignore[attr-defined]


def _html_encode_errors(err: UnicodeError) -> Tuple[bytes, int]:
    """Characters the page's own encoding can't hold -> ``&#N;`` references.

    Only text WE inserted can hit this (everything else was decoded from this
    very encoding): an em dash in generated alt text written into a latin-1
    page becomes ``&#8212;``, which every browser renders as "—".
    """
    out = bytearray()
    for ch in err.object[err.start:err.end]:  # type: ignore[attr-defined]
        out += f"&#{ord(ch)};".encode("ascii")
    return bytes(out), err.end  # type: ignore[attr-defined]


def _cp1252_encode_errors(err: UnicodeError) -> Tuple[bytes, int]:
    out = bytearray()
    for ch in err.object[err.start:err.end]:  # type: ignore[attr-defined]
        cp = ord(ch)
        out += bytes((cp,)) if cp in _CP1252_UNDEFINED else f"&#{cp};".encode("ascii")
    return bytes(out), err.end  # type: ignore[attr-defined]


# Stand-ins for raw source bytes that the decoded text can't carry: a NUL
# (libxml2 reads a str as a C string, so one NUL silently ended the document —
# everything after it vanished from the analysis AND from the "fixed" file,
# and the content-loss gate, which parses the same way, saw nothing wrong),
# or a byte sequence invalid in the page's own encoding (decoded as U+FFFD it
# would be re-encoded as EF BF BD, destroying the original byte). The last
# 256 code points of Supplementary Private Use Area-B, one per byte value:
# lxml keeps them as ordinary characters and encode_html_text restores them.
_BYTE_ESCAPE_BASE = 0x10FF00
_BYTE_ESCAPE_RE = re.compile("[\U0010FF00-\U0010FFFF]+")


def _byte_escape_decode_errors(err: UnicodeError) -> Tuple[str, int]:
    chunk = err.object[err.start:err.end]  # type: ignore[attr-defined]
    return "".join(chr(_BYTE_ESCAPE_BASE + b) for b in bytes(chunk)), err.end  # type: ignore[attr-defined]


codecs.register_error("a508_cp1252_decode", _cp1252_decode_errors)
codecs.register_error("a508_html_charref", _html_encode_errors)
codecs.register_error("a508_cp1252_encode", _cp1252_encode_errors)
codecs.register_error("a508_byte_escape", _byte_escape_decode_errors)


def resolve_charset_label(label: Any) -> Optional[str]:
    """A declared charset label -> the Python codec to use, or None if unusable."""
    if isinstance(label, bytes):
        label = label.decode("ascii", "ignore")
    norm = str(label or "").strip().strip("\"'").strip().lower()
    if not norm:
        return None
    if norm in _LABEL_OVERRIDES:
        return _LABEL_OVERRIDES[norm]
    try:
        name = codecs.lookup(norm).name
    except (LookupError, ValueError):
        return None
    return name if name in _WEB_CODECS else None


_HEAD_END_RE = re.compile(rb"</head\b|<body\b", re.IGNORECASE)
_COMMENT_RE = re.compile(rb"<!--.*?-->", re.DOTALL)
_META_TAG_RE = re.compile(rb"<meta\b([^>]*)>", re.IGNORECASE)
_ATTR_RE = re.compile(rb"""([^\s=/>"']+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+)))?""")
_CONTENT_CHARSET_RE = re.compile(rb"""charset\s*=\s*["']?\s*([^\s"';]+)""", re.IGNORECASE)
_XML_DECL_ENC_RE = re.compile(rb"""^\s*<\?xml\b[^>]*?\bencoding\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_XML_DECL_STR_RE = re.compile(r"""^\s*<\?xml\b[^>]*\?>[ \t]*(?:\r?\n)?""", re.IGNORECASE)
_DOCTYPE_RE = re.compile(r"^\s*(?:<!--.*?-->\s*)*<!doctype\b", re.IGNORECASE | re.DOTALL)
# How much of the page to prescan for <meta charset>. WHATWG's prescan reads
# 1024 bytes, but a browser that meets a <meta charset> later in the <head>
# re-decodes the page with it — and a long <title>/<style> ahead of the meta
# is exactly the case that broke. So scan the whole <head>, capped.
_PRESCAN_LIMIT = 65536


def _meta_declared_charset(blob: bytes) -> Optional[str]:
    head = blob[:_PRESCAN_LIMIT]
    m = _HEAD_END_RE.search(head)
    if m:
        head = head[: m.start()]
    head = _COMMENT_RE.sub(b" ", head)  # a <meta> inside a comment declares nothing
    for tag in _META_TAG_RE.finditer(head):
        attrs: Dict[bytes, bytes] = {}
        for am in _ATTR_RE.finditer(tag.group(1)):
            key = am.group(1).lower()
            if key not in attrs:
                attrs[key] = am.group(2) or am.group(3) or am.group(4) or b""
        if b"charset" in attrs:
            codec = resolve_charset_label(attrs[b"charset"])
            if codec:
                return codec
            continue
        if attrs.get(b"http-equiv", b"").strip().lower() == b"content-type":
            cm = _CONTENT_CHARSET_RE.search(attrs.get(b"content", b""))
            if cm:
                codec = resolve_charset_label(cm.group(1))
                if codec:
                    return codec
    return None


def _sniff_utf16_without_bom(blob: bytes) -> Optional[str]:
    """``utf-16-le``/``utf-16-be`` for UTF-16 markup saved WITHOUT a BOM.

    Some Windows tools write that. Read as an ASCII-compatible encoding it is
    NUL-interleaved garbage (and the NULs used to end the document), so the
    page analysed as empty and the fix 422'd. Markup is mostly ASCII, so in
    UTF-16 one byte of most code units is zero — the XML spec's own
    autodetection keys on the same ``<`` + NUL pattern. Requires the text to
    start with ``<`` so a binary file never qualifies.
    """
    sample = blob[:2048]
    sample = sample[: len(sample) // 2 * 2]
    if len(sample) < 8:
        return None
    units = len(sample) // 2
    even_nul = sample[0::2].count(0)
    odd_nul = sample[1::2].count(0)
    for codec, zeros, other in (("utf-16-le", odd_nul, even_nul), ("utf-16-be", even_nul, odd_nul)):
        if zeros >= 0.4 * units and other <= 0.05 * units:
            head = sample.decode(codec, errors="replace").lstrip()
            if head.startswith("<"):
                return codec
    return None


def _decode_as(blob: bytes, codec: str, preserve: bool = False) -> Tuple[str, bool]:
    """``(text, escaped)``: decode ``blob`` as ``codec``.

    Invalid sequences render as U+FFFD in a browser, so the analysis decodes
    them the same way; NULs, which a browser drops from page text, are
    dropped (they would end the document inside libxml2). With ``preserve``
    (the writer) both are kept as :data:`_BYTE_ESCAPE_BASE` stand-ins instead
    — ``escaped`` says whether any were needed.
    """
    single_nul = not codec.startswith("utf-16")  # a NUL char is one 0x00 byte
    if preserve and single_nul and _BYTE_ESCAPE_RE.search(blob.decode(codec, errors="replace")):
        preserve = False  # the page really uses those code points; can't borrow them
    if codec == "cp1252":
        text = blob.decode("cp1252", errors="a508_cp1252_decode")  # never invalid
    elif preserve and single_nul:
        text = blob.decode(codec, errors="a508_byte_escape")
    else:
        text = blob.decode(codec, errors="replace")
    if "\x00" in text:
        text = text.replace("\x00", chr(_BYTE_ESCAPE_BASE) if preserve and single_nul else "")
    escaped = bool(preserve and single_nul and _BYTE_ESCAPE_RE.search(text))
    return text, escaped


def decode_html_bytes(blob: bytes, preserve_bytes: bool = False) -> Tuple[str, HtmlSource]:
    """Decode page bytes the way a browser does, and record how.

    Order (WHATWG encoding sniffing, minus the HTTP header we don't have):
      1. a byte-order mark — it beats every declaration (and BOM-less UTF-16
         markup, which only makes sense one way);
      2. ``<meta charset>`` / ``<meta http-equiv="Content-Type">`` anywhere in
         the ``<head>`` (not just before the first non-ASCII byte);
      3. an XML declaration's ``encoding=`` (XHTML saved to disk);
      4. no declaration: UTF-8 if the bytes are valid UTF-8, else
         windows-1252 — the browser default for a legacy undeclared page.

    Returns the decoded text with any leading XML declaration removed (lxml
    refuses a str that carries one) and the :class:`HtmlSource` the writer
    uses to re-encode identically. ``preserve_bytes``: see
    :func:`parse_html_source`.
    """
    bom = b""
    codec: Optional[str] = None
    declared = True
    body = blob
    for mark, name in _BOMS:
        if blob.startswith(mark):
            bom, codec, body = mark, name, blob[len(mark):]
            break
    if codec is None:
        codec = _sniff_utf16_without_bom(body)
    if codec is None:
        codec = _meta_declared_charset(body)
    if codec is None:
        xm = _XML_DECL_ENC_RE.match(body)
        if xm:
            codec = resolve_charset_label(xm.group(1))
    if codec is None:
        declared = False
        try:
            body.decode("utf-8")
            codec = "utf-8"
        except UnicodeDecodeError:
            codec = "cp1252"
    try:
        text, escaped = _decode_as(body, codec, preserve_bytes)
    except (LookupError, UnicodeError):  # pragma: no cover - codecs are allowlisted
        codec = "cp1252"
        text, escaped = _decode_as(body, codec, preserve_bytes)
    xml_decl = ""
    xd = _XML_DECL_STR_RE.match(text)
    if xd:
        xml_decl = xd.group(0)
        # Keep its line breaks so source line numbers stay the author's.
        text = "\n" * xml_decl.count("\n") + text[xd.end():]
    has_doctype = bool(_DOCTYPE_RE.match(text[:4096]))
    return text, HtmlSource(
        encoding=codec, bom=bom, xml_decl=xml_decl, has_doctype=has_doctype,
        declared=declared, ascii_only=body.isascii(), escaped=escaped,
    )


def encode_html_text(text: str, src: HtmlSource) -> bytes:
    """Encode serialized page text back into the source's own encoding.

    Inverse of :func:`decode_html_bytes`: same codec, same BOM, same XML
    declaration, and every byte stand-in back to its original byte. Characters
    the encoding can't represent (only ever ones we inserted) become numeric
    character references.

    An UNDECLARED page that was pure ASCII stays pure ASCII: a browser may
    decode it as UTF-8 or as windows-1252 (Firefox never guesses UTF-8 for a
    page served over HTTP), and only ASCII reads the same both ways. lxml
    turns ``&nbsp;``/``&copy;``/``&mdash;`` into the characters themselves, so
    writing them as raw UTF-8 put "Â " in front of every non-breaking space of
    a page that never contained a non-ASCII byte.
    """
    codec = src.encoding or "utf-8"
    if not src.declared and src.ascii_only:
        codec = "ascii"
    handler = "a508_cp1252_encode" if codec == "cp1252" else "a508_html_charref"
    full = src.xml_decl + text
    try:
        if src.escaped and _BYTE_ESCAPE_RE.search(full):
            out = bytearray()
            pos = 0
            for m in _BYTE_ESCAPE_RE.finditer(full):
                out += full[pos:m.start()].encode(codec, errors=handler)
                out += bytes(ord(c) - _BYTE_ESCAPE_BASE for c in m.group(0))
                pos = m.end()
            out += full[pos:].encode(codec, errors=handler)
            encoded = bytes(out)
        else:
            encoded = full.encode(codec, errors=handler)
    except LookupError:  # pragma: no cover - codecs are allowlisted
        return full.encode("utf-8")
    return src.bom + encoded


def _meta(el: Any, roottree: Any, ctx: Optional[Dict[str, Any]] = None) -> NodeMetadata:
    """Node metadata with the writer's xpath locator, plus contrast colours when
    a style context is supplied for a text-bearing node."""
    props: Dict[str, Any] = {"__xpath": roottree.getpath(el)}
    # The source line, so a finding can say where on the page it is (HTML
    # has no pages or coordinates for the location contract).
    line = getattr(el, "sourceline", None)
    if isinstance(line, int) and line > 0:
        props["line"] = line
    if ctx is not None:
        props.update(_contrast_props(ctx))
    return NodeMetadata(source_format="html", properties=props)


def _drive(gen: Any) -> Any:
    """Run a builder generator to completion WITHOUT Python recursion.

    Every builder below is a generator that, instead of calling a sub-builder
    directly, ``yield``s the sub-builder's generator and receives its result
    back via ``send``. This loop keeps those generators on an explicit list, so
    DOM depth costs heap, not interpreter stack — the old mutually-recursive
    builder raised RecursionError around 450 levels and the parser threw the
    whole structure away. Evaluation order (and therefore every minted node id)
    is exactly what the recursive version produced.
    """
    stack: List[Any] = [gen]
    value: Any = None
    while stack:
        try:
            request = stack[-1].send(value)
        except StopIteration as done:
            stack.pop()
            value = done.value
            continue
        stack.append(request)
        value = None
    return value


def _build_children(el: Any, ids: _Ids, roottree: Any, ctx: Dict[str, Any], depth: int):
    """Generator (see :func:`_drive`): the nodes for ``el``'s children.

    ``depth`` is how many tree nodes enclose the result; it only decides
    whether a plain wrapper still gets its own SectionNode.
    """
    out: List[Any] = []
    for child in el:
        if _budget_spent(roottree):
            break  # see _PathBudget: the document is flagged truncated
        tag = _tag(child)
        if tag is None or tag in _SKIP_TAGS:
            continue
        child_ctx = _merge_ctx(ctx, _inline_style(child))
        node = yield _build_node(child, tag, ids, roottree, child_ctx, depth)
        if isinstance(node, list):
            # A section-tag wrapper whose subtree was already walked and
            # produced nothing. Extend by that result; never walk it again.
            out.extend(node)
        elif node is not None:
            out.append(node)
        else:
            # Transparent wrapper (span, strong, label, etc.): inline any
            # accessibility-relevant descendants so inline <img>/<a> are seen,
            # carrying the wrapper's style down to them.
            out.extend((yield _build_children(child, ids, roottree, child_ctx, depth)))
    return out


_NAME_SKIP_TAGS = {"script", "style", "template", "noscript"}


def _style_hides(el: Any) -> bool:
    """True if an element's INLINE style removes it from the rendered/a11y tree."""
    decls: Dict[str, str] = {}
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            decls[k.strip().lower()] = v.strip().lower()
    return decls.get("display") == "none" or decls.get("visibility") == "hidden"


def _visible_subtree_text(el: Any) -> str:
    """Subtree text of ``el`` EXCLUDING non-rendered tags (script/style/...).

    ``element.text_content()`` would fold in CSS/JS source, so a link wrapping
    only a ``<style>``/``<script>`` would look "named". A ``.tail`` (text after a
    skipped element but still inside the link) IS rendered, so it is kept.
    """
    parts = [el.text or ""]
    for d in _elements(el):
        if d is el or not isinstance(d.tag, str):
            continue
        if d.tag.rsplit("}", 1)[-1].lower() not in _NAME_SKIP_TAGS:
            parts.append(d.text or "")
        parts.append(d.tail or "")  # tail renders regardless of the element's tag
    return "".join(parts).strip()


def _labelledby_text(el: Any, ref: str) -> str:
    """Resolve an ``aria-labelledby`` token list to the referenced elements' text.

    Returns the concatenated non-empty text of the targets (empty if none
    resolve or all are empty) — so a broken/empty reference is correctly treated
    as supplying NO name.
    """
    try:
        root = el.getroottree().getroot()
    except Exception:
        return ""
    parts: List[str] = []
    for token in ref.split():
        try:
            found = root.xpath("//*[@id=$v]", v=token)
        except Exception:
            found = []
        for target in found:
            txt = (_text(target) or "").strip()
            if txt:
                parts.append(txt)
    return " ".join(parts).strip()


def _link_is_nameless(el: Any) -> bool:
    """True iff an ``<a href>`` has NO accessible name from any source.

    Conservative (zero false-positive): returns True only when there is no
    accessible name on the ``<a>`` (``aria-label``/``title``, or an
    ``aria-labelledby`` that RESOLVES to non-empty text), no visible subtree
    text, and no descendant that supplies a name — a descendant ``<img>``/image
    ``<input>`` with non-empty ``alt``/``value``, any descendant with a resolved
    ``aria-label``/``aria-labelledby``, or an inline-SVG ``<title>``. A
    descendant ``<img>`` with a MISSING ``alt`` is deferred to the alt-text
    detection (we never double-flag), so this targets the genuinely-uncovered
    case: icon-font / inline-SVG / empty-element links.
    """
    # A link removed from the accessibility tree (aria-hidden on it or any
    # ancestor, role=presentation/none, the boolean ``hidden`` attribute, or an
    # inline display:none/visibility:hidden) is not announced at all, so a
    # missing name there is not a real defect — and a decorative aria-hidden
    # icon link duplicating a labelled one is common.
    if (el.get("role") or "").strip().lower() in {"presentation", "none"}:
        return False
    if el.get("hidden") is not None or _style_hides(el):
        return False
    for anc in (el, *_ancestors(el)):
        if isinstance(anc.tag, str) and (anc.get("aria-hidden") or "").strip().lower() == "true":
            return False
    # aria-label / title: the attribute value IS the name.
    for attr in ("aria-label", "title"):
        if (el.get(attr) or "").strip():
            return False
    # aria-labelledby: resolve the referenced ids; a broken/empty ref names nothing.
    lb = (el.get("aria-labelledby") or "").strip()
    if lb and _labelledby_text(el, lb):
        return False
    if _visible_subtree_text(el):
        return False
    for d in _elements(el):
        if d is el or not isinstance(d.tag, str):
            continue
        if (d.get("aria-label") or "").strip():
            return False
        dlb = (d.get("aria-labelledby") or "").strip()
        if dlb and _labelledby_text(d, dlb):
            return False
        local = d.tag.rsplit("}", 1)[-1].lower()
        if local == "img":
            if d.get("alt") is None:
                return False  # missing-alt image — alt-text detection covers it
            if (d.get("alt") or "").strip():
                return False  # a named image gives the link its name
            # alt="" (decorative) contributes no name — keep looking
        elif local == "input":
            itype = (d.get("type") or "").strip().lower()
            if itype in {"image", "submit", "button", "reset"} and (
                (d.get("alt") or "").strip() or (d.get("value") or "").strip()
            ):
                return False  # embedded named control contributes its name
        elif local == "title" and (d.text or "").strip():
            return False  # inline SVG <title>
    return True


def _build_node(el: Any, tag: str, ids: _Ids, roottree: Any, ctx: Dict[str, Any], depth: int):
    """Generator (see :func:`_drive`): the node for ``el``, a list (an empty
    wrapper already walked), or None (transparent — caller walks children).

    Ids are minted at the same points the recursive builder minted them
    (before the children for headings/links/paragraphs/list items/cells,
    after them for sections/rows/lists/tables).
    """
    if tag in _NODE_TAGS or (tag == "a" and el.get("href") is not None):
        # A spliced deep wrapper is never located, so it costs nothing.
        if not (tag in _SECTION_TAGS and depth >= _MAX_SECTION_DEPTH):
            _charge(roottree, ctx)
    if tag in _HEADING_TAGS:
        text = _text(el)
        content = (
            NodeContent(kind=ContentKind.TEXT, text=text)
            if text
            else NodeContent(kind=ContentKind.NONE)
        )
        hid = ids("html-h")
        meta = _meta(el, roottree, _emit_ctx(el, ctx) if text else None)
        children = yield _build_children(el, ids, roottree, ctx, depth + 1)
        return HeadingNode(
            id=hid,
            level=_HEADING_TAGS[tag],
            content=content,
            metadata=meta,
            children=children,
            accessibility_flags=[],
        )

    if tag == "img":
        return _build_image(el, ids, roottree)

    if tag == "svg":
        # Never walk an <svg>'s children as page content. It is either an
        # image (role="img") or it is left alone.
        svg = _build_svg(el, ids, roottree)
        return svg if svg is not None else []

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
        meta = _meta(el, roottree, link_ctx)
        # An element-wrapping link with no accessible name (icon-font, inline
        # SVG with no title, empty element) is flagged by LinkNameMissingAnalyzer.
        # Empty-TEXT links are handled separately by LinkTextAnalyzer.
        if content.kind != ContentKind.TEXT and _link_is_nameless(el):
            meta.properties["__link_nameless"] = True
        lid = ids("html-link")
        children = yield _build_children(el, ids, roottree, ctx, depth + 1)
        return LinkNode(
            id=lid,
            target=(el.get("href") or None),
            content=content,
            metadata=meta,
            children=children,
            accessibility_flags=[],
        )

    if tag in {"ul", "ol"}:
        return (yield _build_list(el, tag, ids, roottree, ctx, depth))

    if tag == "table":
        return (yield _build_table(el, ids, roottree, ctx, depth))

    if tag == "p":
        text = _text(el)
        content = (
            NodeContent(kind=ContentKind.TEXT, text=text)
            if text
            else NodeContent(kind=ContentKind.NONE)
        )
        meta = _meta(el, roottree, _emit_ctx(el, ctx) if text else None)
        # Facts the fake-list gate needs from the DOM (the tree node alone can't
        # see inline <strong>/<em>/<br> — they're transparent wrappers with no
        # node — so converting such a <p> to an <li> would flatten its markup).
        if _has_element_children(el):
            meta.properties["__p_has_inline_children"] = True
        if _in_code_context(el):
            meta.properties["__p_in_code_context"] = True
        pid = ids("html-p")
        children = yield _build_children(el, ids, roottree, ctx, depth + 1)
        return ParagraphNode(
            id=pid,
            content=content,
            metadata=meta,
            children=children,
            accessibility_flags=[],
        )

    if tag in _SECTION_TAGS:
        if depth >= _MAX_SECTION_DEPTH:
            # Too deep to keep a grouping level per wrapper: splice this
            # wrapper's nodes into the parent (returned as a list, which the
            # caller extends by). Content and findings are unchanged.
            return (yield _build_children(el, ids, roottree, ctx, depth))
        children = yield _build_children(el, ids, roottree, ctx, depth + 1)
        if not children:
            # Return the (already-built, empty) child list — NOT None. None
            # tells the caller "transparent wrapper, walk my subtree", and the
            # caller then rebuilt this exact subtree a second time. For a
            # chain of wrappers whose leaves yield no node (spans, icons,
            # text) that made work(k) = 2 * work(k-1): a 684-byte page with
            # 23 nested <div> took 17s, doubling per level; ~26 deep pinned a
            # request-thread forever. And /scan-url takes arbitrary public
            # URLs. Returning the list lets the caller extend by it in O(1).
            return children
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


def _figcaption_text(el: Any) -> Optional[str]:
    """The <figcaption> of the <figure> wrapping ``el`` (directly, or one
    wrapper — a link/span — up), or None."""
    parent = el.getparent()
    for holder in (parent, parent.getparent() if parent is not None else None):
        if holder is None or _tag(holder) != "figure":
            continue
        for child in holder:
            if _tag(child) == "figcaption":
                cap = " ".join((child.text_content() or "").split())
                if cap:
                    return cap[:200]
    return None


_CAPTION_SIBLING_LOOKBACK = 12


def _image_caption(el: Any) -> Optional[str]:
    """Nearby human text that describes an <img>, or None (see
    :func:`_image_caption_and_source`)."""
    found = _image_caption_and_source(el)
    return found[0] if found else None


def _image_caption_and_source(el: Any) -> Optional[Tuple[str, str]]:
    """``(text, caption_source)`` for an <img>, or None.

    1. <figure><img><figcaption>…</figcaption></figure> — "figcaption".
    2. The <img>'s title attribute (a tooltip, but authored for humans) —
       "title".
    3. The nearest PRECEDING text-bearing sibling or ancestor's preceding
       sibling — "preceding_text". This is only text NEAR the image (often a
       nav bar or a byline), so the alt executor never writes it as a
       description; recording the source is what lets it tell the two apart.
    Kept short — never the filename, never boilerplate."""
    # <figure><img><figcaption> (directly or one wrapper up); the same
    # helper names inline SVGs, so both read one definition of a caption.
    cap = _figcaption_text(el)
    if cap:
        return cap, "figcaption"
    title = (el.get("title") or "").strip()
    if title:
        return title[:200], "title"
    # Nearest preceding text: walk previous siblings of the img, then of its
    # ancestors, up to a few hops, and take the first with real words.
    # Only the few siblings right before it: a lead-in is NEXT to its image,
    # and walking every preceding sibling made each image O(page) — a long
    # page of one-word lines and many images went quadratic.
    node = el
    for _hop in range(4):
        prev = node.getprevious()
        looked = 0
        while prev is not None and looked < _CAPTION_SIBLING_LOOKBACK:
            looked += 1
            if isinstance(prev.tag, str) and prev.tag.lower() not in ("script", "style", "template", "noscript"):
                txt = " ".join((prev.text_content() or "").split())
                if len(txt.split()) >= 3:
                    return txt[:200], "preceding_text"
            prev = prev.getprevious()
        node = node.getparent()
        if node is None or _tag(node) in ("body", "html"):
            break
    return None


_SNIPPET_MAX = 200
_SNIPPET_ATTR_MAX = 60


def _start_tag_snippet(el: Any) -> str:
    """The element's start tag as the author wrote it (attribute order kept),
    e.g. ``<img src="charts/q3.png" width="400">`` — how a person finds THIS
    image in their page (an image has no text to quote). Long values (a
    data: URI) are elided; the whole snippet is capped at 200 characters."""
    tag = _svg_local(el) if isinstance(el.tag, str) else ""
    parts = [f"<{tag}"]
    for key, value in el.attrib.items():
        value = " ".join(str(value).split())
        if len(value) > _SNIPPET_ATTR_MAX:
            value = value[: _SNIPPET_ATTR_MAX - 1] + "…"
        parts.append(f' {key}="{value}"')
    snippet = "".join(parts) + ">"
    if len(snippet) > _SNIPPET_MAX:
        snippet = snippet[: _SNIPPET_MAX - 2] + "…>"
    return snippet


def _locate_props(el: Any) -> Dict[str, Any]:
    """Where an element is, for the shared location contract: its start tag
    and 1-based source line (HTML has no pages or coordinates)."""
    props: Dict[str, Any] = {"snippet": _start_tag_snippet(el)}
    line = getattr(el, "sourceline", None)
    if isinstance(line, int) and line > 0:
        props["line"] = line
    return props


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
    meta.properties.update(_locate_props(el))
    # Context for alt generation, so the heuristic provider (no AI key) can
    # derive a REAL description instead of a location placeholder — which the
    # executor now refuses to write. Prefer a <figcaption> sibling, then the
    # <img>'s own title attribute, then the nearest preceding block of text.
    if not is_decorative:
        found = _image_caption_and_source(el)
        if found:
            if meta.properties is None:
                meta.properties = {}
            meta.properties["caption"], meta.properties["caption_source"] = found

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


_SVG_MAX_NAME_WORDS = 12


def _svg_local(el: Any) -> str:
    t = el.tag if isinstance(el.tag, str) else ""
    return t.rsplit("}", 1)[-1].lower()


def _svg_visible_text(svg: Any) -> str:
    """The words an inline SVG DRAWS: its <text>/<tspan>/<textPath> content.

    <title>/<desc> are the accessible name/description, not drawn text, and
    <style>/<script> are source code — none of them count.
    """
    parts: List[str] = []
    for d in _elements(svg):
        if not isinstance(d.tag, str):
            continue
        if _svg_local(d) in ("text", "tspan", "textpath"):
            # Only the element's own text; its <tspan> children are visited
            # themselves, and a tail belongs to the parent element.
            if d.text and d.text.strip():
                parts.append(d.text)
            for c in d:
                if isinstance(c.tag, str) and c.tail and c.tail.strip():
                    parts.append(c.tail)
    return " ".join(" ".join(parts).split())


def _svg_accessible_name(svg: Any) -> str:
    """aria-label, a resolving aria-labelledby, or a DIRECT-child <title>."""
    label = (svg.get("aria-label") or "").strip()
    if label:
        return label
    lb = (svg.get("aria-labelledby") or "").strip()
    if lb:
        txt = _labelledby_text(svg, lb)
        if txt:
            return txt
    for child in svg:
        if isinstance(child.tag, str) and _svg_local(child) == "title":
            txt = " ".join((child.text_content() or "").split())
            if txt:
                return txt
    return ""


def _build_svg(el: Any, ids: _Ids, roottree: Any) -> Optional[ImageNode]:
    """An inline ``<svg>`` as an image, or None when it is not one we judge.

    Emitted ONLY for ``role="img"`` — the author declared it one image, so
    assistive tech treats its children as presentational and it needs a name
    of its own (WCAG 1.1.1, axe's svg-img-alt). An SVG WITHOUT that role is
    not judged: its drawn words (<text>) are already exposed to a screen
    reader as text — a badge that draws "Fees waived" is read as "Fees
    waived" — so there is no missing alternative to report, and "fixing" it
    with role="img" + a label would hide those words behind whatever the label
    says. A plain icon (no role, no text) could be decorative or not, so we
    make no claim about it either. SVGs hidden from assistive tech
    (aria-hidden, role=presentation/none, ``hidden``, inline display:none)
    are skipped, and so are SVGs inside a link or button — the control's name
    rules own those.

    An unnamed ``role="img"`` SVG raises MISSING_ALT_TEXT like an ``<img>``
    with no alt; the writer's fix is ``aria-label``. The words it draws —
    hidden from assistive tech by the role — are offered as the grounded
    ``caption`` when they are short enough to read as a name (a chart's axis
    labels are not a description, so long text gets no caption).
    """
    role = (el.get("role") or "").strip().lower()
    if role != "img":
        return None
    if el.get("hidden") is not None or _style_hides(el):
        return None
    for anc in (el, *_ancestors(el)):
        if not isinstance(anc.tag, str):
            continue
        if (anc.get("aria-hidden") or "").strip().lower() == "true":
            return None
        if anc is not el and _tag(anc) in ("a", "button"):
            return None
    drawn = _svg_visible_text(el)
    name = _svg_accessible_name(el)
    props: Dict[str, Any] = {"__xpath": roottree.getpath(el), "svg_inline": True, **_locate_props(el)}
    if drawn:
        props["svg_text"] = drawn[:200]
        if len(drawn.split()) <= _SVG_MAX_NAME_WORDS and any(c.isalpha() for c in drawn):
            props["caption"] = drawn[:200]
    if "caption" not in props:
        cap = _figcaption_text(el)
        if cap:
            props["caption"] = cap
    return ImageNode(
        id=ids("html-img"),
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(source_format="html", properties=props),
        children=[],
        accessibility_flags=[],
        is_decorative=False,
        alt_text=name or None,
    )


def _build_list(el: Any, tag: str, ids: _Ids, roottree: Any, ctx: Dict[str, Any], depth: int):
    """Generator (see :func:`_drive`)."""
    items: List[Any] = []
    for li in el:
        if _budget_spent(roottree):
            break
        if _tag(li) != "li":
            continue
        li_ctx = _merge_ctx(ctx, _inline_style(li))
        _charge(roottree, li_ctx)
        text = _text(li)
        content = (
            NodeContent(kind=ContentKind.TEXT, text=text)
            if text
            else NodeContent(kind=ContentKind.NONE)
        )
        li_id = ids("html-li")
        li_meta = _meta(li, roottree, _emit_ctx(li, li_ctx) if text else None)
        li_children = yield _build_children(li, ids, roottree, li_ctx, depth + 2)
        items.append(
            ListItemNode(
                id=li_id,
                content=content,
                metadata=li_meta,
                children=li_children,
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


def _build_table(el: Any, ids: _Ids, roottree: Any, ctx: Dict[str, Any], depth: int):
    """Generator (see :func:`_drive`)."""
    rows_nodes: List[Any] = []
    for tr in _table_rows(el):
        if _budget_spent(roottree):
            break
        tr_ctx = _merge_ctx(ctx, _inline_style(tr))
        _charge(roottree, tr_ctx)
        cells: List[Any] = []
        for cell_el in tr:
            if _budget_spent(roottree):
                break
            ct = _tag(cell_el)
            if ct not in {"td", "th"}:
                continue
            cell_ctx = _merge_ctx(tr_ctx, _inline_style(cell_el))
            _charge(roottree, cell_ctx)
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
            cell_id = ids("html-cell")
            cell_meta = _meta(cell_el, roottree, _emit_ctx(cell_el, cell_ctx) if text else None)
            cell_children = yield _build_children(cell_el, ids, roottree, cell_ctx, depth + 3)
            cells.append(
                TableCellNode(
                    id=cell_id,
                    cell_type=TableCellType.HEADER if is_header else TableCellType.DATA,
                    header_scope=scope,
                    content=content,
                    metadata=cell_meta,
                    children=cell_children,
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
    # Preserve an explicit layout-table signal so the caption analyzer can skip
    # role="presentation"/"none" tables (they convey no tabular data).
    role = (el.get("role") or "").strip().lower()
    if role:
        meta.properties["role"] = role
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


def _build_labels_for(doc: Any) -> set:
    """The set of ids targeted by a ``<label for=...>`` (an associated label)."""
    labels_for: set = set()
    for lbl in doc.iter("label"):
        target = lbl.get("for")
        if target:
            labels_for.add(target)
    return labels_for


def _iter_labelable_controls(doc: Any):
    """Yield every labelable form control in document order.

    Iterating tag-by-tag (all inputs, then selects, then textareas) is a fixed,
    deterministic order shared by the counter and the writer, so the count we
    claim equals what gets labeled.
    """
    for tag in _FORM_CONTROL_TAGS:
        for ctrl in doc.iter(tag):
            if tag == "input":
                itype = (ctrl.get("type") or "text").strip().lower()
                if itype in _NONLABELABLE_INPUT_TYPES:
                    continue
            yield ctrl


def _count_form_fields(doc: Any) -> Tuple[int, int, int]:
    """Return ``(total, unlabeled, derivable)`` labelable form controls.

    ``derivable`` is how many of the unlabeled controls have a confident nearby
    label we can auto-write (see :func:`_derive_html_label`); the rest stay
    manual.
    """
    labels_for = _build_labels_for(doc)
    total = 0
    unlabeled = 0
    derivable = 0
    for ctrl in _iter_labelable_controls(doc):
        total += 1
        if _control_has_accessible_name(ctrl, labels_for):
            continue
        unlabeled += 1
        if _derive_html_label(ctrl, labels_for):
            derivable += 1
    return total, unlabeled, derivable


# ---------------------------------------------------------------------------
# WCAG 1.3.5 — Identify Input Purpose (autocomplete)
# ---------------------------------------------------------------------------

# EXACT identifier -> the WCAG-listed autocomplete token.
#
# These are matched EXACTLY against a normalized field identifier — never as
# substrings. An adversarial review proved substring matching is actively
# dangerous here: "mobile" lives inside "automobile", "lname" inside
# "hotel_name"/"model_name", "company" inside "accompanying", "zip" inside
# "zip_file", "email" inside "email_subject". Each of those wrote a
# personal-data token onto an unrelated field, so the browser would silently
# prefill the user's real name/phone/address into the wrong box — strictly worse
# than the missing attribute we set out to fix.
_AUTOCOMPLETE_EXACT: Dict[str, str] = {
    # email
    "email": "email", "emailaddress": "email", "emailaddr": "email",
    "mail": "email", "mailaddress": "email", "useremail": "email",
    # names
    "firstname": "given-name", "givenname": "given-name", "fname": "given-name",
    "forename": "given-name",
    "lastname": "family-name", "familyname": "family-name", "surname": "family-name",
    "lname": "family-name",
    "fullname": "name", "yourname": "name", "name": "name",
    # phone
    "phone": "tel", "phonenumber": "tel", "telephone": "tel", "telephonenumber": "tel",
    "tel": "tel", "mobile": "tel", "mobilenumber": "tel", "mobilephone": "tel",
    "cellphone": "tel", "cell": "tel",
    # address
    "streetaddress": "street-address", "street": "street-address",
    "address": "street-address",
    "address1": "address-line1", "addressline1": "address-line1",
    "address2": "address-line2", "addressline2": "address-line2",
    "postalcode": "postal-code", "zipcode": "postal-code", "postcode": "postal-code",
    "zip": "postal-code",
    "country": "country-name", "countryname": "country-name",
    # org / misc
    "organization": "organization", "organisation": "organization",
    "organizationname": "organization", "company": "organization",
    "companyname": "organization", "employer": "organization",
    "birthday": "bday", "dateofbirth": "bday", "dob": "bday", "bday": "bday",
    "username": "username", "userid": "username",
}

# Noise wrappers commonly put AROUND a real purpose token ("user_email",
# "billing_zip"). Stripped only at the ends, so the anchor is preserved.
_AUTOCOMPLETE_AFFIXES = (
    "user", "contact", "billing", "shipping", "home", "work", "your", "my",
    "customer", "input", "field", "txt", "text", "the",
)

# <input type> that maps directly, regardless of the field's name.
_AUTOCOMPLETE_BY_TYPE = {"email": "email", "tel": "tel"}
# Fields we must NEVER guess for: a wrong token here is a security/UX hazard.
_AUTOCOMPLETE_SKIP_TYPES = {
    "password", "hidden", "submit", "button", "reset", "image", "file",
    "checkbox", "radio", "search", "range", "color",
}
# Only these types can carry a personal-data purpose. url/number are excluded:
# a URL field is never a person's name or postal code, and a number field is
# never a name — including them could ONLY produce wrong tokens.
_AUTOCOMPLETE_OK_TYPES = {"text", "tel", "email", ""}

# A field holding SOMEONE ELSE'S data is out of scope for WCAG 1.3.5, which is
# explicitly about "collecting information about the USER". Autofilling the
# user's own details there is wrong.
_THIRD_PARTY_MARKERS = (
    "recipient", "friend", "colleague", "referral", "refer", "guest", "invitee",
    "emergency", "nextofkin", "beneficiary", "dependent", "spouse", "parent",
    "child", "employee", "candidate", "patient", "client", "student", "member",
    "sender", "to", "cc", "bcc",
)


def _normalize_ident(raw: str) -> str:
    """camelCase/snake_case/kebab -> a bare lowercase identifier."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw or "")
    return re.sub(r"[^a-z0-9]+", "", spaced.lower())


def _strip_affixes(ident: str) -> str:
    changed = True
    while changed:
        changed = False
        for aff in _AUTOCOMPLETE_AFFIXES:
            if ident.startswith(aff) and len(ident) > len(aff):
                ident, changed = ident[len(aff):], True
            elif ident.endswith(aff) and len(ident) > len(aff):
                ident, changed = ident[: -len(aff)], True
    return ident


def _autocomplete_token_for(ctrl: Any) -> Optional[str]:
    """The unambiguous autocomplete token for a control, or None.

    EXACT-matches a normalized ``name``/``id`` (each independently — never
    concatenated, since joining two attributes manufactures adjacencies present
    in neither). Anything not an exact, known purpose is left alone: a missing
    autocomplete is a WCAG 1.3.5 warning, a wrong one autofills the user's real
    personal data into the wrong field.
    """
    if not isinstance(ctrl.tag, str) or ctrl.tag.lower() != "input":
        return None  # <select>/<textarea> purposes are far less predictable
    itype = (ctrl.get("type") or "text").strip().lower()
    if itype in _AUTOCOMPLETE_SKIP_TYPES:
        return None
    if (ctrl.get("autocomplete") or "").strip():
        return None  # already declared (including autocomplete="off")

    # WCAG 1.3.5 covers the USER'S OWN data. A "recipient email" or "emergency
    # contact phone" collects someone else's, so we must not autofill it.
    scope_hay = _normalize_ident(" ".join((ctrl.get(a) or "") for a in ("name", "id")))
    if any(marker in scope_hay for marker in _THIRD_PARTY_MARKERS if len(marker) > 2):
        return None

    by_type = _AUTOCOMPLETE_BY_TYPE.get(itype)
    if by_type:
        return by_type
    if itype not in _AUTOCOMPLETE_OK_TYPES:
        return None
    # ``placeholder`` is deliberately NOT consulted: it is prose ("we'll never
    # share your email"), not an identifier, and matching it turns search boxes
    # and free-text notes into personal-data fields.
    for attr in ("name", "id"):
        token = _AUTOCOMPLETE_EXACT.get(_strip_affixes(_normalize_ident(ctrl.get(attr) or "")))
        if token:
            return token
    return None


def iter_autocomplete_candidates(doc: Any):
    """Yield ``(input_element, token)`` for inputs whose purpose is unambiguous.

    Shared by the parser (which COUNTS them) and the writer (which APPLIES
    them) so the number we claim is exactly the number we write — the same
    honesty contract as :func:`iter_derivable_form_labels`.
    """
    for ctrl in doc.iter("input"):
        token = _autocomplete_token_for(ctrl)
        if token:
            yield ctrl, token


# ---------------------------------------------------------------------------
# WCAG 2.4.3 — Focus Order (positive tabindex)
# ---------------------------------------------------------------------------


def iter_positive_tabindex(doc: Any):
    """Yield elements with a POSITIVE tabindex.

    ``tabindex="3"`` yanks an element out of DOM order and to the front of the
    whole page's tab sequence, so keyboard focus jumps unpredictably. The fix is
    always the same and is safe: ``tabindex="0"`` keeps the element focusable
    but restores natural order. ``tabindex="-1"`` (programmatic focus) and
    ``tabindex="0"`` are both fine and never yielded.
    """
    for el in _elements(doc):
        if not isinstance(el.tag, str):
            continue
        raw = (el.get("tabindex") or "").strip()
        if not raw:
            continue
        try:
            if int(raw) > 0:
                yield el
        except ValueError:
            continue


# ---------------------------------------------------------------------------
# WCAG 4.1.2 / 2.4.1 — frames need an accessible name
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# WCAG 2.5.3 — Label in Name
# ---------------------------------------------------------------------------

_LABEL_IN_NAME_TAGS = ("a", "button")
_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)


def _speech_normalize(text: str) -> str:
    """Lowercase, strip punctuation/whitespace — how a voice command is matched."""
    return re.sub(r"\s+", " ", _PUNCT_RE.sub(" ", (text or "").lower())).strip()


def count_label_in_name_mismatches(doc: Any) -> int:
    """Controls whose accessible name omits their own VISIBLE text (WCAG 2.5.3).

    Speech-input users activate a control by saying the words they can see
    ("click Submit order"). If the ``aria-label`` says something different, the
    spoken command doesn't match the accessible name and the control simply
    cannot be operated by voice — a Level A failure, and one that a
    well-meaning ``aria-label`` usually CAUSES.

    Deliberately narrow, so this is a fact rather than a judgement: we only look
    at controls that have BOTH visible text and an explicit ``aria-label``, and
    flag only when the visible text is not contained in the label at all. An
    ``aria-label`` that merely ADDS context ("Read more about pensions" over
    "Read more") is correct and is never flagged.
    """
    n = 0
    for tag in _LABEL_IN_NAME_TAGS:
        for el in doc.iter(tag):
            label = (el.get("aria-label") or "").strip()
            if not label:
                continue  # no override -> the visible text IS the name
            visible = _speech_normalize(_visible_subtree_text(el))
            if not visible:
                continue  # icon-only control: LINK_NAME_MISSING's territory
            if len(visible) < 2:
                continue  # single character ("x", ">") — not a spoken command
            if _speech_normalize(label).find(visible) == -1:
                n += 1
    return n


def count_untitled_iframes(doc: Any) -> int:
    """How many <iframe>/<frame> elements have no accessible name.

    An embedded map, video or widget with no ``title`` is announced only as
    "frame", so a screen reader user cannot tell what is inside or whether to
    enter it. Frames hidden from the a11y tree don't count.
    """
    n = 0
    for tag in ("iframe", "frame"):
        for el in doc.iter(tag):
            if (el.get("title") or "").strip():
                continue
            if (el.get("aria-label") or "").strip():
                continue
            if (el.get("aria-labelledby") or "").strip():
                continue
            # Explicitly removed from the a11y tree — nothing to name.
            if (el.get("role") or "").strip().lower() in {"presentation", "none"}:
                continue
            if el.get("hidden") is not None:
                continue
            if _style_hides(el):
                continue
            # aria-hidden or display:none on ANY ancestor also removes it.
            if any(
                isinstance(a.tag, str)
                and (
                    (a.get("aria-hidden") or "").strip().lower() == "true"
                    or a.get("hidden") is not None
                    or _style_hides(a)
                )
                for a in _ancestors(el)
            ):
                continue
            # A 0x0 / 1x1 frame is a tracking pixel or a hidden RPC channel, not
            # content a user could enter — naming it would be noise.
            try:
                w = int(float((el.get("width") or "").strip() or -1))
                h = int(float((el.get("height") or "").strip() or -1))
                if 0 <= w <= 1 and 0 <= h <= 1:
                    continue
            except (TypeError, ValueError):
                pass
            n += 1
    return n


def iter_derivable_form_labels(doc: Any):
    """Yield ``(control_element, label_text)`` for every unlabeled control that
    has a confident nearby label.

    The html_writer calls this on a fresh re-parse of the SAME bytes the parser
    saw, so the controls, order, and derived text are identical to what
    ``_count_form_fields`` counted — the writer never labels more (or fewer)
    than the ``form_fields_derivable`` count the executor reported.
    """
    labels_for = _build_labels_for(doc)
    for ctrl in _iter_labelable_controls(doc):
        if _control_has_accessible_name(ctrl, labels_for):
            continue
        text = _derive_html_label(ctrl, labels_for)
        if text:
            yield ctrl, text


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


# Generic placeholder prompts that name no field, plus stopwords / heading
# prefixes. Mirrors the DOCX content-control rules in
# ``docx_parser._clean_form_label`` so "a wrong label is worse than none" means
# the same thing in every format (kept local to avoid importing python-docx).
_HTML_GENERIC_PROMPTS = {
    "search",
    "search…",
    "search...",
    "enter text",
    "type here",
    "choose an item",
    "select an item",
    "choose a date",
}
_HTML_LABEL_STOPWORDS = {"n/a", "na", "tbd", "required", "optional", "yes", "no"}
_HTML_HEADING_PREFIX_RE = re.compile(
    r"^(section|part|chapter|article|step|appendix)\b", re.IGNORECASE
)
# Leading imperative verbs mark page INSTRUCTION text ("Please fill in", "Enter
# your name"), not a field's name — reject so we never name a field after an
# instruction. (A 1-word label like "Note" is unaffected: these all read as
# instructions only as a phrase's first word.)
_HTML_INSTRUCTION_PREFIXES = {
    "please", "enter", "type", "select", "choose", "complete", "fill",
    "provide", "ensure", "upload", "confirm", "kindly", "specify",
}
# A <legend> is a <fieldset> GROUP caption and an <h1>..<h6> is a section title —
# neither is any single control's accessible name. Treat them as label
# boundaries so their text is never harvested as a field label.
_HTML_LABEL_BOUNDARY_TAGS = set(_HEADING_TAGS) | {"legend"}
# Controls whose visible label conventionally sits AFTER the control and whose
# group caption is not a valid per-option name — auto-deriving from preceding
# text reliably mislabels them, so they stay manual.
_HTML_GROUPED_INPUT_TYPES = {"radio", "checkbox"}


def _clean_html_label(text: str, max_len: int = 60) -> Optional[str]:
    """Normalize candidate label text, or None if it isn't label-like.

    A real field label is a short noun phrase, optionally ending in a colon —
    not a sentence, question, instruction, heading or generic prompt.
    Deliberately conservative: a wrong accessible name is worse than none.
    """
    t = (text or "").strip()
    # Strip leading bullet/asterisk/dash/colon artifacts ("* Required", "- Name").
    t = re.sub(r"^[\s•\*\-–—·:]+", "", t)
    # Collapse interior whitespace and non-breaking spaces so the derived name
    # matches the visible single-spaced label (\s does not match U+00A0).
    t = re.sub(r"\s+", " ", t.replace("\xa0", " ")).strip()
    # Strip a trailing colon, then trailing currency/unit/operator decoration
    # ("Amount $" -> "Amount") — only trailing symbol runs, never interior chars.
    t = t.rstrip().rstrip(":").strip()
    t = t.rstrip(" $€£¥%#*").strip()
    if not t or len(t) > max_len:
        return None
    if not any(c.isalpha() for c in t):
        return None
    low = t.lower()
    if low in _HTML_GENERIC_PROMPTS or low in _HTML_LABEL_STOPWORDS:
        return None
    words = t.split()
    # An imperative first word marks instruction text, not a field name
    # ("All fields required", "Please fill in") — even without end punctuation.
    first = words[0].lower().strip(".,;:!?") if words else ""
    if first in _HTML_INSTRUCTION_PREFIXES or first == "all":
        return None
    # A trailing annotation ("... required" / "... optional") is not a name.
    last = words[-1].lower().strip(".,;:!?") if words else ""
    if last in {"required", "optional"}:
        return None
    if t.endswith((".", "?", "!")):  # sentences / questions / instructions
        return None
    if "." in t and " " in t:  # an internal period with spaces reads as prose
        return None
    if _HTML_HEADING_PREFIX_RE.match(t):  # "Section 4 Employment" is a heading
        return None
    if len(words) > 6:  # a field label is short
        return None
    return t


def _wraps_control(el: Any) -> bool:
    """True if ``el`` contains a form control in its subtree."""
    for tag in _FORM_CONTROL_TAGS:
        if el.find(".//" + tag) is not None:
            return True
    return False


def _cell_text_excluding_controls(el: Any) -> str:
    """All text inside ``el`` except text that belongs to a form control.

    A label cell like ``<td>Email <input></td>`` should yield "Email", not the
    control's own placeholder/value text.
    """
    parts: List[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        if _tag(child) not in _FORM_CONTROL_TAGS:
            parts.append(_cell_text_excluding_controls(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _shared_with_following_control(ctrl: Any, parent: Any) -> bool:
    """True if the next labelable control after ``ctrl`` (same parent) has no
    clean label of its own between them.

    That means ``ctrl``'s preceding label is really a GROUP label shared by both
    controls (e.g. one "Legal name" over a first/last pair), so neither should
    claim it as its individual accessible name.
    """
    after = False
    pending = False  # have we seen a clean own-label for the following control?
    for child in parent:
        if child is ctrl:
            after = True
            if child.tail and _clean_html_label(child.tail):
                pending = True
            continue
        if not after:
            continue
        ctag = _tag(child)
        if ctag in _FORM_CONTROL_TAGS:
            labelable = True
            if ctag == "input":
                itype = (child.get("type") or "text").strip().lower()
                if itype in _NONLABELABLE_INPUT_TYPES:
                    labelable = False
            if labelable:
                return not pending
            # a non-labelable control (submit/hidden) is transparent here
        elif ctag in _HTML_LABEL_BOUNDARY_TAGS:
            pass  # legend/heading is never a field's own label
        elif ctag == "label":
            if not (child.get("for") or "").strip() and not _wraps_control(child):
                if _clean_html_label(child.text_content()):
                    pending = True
        elif ctag is not None:
            if _clean_html_label(child.text_content() or ""):
                pending = True
        if child.tail and _clean_html_label(child.tail):
            pending = True
    return False


def _derive_html_label(ctrl: Any, labels_for: set) -> Optional[str]:
    """Best-confidence accessible name for an unlabeled HTML control, or None.

    Mirrors the DOCX content-control deriver. Three high-precision sources, in
    order; the writer applies the result as an ``aria-label``:

      1. An orphan ``<label>`` (text, no ``for``, not wrapping a control)
         appearing in the same parent before this control.
      2. Visible label-like text immediately before the control in the same
         parent ("Name: [input]"), measured only SINCE the previous control so
         the 2nd control in "Name: [ ] Date: [ ]" derives "Date".
      3. The table cell immediately left of the control's cell.

    Declines (returns None — leave for manual review) for grouped option
    controls (radio/checkbox), for a label/text shared by >1 control (a GROUP
    label), and whenever a ``<legend>``, heading, or ``<label for=other>`` would
    otherwise leak its text — so we never invent a misleading name.
    """
    try:
        # Grouped option controls: the visible label sits AFTER the control and
        # a group question is not a per-option name — auto-deriving from the
        # preceding sibling reliably mislabels, so leave them manual.
        if _tag(ctrl) == "input":
            itype = (ctrl.get("type") or "text").strip().lower()
            if itype in _HTML_GROUPED_INPUT_TYPES:
                return None
        parent = ctrl.getparent()
        if parent is None:
            return None
        # --- Sources 1 + 2: inline content in the same parent, since the last
        # control boundary. ---
        orphan_label: Optional[str] = None
        preceding: List[str] = []
        if parent.text and parent.text.strip():
            preceding.append(parent.text)
        for child in parent:
            if child is ctrl:
                break
            ctag = _tag(child)
            if ctag in _FORM_CONTROL_TAGS or ctag in _HTML_LABEL_BOUNDARY_TAGS:
                # A preceding control ends the previous label's scope; a
                # <legend>/heading is a group/section caption, never a field
                # name — both reset the scope and contribute no text.
                orphan_label = None
                preceding = []
            elif ctag == "label":
                if (child.get("for") or "").strip():
                    pass  # owned by another control via for= — never harvest it
                elif not _wraps_control(child):
                    cleaned = _clean_html_label(child.text_content())
                    if cleaned:
                        orphan_label = cleaned
            elif ctag is not None:  # skip comments / PIs (no text_content)
                txt = child.text_content()
                if txt:
                    preceding.append(txt)
            if child.tail and child.tail.strip():
                preceding.append(child.tail)
        # A single label shared by >1 unlabeled control is a GROUP label (e.g.
        # "Legal name" over first/last) — decline so we never mislabel one
        # arbitrary member with it.
        shared = _shared_with_following_control(ctrl, parent)
        if orphan_label and not shared:
            return orphan_label
        inline = _clean_html_label("".join(preceding))
        if inline and not shared:
            return inline
        # --- Source 3: the cell immediately left of the control's cell. ---
        cell = ctrl
        while cell is not None and _tag(cell) not in ("td", "th"):
            cell = cell.getparent()
        if cell is not None:
            row = cell.getparent()
            if row is not None and _tag(row) == "tr":
                prev_cell = None
                for c in row:
                    if _tag(c) not in ("td", "th"):
                        continue
                    if c is cell:
                        break
                    prev_cell = c
                if prev_cell is not None:
                    label = _clean_html_label(_cell_text_excluding_controls(prev_cell))
                    if label:
                        return label
    except Exception:
        return None
    return None


_FAKE_LIST_MAX_WORDS = 12


def _set_fake_list_signature(node: Any) -> None:
    """Tag a plain-text ``<p>`` node with its typed-list signature, if any.

    Conservative — a wrong conversion (or lost markup) is worse than leaving a
    typed list as plain paragraphs. A paragraph is eligible ONLY when it is:
      * a ParagraphNode with no child nodes AND no inline DOM element children
        (so converting to <li> from its text can't drop <strong>/<em>/<br>…);
      * not inside <pre>/<code> (a leading "- " there is a diff/code marker);
      * short — real list items are fragments, not full sentences (this keeps
        enumerated prose like "1. <long sentence>." out of an <ol>).
    """
    if not isinstance(node, ParagraphNode) or node.children:
        return
    props_in = node.metadata.properties or {}
    if props_in.get("__p_has_inline_children") or props_in.get("__p_in_code_context"):
        return
    if not (node.content and node.content.kind == ContentKind.TEXT and node.content.text):
        return
    text = node.content.text
    sig = _fake_list_signature(text)
    if sig is None:
        return
    if len(strip_fake_list_prefix(text).split()) > _FAKE_LIST_MAX_WORDS:
        return  # reads as prose, not a list item
    kind, char, ordinal = sig
    props = dict(props_in)
    props["fake_list_kind"] = kind
    props["fake_list_char"] = char
    if ordinal is not None:
        props["fake_list_ordinal"] = ordinal
    node.metadata.properties = props


def _mark_html_fake_lists(nodes: List[Any]) -> None:
    """Mark runs of consecutive typed-list paragraphs at every nesting level.

    Sets each member's signature, then reuses the shared
    :func:`_group_fake_list_runs` to record ``fake_list_run_ids`` on the first
    node of every >=2-item run.
    """
    # Iterative (explicit stack of sibling lists) — the tree can be deep.
    pending: List[List[Any]] = [nodes]
    while pending:
        siblings = pending.pop()
        for node in siblings:
            _set_fake_list_signature(node)
        _group_fake_list_runs(siblings)
        for node in siblings:
            if getattr(node, "children", None):
                pending.append(node.children)


__all__ = ["HTMLParser"]
