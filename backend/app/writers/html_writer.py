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
IMPROVE_LINK_TEXT, ADD_TABLE_HEADERS, FIX_CONTRAST, FILL_FORM_FIELD_LABELS,
FIX_LIST_STRUCTURE, GENERATE_TABLE_CAPTION}. Every one is persisted here. A locator that unexpectedly fails to resolve is
logged and recorded in ``skipped`` (it should never happen for parser-produced
nodes on a same-bytes re-parse).
"""

from __future__ import annotations

import re

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
    ParagraphNode,
    TableCellNode,
    TableCellType,
    TableHeaderScope,
    TableNode,
    TableRowNode,
    iter_reading_order,
)
from app.parsers.html_parser import (
    HtmlSource,
    _parse_document,
    _svg_accessible_name,
    encode_html_text,
    iter_autocomplete_candidates,
    iter_derivable_form_labels,
    iter_positive_tabindex,
    parse_html_source,
)

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

    # The tree the ANALYSIS parsed (no byte stand-ins), when it isn't ``doc``
    # itself. "Did an executor change this value?" is only answerable against
    # what the analysis read: the byte-preserving parse holds a stand-in where
    # the analysis read U+FFFD (an invalid byte) or nothing (a NUL), so
    # comparing against it saw a change in every title, link and alt holding
    # one — and rewrote them (destroying the very byte preserve_bytes keeps)
    # and reported fixes nobody approved.
    seen_doc = None
    try:
        data = Path(source_path).read_bytes()
        # preserve_bytes: a NUL or an invalid byte in the source goes back out
        # as that same byte (see parse_html_source) — never dropped, never
        # turned into U+FFFD.
        doc, source_info = parse_html_source(data, preserve_bytes=True)
        if source_info.escaped:
            # The stand-ins are ordinary characters to lxml. In text they are
            # harmless, but one INSIDE markup ("<im\0g>", a UTF-32 file) changes
            # the tree — "<" followed by a stand-in is text, not a tag — and
            # the analysis's locators (built without them) would then point
            # at the wrong elements, or the page's markup would be written
            # back escaped. Keep exact bytes only when the two parses agree
            # element for element; otherwise write the tree the analysis saw.
            plain_doc, plain_info = parse_html_source(data)
            if _tag_sequence(plain_doc) != _tag_sequence(doc):
                doc, source_info = plain_doc, plain_info
            else:
                seen_doc = plain_doc
    except Exception as exc:
        logger.exception("html_writer parse failed: %s", exc)
        # Signal a hard failure so the pipeline cleans up and does NOT charge.
        return {"applied": [], "skipped": [{"target_id": str(source_path), "reason": "failed_to_open: html"}]}
    if source_info.fallback or source_info.blank:
        # lxml could not build a document from these bytes (or there were no
        # bytes worth building) and the parser substituted an EMPTY one.
        # Serialising that would replace the customer's file with a skeleton
        # page; leave the copy untouched.
        return {"applied": [], "skipped": [{"target_id": str(source_path), "reason": "failed_to_open: html"}]}

    path_index = _PathIndex(doc)

    def resolve(xpath: Optional[str]) -> Optional[Any]:
        # Locators are getpath() output; walk them against a per-parent child
        # index (O(depth) each). doc.xpath() re-scans a parent's children for
        # every positional step — O(N) per lookup on a page with N siblings,
        # so O(N^2) for a long table — and libxml2's evaluator fails outright
        # ("unknown error") on a long path ending in a positional predicate
        # (a cell ~1000 wrappers deep), which silently skipped the fix.
        # Every lookup happens on the PRISTINE DOM (phase 1), so the index
        # never goes stale.
        if not xpath:
            return None
        el = path_index.resolve(xpath)
        if el is not None:
            return el
        try:
            found = doc.xpath(xpath)
        except Exception:
            found = []
        return found[0] if len(found) == 1 else None

    # The same locators against the analysis's own tree (identical element for
    # element, see above), for reading the values the analysis saw.
    seen_index = _PathIndex(seen_doc) if seen_doc is not None else None

    def seen(xpath: Optional[str], el: Any) -> Any:
        if seen_index is None or el is None or not xpath:
            return el
        found = seen_index.resolve(xpath)
        return found if found is not None else el

    root = tree.root
    props = root.metadata.properties or {}

    # ---- PHASE 1: resolve all locators on the PRISTINE DOM (before mutating) ----
    elements: Dict[str, Any] = {}
    seen_elements: Dict[str, Any] = {}
    for node in iter_reading_order(root):
        if not isinstance(node, _LOCATABLE):
            continue
        nprops = node.metadata.properties or {}
        if nprops.get("synthesized"):
            continue  # synthetic nodes have no source element by design
        xpath = nprops.get("__xpath")
        el = resolve(xpath)
        elements[node.id] = el
        seen_elements[node.id] = seen(xpath, el)
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

    # Fake-list members (typed "- item" <p> the executor approved for
    # FIX_LIST_STRUCTURE) — resolve on the PRISTINE DOM so the later <ul>/<ol>
    # surgery uses held references that survive earlier retag/insert mutations.
    list_member_els: Dict[str, Any] = {}
    nodes_by_id: Dict[str, Any] = {}
    for node in iter_reading_order(root):
        nodes_by_id[node.id] = node
        nprops = node.metadata.properties or {}
        if nprops.get("convert_to_list") and not nprops.get("synthesized"):
            list_member_els[node.id] = resolve(nprops.get("__xpath"))

    html_el = resolve(props.get("__html_xpath"))
    seen_html = seen(props.get("__html_xpath"), html_el)
    if html_el is None:
        html_el = seen_html = doc
    title_el = resolve(props.get("__title_xpath"))
    seen_title = seen(props.get("__title_xpath"), title_el)
    path_index.release()  # every locator is resolved; nothing reads it again
    if seen_index is not None:
        seen_index.release()

    # ---- PHASE 2: mutate via the held references ----
    # Each value is compared with what the ANALYSIS read (the parser strips
    # them), so only a value an executor changed is written and reported —
    # not a title/lang/alt that merely has surrounding spaces, or a byte the
    # analysis could not decode.
    language = root.metadata.language
    if isinstance(language, str) and language.strip() and html_el is not None:
        if ((seen_html.get("lang") or "").strip() or None) != language:
            html_el.set("lang", language)
            applied.append({"action": "SET_DOCUMENT_LANGUAGE", "target_id": root.id})

    title = props.get("title")
    if isinstance(title, str) and title.strip():
        if title_el is None:
            title_el = _ensure_title_element(doc)
            seen_title = None
        current_title = (seen_title.text or "").strip() if seen_title is not None else ""
        if title_el is not None and current_title != title:
            title_el.text = title
            applied.append({"action": "SET_DOCUMENT_TITLE", "target_id": root.id})

    for node in iter_reading_order(root):
        if isinstance(node, ImageNode):
            _apply_image(node, elements.get(node.id), seen_elements.get(node.id), applied)
        elif isinstance(node, HeadingNode):
            _apply_heading(node, elements.get(node.id), applied)
        elif isinstance(node, LinkNode):
            _apply_link(node, elements.get(node.id), seen_elements.get(node.id), applied)
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

    # Form-field labeling: when the executor approved it, give each unlabeled
    # control with a CONFIDENT nearby label an aria-label. iter_derivable_form_labels
    # re-derives with the same helper the parser used (on these same bytes), so we
    # never label more than the form_fields_derivable count the executor reported
    # — keeping the honesty invariant. Ambiguous controls are left for manual work.
    if props.get("apply_form_field_labels"):
        for ctrl, label_text in iter_derivable_form_labels(doc):
            if (ctrl.get("aria-label") or "").strip():
                continue  # already has an accessible name — never clobber
            ctrl.set("aria-label", label_text)
            applied.append({"action": "FILL_FORM_FIELD_LABELS", "target_id": root.id})

    # WCAG 1.3.5: declare each field's purpose. Same contract as above —
    # iter_autocomplete_candidates is the helper the parser COUNTED with, so we
    # write exactly as many tokens as were reported, and only for fields whose
    # purpose is unambiguous.
    if props.get("apply_input_autocomplete"):
        for ctrl, token in iter_autocomplete_candidates(doc):
            ctrl.set("autocomplete", token)
            applied.append({"action": "SET_INPUT_AUTOCOMPLETE", "target_id": root.id})

    # WCAG 2.4.3: a positive tabindex drags an element to the front of the whole
    # page's tab order. Resetting to 0 keeps it focusable in natural DOM order.
    if props.get("apply_tabindex_reset"):
        for el in iter_positive_tabindex(doc):
            el.set("tabindex", "0")
            applied.append({"action": "FIX_POSITIVE_TABINDEX", "target_id": root.id})

    # Fake-list -> real list: each approved run becomes a <ul>/<ol>. Done LAST so
    # the element removals can't disturb other held references.
    for node in iter_reading_order(root):
        if not isinstance(node, ParagraphNode):
            continue
        nprops = node.metadata.properties or {}
        run_ids = nprops.get("fake_list_run_ids")
        if not run_ids or not nprops.get("convert_to_list"):
            continue  # only the run's FIRST node carries run_ids
        _apply_list_conversion(node, run_ids, nodes_by_id, list_member_els, applied, skipped)

    try:
        out_bytes = _serialize(doc, source_info)
    except Exception as exc:
        logger.exception("html_writer serialize failed: %s", exc)
        return {"applied": [], "skipped": [{"target_id": str(source_path), "reason": "failed_to_open: serialize"}]}

    # CONTENT-LOSS GATE. Every fix we make ADDS or REWRITES attributes, or
    # wraps runs in list markup; the ONLY visible text any of them removes is
    # a typed list marker ("- ", "1. ") when a fake list becomes a real one.
    # So the output must carry every WORD the source did — if it lost any,
    # some path dropped content, and shipping that as a "fixed" file would
    # delete a customer's words while reporting success. Mirrors the PDF
    # tagger's op-count guard: refuse, copy the source through unchanged, and
    # say so in `skipped` (the pipeline reads that reason and does not charge).
    # Word count, not character count: markers are not words, so a list
    # conversion passes; a dropped paragraph or a truncated subtree never does.
    src_words, out_words = _visible_word_count(data), _visible_word_count(out_bytes)
    if out_words < src_words:
        logger.error(
            "html_writer: output has FEWER visible words than the source (%d < %d); "
            "refusing to ship it — source copied through unchanged",
            out_words, src_words,
        )
        return {
            "applied": [],
            "skipped": [{"target_id": str(source_path), "reason": "output_would_lose_content"}],
        }

    try:
        Path(output_path).write_bytes(out_bytes)
    except Exception as exc:
        logger.exception("html_writer write failed: %s", exc)
        return {"applied": [], "skipped": [{"target_id": str(source_path), "reason": "failed_to_open: write"}]}

    return {"applied": applied, "skipped": skipped}


_PATH_STEP_RE = re.compile(r"^([^\[\]/]+)(?:\[(\d+)\])?$")


def _tag_sequence(doc: Any) -> List[str]:
    """Every element's tag in document order — the tree's shape."""
    return [el.tag for el in list(doc.iter()) if isinstance(el.tag, str)]


class _PathIndex:
    """Resolve ``ElementTree.getpath()`` locators with per-parent child lists.

    Each parent's element children are grouped by tag once, on first use, so
    a step ``tag[n]`` is a list index. A step WITHOUT an index means the tag
    was unique among its siblings when the path was made; if it is not unique
    now, the locator is ambiguous and resolves to nothing (as ``doc.xpath``
    returning several elements did).
    """

    def __init__(self, doc: Any) -> None:
        self._root = doc.getroottree().getroot()
        self._children: Dict[Any, Dict[str, List[Any]]] = {}

    def _groups(self, parent: Any) -> Dict[str, List[Any]]:
        groups = self._children.get(parent)
        if groups is None:
            groups = {"*": []}
            for child in parent:
                if isinstance(child.tag, str):
                    groups.setdefault(child.tag, []).append(child)
                    groups["*"].append(child)
            self._children[parent] = groups
        return groups

    def release(self) -> None:
        """Drop the cached element proxies INNERMOST first. Freed in the
        dict's own (outermost-first) order, each release made lxml walk from
        that node up to the document — O(depth^2): 2.3 s for one 30,000-deep
        page. See html_parser._elements."""
        while self._children:
            self._children.popitem()

    def resolve(self, xpath: str) -> Optional[Any]:
        if not xpath.startswith("/"):
            return None
        steps = xpath[1:].split("/")
        first = _PATH_STEP_RE.match(steps[0]) if steps else None
        root = self._root
        if first is None or root is None or first.group(1) not in ("*", root.tag) or (first.group(2) or "1") != "1":
            return None
        cur = root
        for step in steps[1:]:
            m = _PATH_STEP_RE.match(step)
            if not m:
                return None
            members = self._groups(cur).get(m.group(1))
            if not members:
                return None
            if m.group(2) is None:
                if len(members) != 1:
                    return None
                cur = members[0]
            else:
                pos = int(m.group(2))
                if pos < 1 or pos > len(members):
                    return None
                cur = members[pos - 1]
        return cur


_LIST_MARKER_TOKEN = re.compile(r"^(?:[-*•·]|\d{1,3}[.)])$")


def _visible_word_count(html_bytes: bytes) -> int:
    """Number of visible WORDS under <body>, for the loss gate.

    Independent of our node tree on purpose: it re-parses the raw bytes with
    lxml and tokenizes text under <body> minus script/style/template, so it
    measures what a reader would see rather than what our parser chose to
    model. Standalone list markers ("-", "1.") are not counted, because a
    fake-list conversion legitimately removes them — the gate must catch lost
    CONTENT, not a marker that became real list markup. Both sides of the
    comparison go through this same function.
    """
    try:
        root = _parse_document(html_bytes)
        body = root.find("body")
        scope = body if body is not None else root
        total = 0
        # A held list, not a lazy walk: see html_parser._elements (a lazy walk
        # over a 20,000-deep page cost 1.3 s per count).
        for el in list(scope.iter()):
            tag = el.tag if isinstance(el.tag, str) else ""
            if tag.lower() in ("script", "style", "template", "noscript"):
                continue
            for chunk in (el.text, el.tail):
                if not chunk:
                    continue
                total += sum(1 for tok in chunk.split() if not _LIST_MARKER_TOKEN.match(tok))
        return total
    except Exception:
        # If we cannot measure, treat the output as suspect: 0 for the OUTPUT
        # trips the gate (fail closed), while 0 for the SOURCE never blocks.
        return 0


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


def _apply_image(node: ImageNode, el: Any, seen_el: Any, applied: List[Dict[str, Any]]) -> None:
    """``seen_el`` is the element as the analysis parsed it (``el`` itself
    unless the page carries byte stand-ins): the change test reads it, the
    write goes to ``el``."""
    # Only GENERATE_ALT_TEXT is a supported HTML image fix in v1. Decorative
    # images are left untouched (a properly empty alt is already correct, and we
    # never edit an image the user didn't approve a fix for).
    if node.is_decorative or not node.alt_text or el is None:
        return
    if seen_el is None:
        seen_el = el
    if (node.metadata.properties or {}).get("svg_inline"):
        _apply_svg_name(node, el, seen_el, applied)
        return
    # The parser stored the alt stripped; an unchanged " Chart " is not a fix.
    if (seen_el.get("alt") or "").strip() != node.alt_text:
        el.set("alt", node.alt_text)
        applied.append({"action": "GENERATE_ALT_TEXT", "target_id": node.id})


def _apply_svg_name(node: ImageNode, el: Any, seen_el: Any, applied: List[Dict[str, Any]]) -> None:
    """Name an inline ``<svg>``: ``role="img"`` + ``aria-label``.

    An ``<svg>`` has no ``alt``. ``role="img"`` makes assistive tech treat it
    as ONE image (instead of a group of loose shapes and text fragments) and
    ``aria-label`` is its name — it outranks a ``<title>`` child in the
    accessible-name computation, so it also replaces a non-descriptive title.
    The parser reads ``aria-label`` back, so a re-scan sees a named image.
    """
    tag = el.tag.rsplit("}", 1)[-1].lower() if isinstance(el.tag, str) else ""
    if tag != "svg":
        return
    if _svg_accessible_name(seen_el) == node.alt_text:
        return  # the name the parser read — nothing was approved/changed
    changed = False
    if not (el.get("role") or "").strip():
        el.set("role", "img")
        changed = True
    if (seen_el.get("aria-label") or "") != node.alt_text:
        el.set("aria-label", node.alt_text)
        changed = True
    if changed:
        applied.append({"action": "GENERATE_ALT_TEXT", "target_id": node.id})


def _apply_heading(node: HeadingNode, el: Any, applied: List[Dict[str, Any]]) -> None:
    if el is None or not isinstance(el.tag, str):
        return
    current = el.tag.lower()
    want = f"h{node.level}"
    if current in _HEADING_TAGS and current != want:
        el.tag = want
        applied.append({"action": "NORMALIZE_HEADING_LEVEL", "target_id": node.id})


def _apply_link(node: LinkNode, el: Any, seen_el: Any, applied: List[Dict[str, Any]]) -> None:
    if node.content.kind != ContentKind.TEXT or not node.content.text or el is None:
        return
    # Parser only assigns TEXT content to links with no element children, so
    # replacing the text destroys nothing.
    if any(isinstance(c.tag, str) for c in el):
        return
    if seen_el is None:
        seen_el = el
    if (seen_el.text_content() or "").strip() != node.content.text:
        el.text = node.content.text
        applied.append({"action": "IMPROVE_LINK_TEXT", "target_id": node.id})


def _apply_table(node: TableNode, elements: Dict[str, Any], applied: List[Dict[str, Any]]) -> None:
    table_el = elements.get(node.id)
    if table_el is None:
        return
    # AI-generated <caption> (GENERATE_TABLE_CAPTION): insert as the table's
    # FIRST child (the only valid position for <caption>). Never clobber an
    # existing one. The executor only sets this when the table was flagged
    # caption-missing, so re-parsing the output reads it back and clears the
    # flag (honesty round-trip).
    caption = (node.metadata.properties or {}).get("caption")
    if isinstance(caption, str) and caption.strip() and table_el.find("caption") is None:
        caption_el = etree.Element("caption")
        caption_el.text = caption
        table_el.insert(0, caption_el)
        applied.append({"action": "GENERATE_TABLE_CAPTION", "target_id": node.id})
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
        # Create a real <thead> and place it at the FIRST valid position for a
        # row group: after any leading <caption>/<colgroup> (HTML requires
        # <caption> to be the table's first child — inserting <thead> at index 0
        # would shove an AI-generated or author-provided caption out of place
        # and trip a conformance validator). Skip leading caption/colgroup.
        thead = etree.Element("thead")
        thead.append(tr)
        idx = 0
        for child in table_el:
            if isinstance(child.tag, str) and child.tag.lower() in ("caption", "colgroup"):
                idx += 1
            else:
                break
        table_el.insert(idx, thead)
        return True
    table_el.append(tr)
    return True


def _apply_list_conversion(
    first_node: Any,
    run_ids: List[str],
    nodes_by_id: Dict[str, Any],
    list_member_els: Dict[str, Any],
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Replace a run of typed-list ``<p>`` elements with a real ``<ul>``/``<ol>``.

    Each member node's text was already stripped of its literal marker by the
    executor, so the ``<li>`` text is clean. Only acts when EVERY member element
    resolved (held from the pristine DOM); otherwise records a skip so the action
    is never credited without persisting.
    """
    members = []
    for mid in run_ids:
        node = nodes_by_id.get(mid)
        el = list_member_els.get(mid)
        if node is None or el is None:
            skipped.append({"target_id": first_node.id, "reason": "list_member_not_resolved"})
            return
        members.append((node, el))
    if len(members) < 2:
        return
    els = [el for _n, el in members]
    # SAFETY: every member must share ONE DOM parent. Transparent inline wrappers
    # (<span>…) can make tree-siblings live under different DOM parents; moving
    # them would scramble reading order or emit a block list inside an inline.
    parent = els[0].getparent()
    if parent is None or any(el.getparent() is not parent for el in els):
        skipped.append({"target_id": first_node.id, "reason": "list_members_cross_parent"})
        return
    # SAFETY: if a member's recolour was already applied + credited, removing its
    # <p> would charge a FIX_CONTRAST that isn't in the bytes — leave the run.
    if any((n.metadata.properties or {}).get("contrast_fix_fg") for n, _e in members):
        skipped.append({"target_id": first_node.id, "reason": "list_member_pending_contrast"})
        return
    kind = (first_node.metadata.properties or {}).get("convert_to_list")
    list_el = etree.Element("ol" if kind == "decimal" else "ul")
    for node, _el in members:
        li = etree.SubElement(list_el, "li")
        li.text = node.content.text if (node.content and node.content.text) else ""
    parent.insert(parent.index(els[0]), list_el)
    # Preserve any real text that sat between/after the member <p> (their lxml
    # .tail, which remove() would drop) — re-home it after the new list so no
    # document content is ever lost.
    tail_text = "".join((el.tail or "") for el in els)
    for el in els:
        el_parent = el.getparent()
        if el_parent is not None:
            el_parent.remove(el)
    if tail_text.strip():
        list_el.tail = (list_el.tail or "") + tail_text
    applied.append({"action": "FIX_LIST_STRUCTURE", "target_id": first_node.id})


def _ensure_title_element(doc: Any) -> Optional[Any]:
    head = doc.find("head")
    if head is None:
        head = etree.Element("head")
        doc.insert(0, head)  # first child of <html>
    title_el = head.find("title")
    if title_el is None:
        title_el = etree.SubElement(head, "title")
    return title_el


_LEADING_DOCTYPE_RE = re.compile(r"^\s*<!DOCTYPE[^>]*>[ \t]*(?:\r?\n)?", re.IGNORECASE)


def _serialize(doc: Any, source: Optional[HtmlSource] = None) -> bytes:
    """Serialize back in the SOURCE'S OWN encoding, doctype and declaration.

    We used to always emit UTF-8 (rewriting any <meta charset> to match). For
    a legacy windows-1252 page with no declaration that shipped UTF-8 bytes
    the browser still decoded as windows-1252 — every "é" became "Ã©" — and a
    page served by a server that sends ``charset=ISO-8859-1`` breaks the same
    way whatever the meta says. Re-encoding with the codec the parser decoded
    with means every byte of text we did not change comes back as it was, the
    existing declaration stays true, and nothing is (re)declared.

    The whole document tree is serialized (so a comment before ``<html>`` —
    e.g. IE's "saved from url" mark — survives), but lxml invents an HTML 4.0
    Transitional doctype for a page that had none, and adding a doctype can
    switch a page's rendering mode, so it is only kept if the source had one.
    """
    source = source or HtmlSource()
    # include_meta_content_type=True means "leave the page's own
    # <meta http-equiv="Content-Type"> alone" (with str output lxml never
    # injects one). False DELETED it — and for a Word "Save as Web Page"
    # export that meta is the page's only charset declaration.
    text = lxml_html.tostring(
        doc.getroottree(),
        encoding="unicode",
        method="html",
        include_meta_content_type=True,
    )
    if not source.has_doctype:
        text = _LEADING_DOCTYPE_RE.sub("", text, count=1)
    return encode_html_text(text, source)


__all__ = ["write_remediated_html"]
