"""PPTX writer — applies :class:`AccessibilityTree` mutations back to a .pptx.

The companion :mod:`app.parsers.pptx_parser` module turns a PowerPoint deck
into an :class:`AccessibilityTree`.  After analyzers / executors mutate that
tree (for example, filling in alt text on a picture, marking a picture as
decorative, or setting deck-level title / language metadata), this module
materializes those mutations onto a copy of the original ``.pptx``.

Design notes
------------

* **Always copy first.**  The source ``.pptx`` is duplicated to the output
  path *before* any mutation; the source file itself is never written to.
* **Re-parse for id alignment.**  The parser uses a stateful ``_IdCounter``
  to mint stable ids per slide (``slide-3-img-1``, ``slide-3-cell-2``, ...).
  The counter is deterministic, so re-parsing the source with the same
  parser produces the exact same ids in the same structural order as the
  tree the caller hands us.  We rely on that property: when we want to find
  the source counterpart of a node in the (mutated) tree, we re-walk the
  source's slides + shapes using the same parser visit-order and re-mint
  ids in lock-step — then look up shapes by the id.
* **Defensive matching.**  If a tree node cannot be paired to anything in
  the source (shape disappeared, slide deleted, format mismatch, etc.) we
  log it under ``skipped`` and keep going.  The writer never raises for a
  single bad mutation — it always returns a structured report.
* **Minimal-touch XML.**  Where python-pptx exposes a setter
  (``shape.alternative_text``, ``prs.core_properties.title``,
  ``prs.core_properties.language``) we use it.  For things python-pptx does
  not expose (the ``decorative`` attribute on a picture's ``nvPicPr`` and
  the ``firstRow`` attribute on a table's ``tblPr``) we manipulate lxml
  elements directly via ``shape._element`` / ``table._tbl``.
"""

from __future__ import annotations

import copy
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import PP_PLACEHOLDER
from pptx.oxml.ns import qn

from app.models.accessibility import (
    AccessibilityTree,
    DocumentNode,
    ImageNode,
    LinkNode,
    ParagraphNode,
    SectionNode,
    TableCellNode,
    TableCellType,
    TableNode,
    TableRowNode,
)
from app.parsers.docx_parser import _fake_list_signature, strip_fake_list_prefix
from app.parsers.pptx_parser import (
    PPTXParser,
    _IdCounter,
    _image_kind,
    _iter_hyperlink_groups,
    _iter_shapes_recursive,
    _paragraph_has_real_bullet,
    _pptx_theme_colors,
    _run_color_hex_pptx,
    _shape_marked_decorative,
)

logger = logging.getLogger(__name__)


# DrawingML namespaces used while patching <a:nvPicPr>/<a:tblPr>.  The
# canonical OOXML schema URI is the same one python-pptx uses internally.
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def write_remediated_pptx(
    source_path: Path,
    tree: AccessibilityTree,
    output_path: Path,
) -> dict:
    """Apply ``tree`` mutations to a copy of ``source_path`` and write it out.

    Parameters
    ----------
    source_path:
        Path to the original ``.pptx`` file that produced ``tree``.
    tree:
        The (possibly mutated) :class:`AccessibilityTree` whose state should
        be reflected in the output deck.
    output_path:
        Where to write the remediated copy.  Parent directories are created
        if missing.

    Returns
    -------
    dict
        ``{"applied": [...], "skipped": [...]}`` describing each mutation
        attempted.  Each ``applied`` entry has ``kind``, ``target_id`` and
        ``summary`` keys; each ``skipped`` entry has ``target_id`` and
        ``reason``.
    """

    source_path = Path(source_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Step 1 — copy source to output before mutating anything.  We never
    # touch the source file itself, and python-pptx can open output_path
    # directly.
    shutil.copyfile(source_path, output_path)

    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    # Step 2 — re-parse the OUTPUT (a byte-identical copy of the source) so
    # we get the same id sequence the caller's tree was produced with.  If
    # this fails we still try to apply document-level metadata, which does
    # not need any id alignment.
    reference_tree: Optional[AccessibilityTree] = None
    try:
        reference_tree = PPTXParser().parse_to_tree(str(output_path)).tree
    except Exception as exc:  # defensive — never crash on a parser hiccup
        logger.exception("Failed to re-parse source for id alignment: %s", exc)

    mutated_index = _index_tree(tree)
    reference_index = _index_tree(reference_tree) if reference_tree is not None else {}

    try:
        prs = Presentation(str(output_path))
    except Exception as exc:
        logger.exception("Failed to open output pptx: %s", exc)
        skipped.append(
            {"target_id": tree.root.id, "reason": f"failed_to_open_pptx:{exc}"}
        )
        return {"applied": applied, "skipped": skipped}

    # Step 3 — build per-id lookups against the *output* deck using the same
    # visit-order the parser uses.
    (
        image_by_node_id,
        cell_by_node_id,
        link_by_node_id,
        table_by_node_id,
        text_by_node_id,
        section_by_node_id,
    ) = _index_shapes_by_parser_id(prs)

    # Step 4 — document-level metadata (title, language) first.
    _apply_document_metadata(prs, tree.root, applied, skipped)

    # Step 5 — per-node mutations.  We only touch nodes whose mutation kinds
    # this writer supports; everything else passes silently (it is not an
    # error for the tree to contain unsupported node types).
    for node in mutated_index.values():
        if isinstance(node, SectionNode) and (node.metadata.properties or {}).get("set_slide_title"):
            # An untitled slide the SET_SLIDE_TITLE executor approved — make
            # the slide's own text its title (never a visible duplicate).
            _apply_slide_title(node, section_by_node_id, text_by_node_id, prs, applied, skipped)
        elif isinstance(node, ImageNode):
            _apply_image(node, image_by_node_id, applied, skipped)
        elif isinstance(node, ParagraphNode) and (node.metadata.properties or {}).get("convert_to_list"):
            # A typed fake-list text box the FIX_LIST_STRUCTURE executor
            # approved — give its lines real bullet formatting.
            _apply_pptx_list_conversion(node, text_by_node_id, applied, skipped)
        elif isinstance(node, LinkNode):
            _apply_pptx_link(node, link_by_node_id, applied, skipped)
        elif isinstance(node, TableCellNode):
            _apply_table_cell(
                node,
                reference_index,
                cell_by_node_id,
                applied,
                skipped,
            )
        elif isinstance(node, TableNode):
            # A header-less table may have had a SYNTHESIZED header row inserted
            # at the top of the tree by AddTableHeadersExecutor. That row has no
            # source <a:tr>, so insert a real one (and set the firstRow band) —
            # otherwise the fix would be counted but never reach the file.
            _apply_synthetic_pptx_table_header(node, table_by_node_id, applied, skipped)

    # Step 5b — contrast recolour. The FixContrastExecutor leaves a
    # {old_hex: new_hex} map on each approved node naming the failing run
    # colours; recolour the shape's runs whose colour is in the map to their
    # AA-passing shade (via an explicit a:srgbClr, which overrides a theme
    # scheme colour). Counted only when the writer confirms it (the FIX_CONTRAST
    # applied-list reconciliation in pipeline.py is format-agnostic).
    _pptx_theme: Optional[Dict[str, str]] = None
    for node in mutated_index.values():
        color_map = (node.metadata.properties or {}).get("contrast_fix_colors")
        if not color_map:
            continue
        if _pptx_theme is None:
            try:
                _pptx_theme = _pptx_theme_colors(str(source_path))
            except Exception:  # pragma: no cover - defensive
                _pptx_theme = {}
        _apply_pptx_contrast(node, text_by_node_id, color_map, _pptx_theme, applied, skipped)

    # Step 6 — persist. Save to a sibling temp file, then move it into place,
    # and report a failed save as applied=[] — because nothing was applied to
    # the FILE. Swallowing the exception and returning the in-memory ``applied``
    # list charged full price for a deck byte-identical to the user's own
    # upload: the pipeline's hard-failure guard didn't match the reason, and its
    # ``and not applied`` clause couldn't help, since ``applied`` records intent
    # rather than bytes. docx_writer lets its save raise; this is the same
    # contract, expressed through the skip list the caller already reads.
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        prs.save(str(tmp_path))
        os.replace(str(tmp_path), str(output_path))
    except Exception as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        logger.exception("Failed to save remediated pptx: %s", exc)
        return {
            "applied": [],
            "skipped": skipped + [{"target_id": tree.root.id, "reason": f"failed_to_save_pptx:{exc}"}],
        }

    return {"applied": applied, "skipped": skipped}


# ---------------------------------------------------------------------------
# Tree indexing
# ---------------------------------------------------------------------------


def _index_tree(tree: Optional[AccessibilityTree]) -> Dict[str, Any]:
    """Return ``{node.id: node}`` for every node in ``tree`` (depth-first)."""

    out: Dict[str, Any] = {}
    if tree is None:
        return out
    stack = [tree.root]
    while stack:
        node = stack.pop()
        out[node.id] = node
        for child in reversed(node.children):
            stack.append(child)
    return out


# ---------------------------------------------------------------------------
# Source-deck indexing — re-mints the parser's per-slide ids and pairs them
# back to the live python-pptx shape objects so we can mutate them.
# ---------------------------------------------------------------------------


def _norm_hex(value: Any) -> str:
    return str(value or "").lstrip("#").upper()


def _apply_pptx_contrast(
    node: Any,
    text_by_node_id: Dict[str, Any],
    color_map: Dict[str, str],
    theme_colors: Dict[str, str],
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Recolour a text shape's failing runs to their AA-passing shades.

    ``color_map`` is ``{old_hex: new_hex}`` (no ``#``) for the runs the analyzer
    flagged. We resolve each run's effective colour exactly as the analyzer did
    (``_run_color_hex_pptx`` — explicit sRGB or resolved scheme colour) and
    rewrite the matching runs with an explicit ``a:srgbClr`` (via RGBColor),
    which overrides a theme scheme colour. The PPTX node is the whole text shape,
    so we walk all its paragraphs' runs.
    """
    shape = text_by_node_id.get(node.id)
    if shape is None or not getattr(shape, "has_text_frame", False):
        skipped.append({"target_id": node.id, "reason": "contrast_shape_not_resolved"})
        return
    norm_map = {_norm_hex(k): _norm_hex(v) for k, v in color_map.items() if v}
    changed = 0
    try:
        for paragraph in shape.text_frame.paragraphs:
            for run in paragraph.runs:
                cur = _run_color_hex_pptx(run, theme_colors)
                if not cur:
                    continue
                new = norm_map.get(_norm_hex(cur))
                if not new:
                    continue
                run.font.color.rgb = RGBColor.from_string(new)
                changed += 1
    except Exception as exc:  # pragma: no cover - defensive
        skipped.append({"target_id": node.id, "reason": f"failed_to_set_run_color:{exc}"})
        return
    if changed:
        applied.append({"action": "FIX_CONTRAST", "target_id": node.id})
    else:
        skipped.append({"target_id": node.id, "reason": "contrast_no_matching_run"})


def _apply_pptx_link(
    link: LinkNode,
    link_by_node_id: Dict[str, Any],
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Rewrite a hyperlink's display text. The link may span several runs (one
    visual link split across runs); the canonical text goes in the first run and
    the rest are cleared. python-pptx's ``run.text`` setter preserves each run's
    formatting and its ``a:hlinkClick`` (the link)."""
    runs = link_by_node_id.get(link.id)
    if not runs:
        skipped.append({"target_id": link.id, "reason": "link_run_not_found"})
        return
    if not isinstance(runs, list):
        runs = [runs]
    new_text = (link.content.text or "").strip() if link.content else ""
    if not new_text:
        skipped.append({"target_id": link.id, "reason": "empty_link_text"})
        return
    current = "".join((r.text or "") for r in runs).strip()
    if current == new_text:
        skipped.append({"target_id": link.id, "reason": "no_change_required"})
        return
    try:
        runs[0].text = new_text
        for r in runs[1:]:
            r.text = ""
    except Exception as exc:  # pragma: no cover - defensive
        skipped.append({"target_id": link.id, "reason": f"failed_to_set_link_text:{exc}"})
        return
    applied.append({"kind": "link_text", "target_id": link.id, "summary": f"{current!r} -> {new_text!r}"})


def _index_shapes_by_parser_id(
    prs,
) -> Tuple[
    Dict[str, Any],
    Dict[str, Tuple[Any, int, int]],
    Dict[str, Any],
    Dict[str, Any],
    Dict[str, Any],
    Dict[str, Any],
]:
    """Walk ``prs`` exactly the way :func:`PPTXParser.parse_to_tree` does and
    pair each minted id with the underlying python-pptx object.

    Returns five dicts:

    * ``image_by_node_id`` — ``{node_id: shape}`` for picture shapes.
    * ``cell_by_node_id`` — ``{node_id: (table_shape, row_index, col_index)}``
      for table cells.
    * ``link_by_node_id`` — ``{node_id: run}`` for hyperlink runs.
    * ``table_by_node_id`` — ``{table_node_id: table_shape}`` for tables.
    * ``text_by_node_id`` — ``{paragraph_node_id: shape}`` for text shapes.
    * ``section_by_node_id`` — ``{section_node_id: slide}`` for slides.
    """

    image_by_node_id: Dict[str, Any] = {}
    cell_by_node_id: Dict[str, Tuple[Any, int, int]] = {}
    link_by_node_id: Dict[str, Any] = {}
    table_by_node_id: Dict[str, Any] = {}
    text_by_node_id: Dict[str, Any] = {}
    section_by_node_id: Dict[str, Any] = {}

    ids = _IdCounter()

    for slide_index, slide in enumerate(prs.slides, start=1):
        # The parser mints a section + (optional) heading id BEFORE iterating
        # shapes.  We mirror that to keep the counter in lock-step.
        section_by_node_id[ids(f"slide-{slide_index}-section")] = slide
        slide_title = ""
        title_shape_id = None
        try:
            title_shape = slide.shapes.title
            if title_shape is not None and title_shape.text:
                slide_title = title_shape.text.strip()
                if slide_title:
                    title_shape_id = title_shape.shape_id
        except Exception:
            slide_title = ""
            title_shape_id = None
        if slide_title:
            ids(f"slide-{slide_index}-h")

        for shape in _iter_shapes_recursive(slide.shapes):
            if _image_kind(shape):
                node_id = ids(f"slide-{slide_index}-img")
                image_by_node_id[node_id] = shape
                continue
            if shape.has_table:
                table = shape.table
                # The parser mints cells row-major then the row id, then the
                # table id at the very end.  Mirror that order exactly.
                for row_index, row in enumerate(table.rows):
                    for col_index, _ in enumerate(row.cells):
                        cell_id = ids(f"slide-{slide_index}-cell")
                        cell_by_node_id[cell_id] = (shape, row_index, col_index)
                    ids(f"slide-{slide_index}-row")
                table_by_node_id[ids(f"slide-{slide_index}-table")] = shape
                continue
            if hasattr(shape, "text_frame") and shape.text_frame:
                text = (shape.text or "").strip()
                if not text:
                    continue
                # The parser does NOT mint a paragraph id for the title
                # placeholder (it became the slide's HeadingNode) — mirror
                # that, matched by shape_id, or every later paragraph id on
                # the slide drifts.
                is_title = (
                    title_shape_id is not None
                    and getattr(shape, "shape_id", None) == title_shape_id
                )
                if not is_title:
                    node_id = ids(f"slide-{slide_index}-p")
                    text_by_node_id[node_id] = shape
                # Hyperlinks inside runs each consume a link-id (adjacent
                # same-address runs are coalesced, mirroring the parser).
                try:
                    for paragraph in shape.text_frame.paragraphs:
                        for _href, runs in _iter_hyperlink_groups(paragraph):
                            link_by_node_id[ids(f"slide-{slide_index}-link")] = runs
                except Exception:
                    # Defensive — text_frame parsing rarely fails but a
                    # corrupted shape shouldn't take down id alignment.
                    logger.debug(
                        "skip_text_frame_walk slide=%s", slide_index, exc_info=True
                    )

    return (
        image_by_node_id,
        cell_by_node_id,
        link_by_node_id,
        table_by_node_id,
        text_by_node_id,
        section_by_node_id,
    )


# ---------------------------------------------------------------------------
# Mutation appliers
# ---------------------------------------------------------------------------


def _set_pptx_run_langs(prs, language: str) -> int:
    """Set ``lang`` on every text run's ``a:rPr`` so AT speaks the right language.

    PowerPoint/screen readers read spoken language from the run-level ``lang``
    attribute, not ``docProps/core.xml``. Returns the number of runs touched.
    """
    count = 0
    for slide in prs.slides:
        for shape in _iter_shapes_recursive(slide.shapes):
            if not getattr(shape, "has_text_frame", False):
                continue
            try:
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        try:
                            rpr = run._r.get_or_add_rPr()
                            rpr.set("lang", language)
                            count += 1
                        except Exception:
                            continue
            except Exception:
                continue
    return count


def _layout_title_placeholder(slide):
    """Return the slide layout's title placeholder, or None.

    The title is idx 0 by convention; we also accept it by placeholder type so
    a layout that numbers things unusually still matches.
    """
    try:
        layout = slide.slide_layout
        placeholders = list(layout.placeholders)
    except Exception:
        return None
    for ph in placeholders:
        try:
            if ph.placeholder_format.idx == 0:
                return ph
        except Exception:
            continue
    for ph in placeholders:
        try:
            if ph.placeholder_format.type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE):
                return ph
        except Exception:
            continue
    return None


# --- Slide titles -----------------------------------------------------------
#
# The title must come from text the slide ALREADY shows, and must never add a
# second visible copy of it. We used to insert a title placeholder carrying the
# derived text while the original text box stayed put: the placeholder has no
# geometry of its own, so it inherited the layout's title position and the
# words appeared twice on 40 of 200 slides of a real deck. Now:
#
#   1. PROMOTE — the source is a plain top-level text box holding exactly the
#      title: it BECOMES the title placeholder (a <p:ph type="title"/> in its
#      nvPr). Placeholder text inherits the master's title style (44pt, the
#      heading font, centred, anchored to the bottom...), so every property
#      the box was taking from the presentation defaults is first written onto
#      the box explicitly — position, size, font, colour and alignment stay
#      exactly as they were.
#   2. OFF-SLIDE — anything else (the text is in a group, shares its box with
#      body text, or is an autoshape): a title placeholder carrying the text is
#      positioned just ABOVE the slide, PowerPoint's own documented way to
#      give a slide a title nobody sees. It is outside the rendered area, so
#      the slide, a slideshow and a PDF export look exactly as before.

_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_EMU_PER_INCH = 914400

# CT_TextCharacterProperties / CT_TextParagraphProperties / CT_TextBodyProperties
# / CT_ShapeProperties child order — a child out of order and PowerPoint
# refuses to open the file.
_RPR_ORDER = [qn(t) for t in (
    "a:ln", "a:noFill", "a:solidFill", "a:gradFill", "a:blipFill", "a:pattFill", "a:grpFill",
    "a:effectLst", "a:effectDag", "a:highlight", "a:uLnTx", "a:uLn", "a:uFillTx", "a:uFill",
    "a:latin", "a:ea", "a:cs", "a:sym", "a:hlinkClick", "a:hlinkMouseOver", "a:rtl", "a:extLst",
)]
_PPR_ORDER = [qn(t) for t in (
    "a:lnSpc", "a:spcBef", "a:spcAft", "a:buClrTx", "a:buClr", "a:buSzTx", "a:buSzPct",
    "a:buSzPts", "a:buFontTx", "a:buFont", "a:buNone", "a:buAutoNum", "a:buChar", "a:buBlip",
    "a:tabLst", "a:defRPr", "a:extLst",
)]
_BODYPR_ORDER = [qn(t) for t in (
    "a:prstTxWarp", "a:noAutofit", "a:normAutofit", "a:spAutoFit", "a:scene3d", "a:sp3d",
    "a:flatTx", "a:extLst",
)]
_SPPR_ORDER = [qn(t) for t in (
    "a:xfrm", "a:custGeom", "a:prstGeom", "a:noFill", "a:solidFill", "a:gradFill", "a:blipFill",
    "a:pattFill", "a:grpFill", "a:ln", "a:effectLst", "a:effectDag", "a:scene3d", "a:sp3d",
    "a:extLst",
)]
_FILL_TAGS = {qn(t) for t in ("a:noFill", "a:solidFill", "a:gradFill", "a:blipFill", "a:pattFill", "a:grpFill")}
_BULLET_TAGS = {qn(t) for t in ("a:buNone", "a:buAutoNum", "a:buChar", "a:buBlip")}
_AUTOFIT_TAGS = {qn(t) for t in ("a:noAutofit", "a:normAutofit", "a:spAutoFit")}

# Values the schema/PowerPoint use when nothing in a text box's chain sets one.
_RUN_ATTR_DEFAULTS = {
    "sz": "1800", "b": "0", "i": "0", "u": "none", "strike": "noStrike",
    "cap": "none", "spc": "0", "baseline": "0",
}
_PARA_ATTR_DEFAULTS = {"algn": "l", "marL": "0", "indent": "0", "rtl": "0"}
_BODY_ATTR_DEFAULTS = {
    "lIns": "91440", "tIns": "45720", "rIns": "91440", "bIns": "45720",
    "anchor": "t", "wrap": "square", "vert": "horz", "anchorCtr": "0", "rtlCol": "0",
}


def _insert_ordered(parent, child, order: List[str]) -> None:
    """Insert ``child`` at its schema position among ``parent``'s children."""
    try:
        rank = order.index(child.tag)
    except ValueError:
        parent.append(child)
        return
    for i, existing in enumerate(parent):
        try:
            if order.index(existing.tag) > rank:
                parent.insert(i, child)
                return
        except ValueError:
            continue
    parent.append(child)


def _lvl_ppr(list_style, lvl: int):
    """``a:lvl{N}pPr`` of an ``a:lstStyle``/``p:defaultTextStyle``, or None."""
    if list_style is None:
        return None
    return list_style.find(qn(f"a:lvl{lvl}pPr"))


def _freeze_text_box_look(prs, sp) -> None:
    """Write onto ``sp`` every text/shape property it currently takes from the
    presentation defaults, so promoting it to a placeholder changes nothing.

    A text box's text resolves: run/paragraph -> the box's own ``a:lstStyle``
    -> ``p:defaultTextStyle`` -> schema defaults. A title placeholder resolves
    run/paragraph -> own lstStyle -> the layout's and master's title styles.
    The box's own lstStyle keeps its priority either way, so only values that
    came from the presentation defaults (or the schema) need writing down.
    """
    try:
        default_style = prs.part._element.find(qn("p:defaultTextStyle"))  # noqa: SLF001
    except Exception:
        default_style = None
    tx_body = sp.find(qn("p:txBody"))
    list_style = tx_body.find(qn("a:lstStyle")) if tx_body is not None else None

    # -- shape geometry / fill / line: a placeholder inherits these too ------
    sp_pr = sp.find(qn("p:spPr"))
    if sp_pr is not None:
        if sp_pr.find(qn("a:prstGeom")) is None and sp_pr.find(qn("a:custGeom")) is None:
            geom = sp_pr.makeelement(qn("a:prstGeom"), {"prst": "rect"})
            etree.SubElement(geom, qn("a:avLst"))
            _insert_ordered(sp_pr, geom, _SPPR_ORDER)
        if not any(c.tag in _FILL_TAGS for c in sp_pr):
            nf = sp_pr.makeelement(qn("a:noFill"), {})
            _insert_ordered(sp_pr, nf, _SPPR_ORDER)
        if sp_pr.find(qn("a:ln")) is None:
            ln = sp_pr.makeelement(qn("a:ln"), {})
            etree.SubElement(ln, qn("a:noFill"))
            _insert_ordered(sp_pr, ln, _SPPR_ORDER)
        if sp_pr.find(qn("a:effectLst")) is None and sp_pr.find(qn("a:effectDag")) is None:
            _insert_ordered(sp_pr, sp_pr.makeelement(qn("a:effectLst"), {}), _SPPR_ORDER)

    if tx_body is None:
        return

    # -- body properties: a text box's are its own or the schema's ----------
    body_pr = tx_body.find(qn("a:bodyPr"))
    if body_pr is None:
        body_pr = tx_body.makeelement(qn("a:bodyPr"), {})
        tx_body.insert(0, body_pr)
    for attr, value in _BODY_ATTR_DEFAULTS.items():
        if body_pr.get(attr) is None:
            body_pr.set(attr, value)
    if not any(c.tag in _AUTOFIT_TAGS for c in body_pr):
        _insert_ordered(body_pr, body_pr.makeelement(qn("a:noAutofit"), {}), _BODYPR_ORDER)

    for p in tx_body.findall(qn("a:p")):
        p_pr = p.find(qn("a:pPr"))
        if p_pr is None:
            p_pr = p.makeelement(qn("a:pPr"), {})
            p.insert(0, p_pr)
        try:
            lvl = int(p_pr.get("lvl") or 0) + 1
        except ValueError:
            lvl = 1
        own_lvl = _lvl_ppr(list_style, lvl)
        def_lvl = _lvl_ppr(default_style, lvl)

        # paragraph attributes
        for attr, value in _PARA_ATTR_DEFAULTS.items():
            if p_pr.get(attr) is not None or (own_lvl is not None and own_lvl.get(attr) is not None):
                continue
            inherited = def_lvl.get(attr) if def_lvl is not None else None
            p_pr.set(attr, inherited if inherited is not None else value)
        # spacing + bullets
        for tag, inner_tag, inner_val in (
            ("a:lnSpc", "a:spcPct", "100000"),
            ("a:spcBef", "a:spcPts", "0"),
            ("a:spcAft", "a:spcPts", "0"),
        ):
            if p_pr.find(qn(tag)) is not None or (own_lvl is not None and own_lvl.find(qn(tag)) is not None):
                continue
            src = def_lvl.find(qn(tag)) if def_lvl is not None else None
            if src is not None:
                spacing = copy.deepcopy(src)
            else:
                spacing = p_pr.makeelement(qn(tag), {})
                etree.SubElement(spacing, qn(inner_tag)).set("val", inner_val)
            _insert_ordered(p_pr, spacing, _PPR_ORDER)
        has_bullet = any(c.tag in _BULLET_TAGS for c in p_pr) or (
            own_lvl is not None and any(c.tag in _BULLET_TAGS for c in own_lvl)
        )
        if not has_bullet:
            src = next((c for c in def_lvl if c.tag in _BULLET_TAGS), None) if def_lvl is not None else None
            _insert_ordered(p_pr, copy.deepcopy(src) if src is not None else p_pr.makeelement(qn("a:buNone"), {}), _PPR_ORDER)

        own_def = own_lvl.find(qn("a:defRPr")) if own_lvl is not None else None
        dflt_def = def_lvl.find(qn("a:defRPr")) if def_lvl is not None else None
        run_props = []
        for r in p:
            if r.tag in (qn("a:r"), qn("a:fld")):
                r_pr = r.find(qn("a:rPr"))
                if r_pr is None:
                    r_pr = r.makeelement(qn("a:rPr"), {})
                    r.insert(0, r_pr)
                run_props.append(r_pr)
        end_pr = p.find(qn("a:endParaRPr"))
        if end_pr is not None:
            run_props.append(end_pr)
        for r_pr in run_props:
            _freeze_run_props(r_pr, own_def, dflt_def)


def _freeze_run_props(r_pr, own_def, dflt_def) -> None:
    attrs = dict(_RUN_ATTR_DEFAULTS)
    if dflt_def is not None and dflt_def.get("kern") is not None:
        attrs["kern"] = dflt_def.get("kern")
    # A hyperlink run takes its colour and underline from the theme's hlink
    # styling, not from the text style chain; pinning tx1 / u="none" on it
    # would turn a blue underlined link into plain black text.
    is_link = r_pr.find(qn("a:hlinkClick")) is not None
    if is_link:
        attrs.pop("u", None)
    for attr, value in attrs.items():
        if r_pr.get(attr) is not None or (own_def is not None and own_def.get(attr) is not None):
            continue
        inherited = dflt_def.get(attr) if dflt_def is not None else None
        r_pr.set(attr, inherited if inherited is not None else value)

    def _own_has(tags) -> bool:
        return any(c.tag in tags for c in r_pr) or (
            own_def is not None and any(c.tag in tags for c in own_def)
        )

    if not is_link and not _own_has(_FILL_TAGS):
        src = next((c for c in dflt_def if c.tag in _FILL_TAGS), None) if dflt_def is not None else None
        if src is not None:
            _insert_ordered(r_pr, copy.deepcopy(src), _RPR_ORDER)
        else:
            fill = r_pr.makeelement(qn("a:solidFill"), {})
            etree.SubElement(fill, qn("a:schemeClr")).set("val", "tx1")
            _insert_ordered(r_pr, fill, _RPR_ORDER)
    if not _own_has({qn("a:effectLst"), qn("a:effectDag")}):
        src = None
        if dflt_def is not None:
            # (never `find() or find()`: an EMPTY lxml element is falsy)
            src = next((c for c in dflt_def if c.tag in (qn("a:effectLst"), qn("a:effectDag"))), None)
        _insert_ordered(r_pr, copy.deepcopy(src) if src is not None else r_pr.makeelement(qn("a:effectLst"), {}), _RPR_ORDER)
    for tag, theme_font in (("a:latin", "+mn-lt"), ("a:ea", "+mn-ea"), ("a:cs", "+mn-cs")):
        if _own_has({qn(tag)}):
            continue
        src = dflt_def.find(qn(tag)) if dflt_def is not None else None
        if src is not None:
            _insert_ordered(r_pr, copy.deepcopy(src), _RPR_ORDER)
        else:
            font = r_pr.makeelement(qn(tag), {})
            font.set("typeface", theme_font)
            _insert_ordered(r_pr, font, _RPR_ORDER)


def _shape_is_placeholder(sp) -> bool:
    return sp.find(f"{qn('p:nvSpPr')}/{qn('p:nvPr')}/{qn('p:ph')}") is not None


def _shape_referenced_by_animation(slide, shape_id) -> bool:
    try:
        timing = slide._element.find(qn("p:timing"))  # noqa: SLF001
    except Exception:
        return True  # can't tell -> don't touch it
    if timing is None:
        return False
    sid = str(shape_id)
    return any(el.get("spid") == sid for el in timing.iter(qn("p:spTgt")))


def _promotable_title_box(slide, shape, title: str) -> bool:
    """True when ``shape`` is a plain top-level text box holding exactly ``title``."""
    try:
        sp = shape._element  # noqa: SLF001
    except Exception:
        return False
    if sp.tag != qn("p:sp"):
        return False
    parent = sp.getparent()
    if parent is None or parent.tag != qn("p:spTree"):
        return False  # inside a group: its xfrm is group-relative
    if _shape_is_placeholder(sp):
        return False
    if sp.find(qn("p:style")) is not None:
        return False  # an autoshape's theme font/colour comes from p:style
    xfrm = sp.find(f"{qn('p:spPr')}/{qn('a:xfrm')}")
    if xfrm is None or xfrm.find(qn("a:off")) is None or xfrm.find(qn("a:ext")) is None:
        return False  # would inherit the layout title's position
    tx_body = sp.find(qn("p:txBody"))
    if tx_body is None:
        return False
    paras = tx_body.findall(qn("a:p"))
    if len(paras) != 1 or paras[0].find(qn("a:br")) is not None:
        return False  # other lines would become part of the title
    text = "".join(t.text or "" for t in paras[0].iter(qn("a:t"))).strip()
    return text == title.strip()


def _promote_to_title(prs, slide, shape) -> Optional[str]:
    """Turn a plain text box into the slide's title placeholder, look frozen."""
    sp = shape._element  # noqa: SLF001
    try:
        existing = slide.shapes.title
    except Exception:
        existing = None
    if existing is not None:
        # An EMPTY title placeholder (the slide was flagged untitled, so it has
        # no text) renders nothing in a slideshow — but two title placeholders
        # on one slide would be ambiguous. Drop it, unless an animation
        # targets it, in which case the caller falls back to the off-slide
        # title (which reuses this placeholder).
        if (existing.text or "").strip():
            return None
        if _shape_referenced_by_animation(slide, existing.shape_id):
            return None
        ex_el = existing._element  # noqa: SLF001
        ex_el.getparent().remove(ex_el)
    _freeze_text_box_look(prs, sp)
    nv_sp_pr = sp.find(qn("p:nvSpPr"))
    c_nv_sp_pr = nv_sp_pr.find(qn("p:cNvSpPr"))
    if c_nv_sp_pr is not None and "txBox" in c_nv_sp_pr.attrib:
        del c_nv_sp_pr.attrib["txBox"]  # it is a placeholder now, not a text box
    nv_pr = nv_sp_pr.find(qn("p:nvPr"))
    if nv_pr is None:
        nv_pr = etree.SubElement(nv_sp_pr, qn("p:nvPr"))
    ph = nv_pr.makeelement(qn("p:ph"), {"type": "title"})
    nv_pr.insert(0, ph)  # p:ph must be nvPr's first child
    return "promoted_existing_text"


def _off_slide_title(prs, slide, text: str) -> Optional[str]:
    """Give ``slide`` a title placeholder carrying ``text``, placed above the
    slide so it is never rendered. Reuses an empty title placeholder; else
    clones the layout's; else builds one."""
    how = None
    title = None
    try:
        title = slide.shapes.title
        if title is not None:
            how = "existing_placeholder_moved_off_slide"
    except Exception:
        title = None
    if title is None:
        layout_title = _layout_title_placeholder(slide)
        if layout_title is not None:
            try:
                slide.shapes.clone_placeholder(layout_title)
                title = slide.shapes.title
                how = "off_slide_title"
            except Exception:
                logger.debug("clone_placeholder failed; falling back to scratch title", exc_info=True)
                title = None
    if title is None:
        try:
            shapes = slide.shapes
            id_ = shapes._next_shape_id  # noqa: SLF001 - mirrors clone_placeholder
            # orient/sz must be valid ST strings ("horz"/"full") — new_placeholder_sp
            # assigns them unconditionally and rejects None.
            shapes._spTree.add_placeholder(id_, f"Title {id_}", PP_PLACEHOLDER.TITLE, "horz", "full", 0)  # noqa: SLF001
            title = slide.shapes.title
            how = "off_slide_title"
        except Exception:
            logger.debug("scratch title placeholder build failed", exc_info=True)
            return None
    if title is None:
        return None

    def _dim(name: str) -> int:
        # A scratch-built placeholder has no geometry to inherit; python-pptx
        # may answer None or raise for it.
        try:
            return int(getattr(title, name) or 0)
        except Exception:
            return 0

    try:
        slide_width = int(prs.slide_width or 0) or 12192000
        width = _dim("width") or max(slide_width - _EMU_PER_INCH, _EMU_PER_INCH)
        height = _dim("height") or _EMU_PER_INCH
        left = _dim("left") or _EMU_PER_INCH // 2
        title.text = text
        title.left = left
        title.width = width
        title.height = height
        # Entirely above the slide's top edge, with a margin.
        title.top = -(height + _EMU_PER_INCH // 2)
        # Screen readers read shapes in tree order: put the title first.
        sp = title._element  # noqa: SLF001
        tree = sp.getparent()
        first_shape_index = 2  # after p:nvGrpSpPr and p:grpSpPr
        if tree is not None and tree.index(sp) != first_shape_index:
            tree.remove(sp)
            tree.insert(first_shape_index, sp)
    except Exception:
        logger.debug("off-slide title placement failed", exc_info=True)
        return None
    return how


def _apply_slide_title(
    section: SectionNode,
    section_by_node_id: Dict[str, Any],
    text_by_node_id: Dict[str, Any],
    prs,
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Make the slide's own text its title — never a visible duplicate."""
    slide = section_by_node_id.get(section.id)
    if slide is None:
        skipped.append({"target_id": section.id, "reason": "slide_not_found_for_section"})
        return
    props = section.metadata.properties or {}
    title = str(props.get("set_slide_title") or "").strip()
    if not title:
        skipped.append({"target_id": section.id, "reason": "no_title_text"})
        return
    source = text_by_node_id.get(props.get("set_slide_title_source") or "")
    result = None
    if source is not None and _promotable_title_box(slide, source, title):
        try:
            result = _promote_to_title(prs, slide, source)
        except Exception:
            logger.debug("title promotion failed; using an off-slide title", exc_info=True)
            result = None
    if result is None:
        result = _off_slide_title(prs, slide, title)
    if result is None:
        skipped.append({"target_id": section.id, "reason": "could_not_create_title_placeholder"})
        return
    applied.append(
        {
            "kind": "slide_title",
            "action": "SET_SLIDE_TITLE",
            "target_id": section.id,
            "summary": f"slide title = {title!r} ({result})",
        }
    )


def _apply_document_metadata(
    prs,
    root: DocumentNode,
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Sync ``DocumentNode`` metadata (title, language) onto core properties."""

    core = prs.core_properties
    # Read before anything is written: the parser takes the deck language from
    # core_properties.language, so equal means no language fix was applied.
    source_language = (getattr(core, "language", None) or "").strip()
    source_title = (getattr(core, "title", None) or "").strip()

    title = ""
    if root.metadata.properties:
        title = (root.metadata.properties.get("title") or "").strip()
    # Same rule as the language: the deck's own title, read back unchanged, is
    # not a fix (it used to be reported as one on every deck that had a title).
    if title and title != source_title:
        try:
            core.title = title
            applied.append(
                {
                    "kind": "document_title",
                    "target_id": root.id,
                    "summary": f"core_properties.title = {title!r}",
                }
            )
        except Exception as exc:  # pragma: no cover
            skipped.append(
                {"target_id": root.id, "reason": f"failed_to_set_title:{exc}"}
            )

    language = (root.metadata.language or "").strip()
    # Only a CHANGED language is written. Re-asserting the source's own value
    # stamped a:rPr@lang onto every run of decks where nobody approved a
    # language fix (SET_DOCUMENT_LANGUAGE runs only when the deck has none).
    if language and language != source_language:
        try:
            core.language = language
            applied.append(
                {
                    "kind": "document_language",
                    "target_id": root.id,
                    "summary": f"core_properties.language = {language!r}",
                }
            )
        except Exception as exc:  # pragma: no cover
            skipped.append(
                {"target_id": root.id, "reason": f"failed_to_set_language:{exc}"}
            )
        # Screen readers read the spoken language from each run's a:rPr@lang,
        # not from core properties — set it on every text run.
        try:
            n = _set_pptx_run_langs(prs, language)
            applied.append(
                {
                    "kind": "document_language_runs",
                    "target_id": root.id,
                    "summary": f"a:rPr@lang = {language!r} on {n} run(s)",
                }
            )
        except Exception as exc:
            skipped.append(
                {"target_id": root.id, "reason": f"failed_to_set_run_lang:{exc}"}
            )


def _apply_image(
    image: ImageNode,
    image_by_node_id: Dict[str, Any],
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Apply alt text / decorative flag mutations to a picture shape.

    Decorative pictures get their alt text cleared and a ``decorative="1"``
    attribute added to ``<p:nvPicPr><p:cNvPr>`` (the closest OOXML accessor
    PowerPoint honors for the "Mark as decorative" checkbox).  Non-decorative
    pictures with alt text get ``shape.alternative_text`` set; this writes
    both the ``descr`` and ``title`` attributes on the underlying ``cNvPr``.
    """

    shape = image_by_node_id.get(image.id)
    if shape is None:
        skipped.append({"target_id": image.id, "reason": "shape_not_found_in_source"})
        return

    if image.is_decorative:
        if _shape_marked_decorative(shape):
            # Already decorative in the source (the parser read it from this
            # very extension): nothing was approved, so nothing is written —
            # not even clearing a descr PowerPoint never announces.
            return
        # Decorative: clear alt text and mark via the Office "decorative"
        # extension (the only mechanism PowerPoint actually honors).
        _clear_descr(shape)
        marked = _mark_shape_decorative(shape)
        applied.append(
            {
                "kind": "image_decorative",
                "target_id": image.id,
                "summary": ("descr cleared; " + ("marked decorative" if marked else "(decorative ext unavailable)")),
            }
        )
        return

    alt_text = (image.alt_text or "").strip()
    if not alt_text:
        # Nothing to write and the image is not decorative — leave the file
        # alone but record the skip so callers can see why.
        skipped.append(
            {"target_id": image.id, "reason": "alt_text_empty_and_not_decorative"}
        )
        return

    cnv = _find_cnvpr(shape)
    if cnv is not None and (cnv.get("descr") or "").strip() == alt_text and not _shape_marked_decorative(shape):
        # The alt text the parser READ from this shape — nothing was approved
        # for it, so nothing is written or reported. (Every picture that
        # already had a descr, "image.png" included, used to come back as an
        # applied "fix" on every run.)
        return
    if not _set_descr(shape, alt_text):
        skipped.append({"target_id": image.id, "reason": "cNvPr_not_found"})
        return

    # If the shape was previously marked decorative, drop the extension so
    # screen readers will announce the new alt text.
    _unmark_shape_decorative(shape)

    applied.append(
        {
            "kind": "image_alt_text",
            "target_id": image.id,
            "summary": f"descr={alt_text!r}",
        }
    )


# --- lxml helpers --------------------------------------------------------
# PowerPoint stores alt text in <p:cNvPr @descr> and the decorative flag in an
# Office 2017 extension. python-pptx's ``Picture`` does NOT expose
# ``alternative_text`` in current versions (setting it is a silent no-op that
# never reaches the XML), so we edit the underlying elements directly.

_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_ADEC_NS = "http://schemas.microsoft.com/office/drawing/2017/decorative"
_DECORATIVE_EXT_URI = "{C183D7F6-B498-43B3-948B-1728B52AA6E4}"


def _find_cnvpr(shape):
    """Return the <p:cNvPr> element for a shape/picture, or None."""
    try:
        for nv in shape._element.iter():
            tag = nv.tag
            if isinstance(tag, str) and tag.endswith("}cNvPr"):
                return nv
    except Exception:  # pragma: no cover - defensive
        return None
    return None


def _set_descr(shape, alt_text: str) -> bool:
    cnv = _find_cnvpr(shape)
    if cnv is None:
        return False
    cnv.set("descr", alt_text)
    return True


def _clear_descr(shape) -> None:
    cnv = _find_cnvpr(shape)
    if cnv is None:
        return
    for attr in ("descr", "title"):
        if attr in cnv.attrib:
            del cnv.attrib[attr]


def _mark_shape_decorative(shape) -> bool:
    """Mark a picture decorative via the Office 2017 <adec:decorative> ext.

    A bare ``decorative="1"`` attribute (what this code used to write) is NOT
    valid OOXML — PowerPoint ignores it, so the image stays announced. The real
    mechanism is an extension-list entry under <p:cNvPr>.
    """
    cnv = _find_cnvpr(shape)
    if cnv is None:
        return False
    try:
        _remove_decorative_ext(cnv)  # idempotent
        extlst = cnv.find(f"{{{_A_NS}}}extLst")
        if extlst is None:
            extlst = etree.SubElement(cnv, f"{{{_A_NS}}}extLst")
        ext = etree.SubElement(extlst, f"{{{_A_NS}}}ext")
        ext.set("uri", _DECORATIVE_EXT_URI)
        dec = etree.SubElement(ext, f"{{{_ADEC_NS}}}decorative", nsmap={"adec": _ADEC_NS})
        dec.set("val", "1")
        return True
    except Exception:  # pragma: no cover - defensive
        logger.debug("mark_decorative_failed", exc_info=True)
        return False


def _remove_decorative_ext(cnv) -> None:
    extlst = cnv.find(f"{{{_A_NS}}}extLst")
    if extlst is None:
        return
    for ext in list(extlst.findall(f"{{{_A_NS}}}ext")):
        if ext.get("uri") == _DECORATIVE_EXT_URI:
            extlst.remove(ext)
    if len(extlst) == 0:
        cnv.remove(extlst)


def _unmark_shape_decorative(shape) -> None:
    """Remove the Office decorative extension if present."""
    cnv = _find_cnvpr(shape)
    if cnv is None:
        return
    try:
        _remove_decorative_ext(cnv)
    except Exception:  # pragma: no cover
        logger.debug("unmark_decorative_failed", exc_info=True)


def _apply_table_cell(
    cell: TableCellNode,
    reference_index: Dict[str, Any],
    cell_by_node_id: Dict[str, Tuple[Any, int, int]],
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """When a first-row cell is HEADER, set ``firstRow="1"`` on the table.

    PowerPoint tables have no per-cell scope/header attribute; the closest
    accessibility hint is the ``firstRow`` flag on ``<a:tblPr>``, which tells
    PowerPoint to render (and screen readers to announce) the first row as a
    header band.  We therefore only act on HEADER cells whose row index is 0
    and we set the flag on the parent table.  Repeated promotions on the
    same table are idempotent.
    """

    if cell.cell_type != TableCellType.HEADER:
        return

    pair = cell_by_node_id.get(cell.id)
    if pair is None:
        skipped.append({"target_id": cell.id, "reason": "cell_not_found_in_source"})
        return
    table_shape, row_index, _col_index = pair

    if row_index != 0:
        # Only first-row headers map to the firstRow band; non-first-row
        # header cells in PPTX have no canonical mark.
        skipped.append(
            {"target_id": cell.id, "reason": f"header_cell_not_in_first_row:row={row_index}"}
        )
        return

    try:
        table = table_shape.table
        tbl = table._tbl
    except Exception as exc:
        skipped.append(
            {"target_id": cell.id, "reason": f"failed_to_reach_tbl:{exc}"}
        )
        return

    tbl_pr = tbl.find(qn("a:tblPr"))
    if tbl_pr is None:
        # ``<a:tblPr>`` is required by the OOXML schema for tables, but be
        # defensive: create one if it's missing.
        try:
            from lxml import etree

            tbl_pr = etree.SubElement(tbl, qn("a:tblPr"))
            tbl.insert(0, tbl_pr)
        except Exception as exc:  # pragma: no cover
            skipped.append(
                {"target_id": cell.id, "reason": f"failed_to_create_tblPr:{exc}"}
            )
            return

    if tbl_pr.get("firstRow") in {"1", "true"}:
        # Already a header band in the source (or set by an earlier cell of
        # this row): nothing written, so nothing reported as applied.
        return

    tbl_pr.set("firstRow", "1")
    applied.append(
        {
            "kind": "table_first_row_header",
            "target_id": cell.id,
            "summary": "tblPr/@firstRow=1",
        }
    )


def _apply_synthetic_pptx_table_header(
    table: TableNode,
    table_by_node_id: Dict[str, Any],
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Insert a real header ``<a:tr>`` when the tree's first row is synthesized.

    ``AddTableHeadersExecutor`` inserts a placeholder header row (tagged
    ``metadata.properties['synthesized']``) at the top of a header-less table
    whose first data row doesn't read like a header. That row has no source
    ``<a:tr>``, so we materialise it: a new top row plus the ``firstRow`` band so
    PowerPoint and screen readers treat it as the header.
    """

    rows = [c for c in table.children if isinstance(c, TableRowNode)]
    if not rows:
        return
    first = rows[0]
    props = (first.metadata.properties if first.metadata else None) or {}
    if not props.get("synthesized"):
        return  # promote path handled per-cell; nothing to insert

    shape = table_by_node_id.get(table.id)
    if shape is None:
        skipped.append({"target_id": table.id, "reason": "table_not_found_in_source"})
        return

    header_cells = [c for c in first.children if isinstance(c, TableCellNode)]
    texts = [
        ((c.content.text or "").strip() if c.content else "") or " " for c in header_cells
    ]
    if not texts:
        return

    try:
        _insert_pptx_header_row(shape.table, texts)
    except Exception as exc:  # pragma: no cover - defensive
        skipped.append({"target_id": table.id, "reason": f"failed_to_insert_pptx_header:{exc}"})
        return

    applied.append(
        {
            "kind": "table_header_row_inserted",
            "target_id": table.id,
            "summary": f"inserted {len(texts)}-column header row + firstRow band",
        }
    )


def _insert_pptx_header_row(pptx_table, texts: List[str]) -> None:
    """Build and insert a header ``<a:tr>`` at the top of ``pptx_table``."""

    from lxml import etree

    tbl = pptx_table._tbl

    # Defense in depth: the row MUST have exactly one cell per <a:gridCol>, or
    # the table won't reopen. The executor already sizes the header to the grid,
    # but pad/truncate here so the writer is self-consistent for any input.
    grid = tbl.find(qn("a:tblGrid"))
    n_cols = len(grid.findall(qn("a:gridCol"))) if grid is not None else len(texts)
    if n_cols > 0:
        texts = (list(texts) + [" "] * n_cols)[:n_cols]

    existing_trs = tbl.findall(qn("a:tr"))
    # Reuse an existing row height so the new row matches; default ~0.3in (EMU).
    height = existing_trs[0].get("h") if existing_trs else None

    tr = etree.Element(qn("a:tr"))
    tr.set("h", height or "370840")
    for text in texts:
        tc = etree.SubElement(tr, qn("a:tc"))
        tx_body = etree.SubElement(tc, qn("a:txBody"))
        etree.SubElement(tx_body, qn("a:bodyPr"))
        para = etree.SubElement(tx_body, qn("a:p"))
        run = etree.SubElement(para, qn("a:r"))
        r_pr = etree.SubElement(run, qn("a:rPr"))
        r_pr.set("lang", "en-US")
        r_pr.set("b", "1")  # bold — the visual header convention
        t = etree.SubElement(run, qn("a:t"))
        t.text = text
        etree.SubElement(tc, qn("a:tcPr"))

    # Insert before the first existing row (after <a:tblPr>/<a:tblGrid>).
    if existing_trs:
        existing_trs[0].addprevious(tr)
    else:
        tbl.append(tr)

    # Mark the header band so the new first row is announced as the header.
    tbl_pr = tbl.find(qn("a:tblPr"))
    if tbl_pr is None:
        tbl_pr = etree.SubElement(tbl, qn("a:tblPr"))
        tbl.insert(0, tbl_pr)
    tbl_pr.set("firstRow", "1")


# ---------------------------------------------------------------------------
# Typed fake-list -> real bullet formatting (a:buChar / a:buAutoNum)
# ---------------------------------------------------------------------------

_A_URI = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _apply_pptx_list_conversion(node, text_by_node_id, applied, skipped) -> None:
    """Give a typed fake-list text box real bullet semantics.

    Each paragraph whose text starts with a typed marker gets explicit bullet
    formatting on its ``a:pPr`` — ``a:buChar`` for dashes/asterisks, or
    ``a:buAutoNum type="arabicPeriod"`` for sequential numbers — and the
    literal marker is stripped from its first run.
    """

    shape = text_by_node_id.get(node.id)
    if shape is None:
        skipped.append({
            "target_id": node.id,
            "reason": "text shape not found for list conversion",
        })
        return

    converted = 0
    try:
        paragraphs = list(shape.text_frame.paragraphs)
    except Exception:
        skipped.append({"target_id": node.id, "reason": "text_frame unreadable"})
        return

    for paragraph in paragraphs:
        line = "".join((r.text or "") for r in paragraph.runs).strip()
        sig = _fake_list_signature(line) if line else None
        if sig is None or _paragraph_has_real_bullet(paragraph):
            continue
        kind = sig[0]

        pPr = paragraph._p.find(qn("a:pPr"))
        if pPr is None:
            pPr = paragraph._p.makeelement(qn("a:pPr"), {})
            paragraph._p.insert(0, pPr)  # a:pPr must be the first child of a:p
        # Drop an explicit "no bullet" marker if present, then add the bullet.
        for tag in ("a:buNone", "a:buChar", "a:buAutoNum"):
            stale = pPr.find(qn(tag))
            if stale is not None:
                pPr.remove(stale)
        if kind == "bullet":
            bu = pPr.makeelement(qn("a:buChar"), {"char": "•"})
        else:
            bu = pPr.makeelement(qn("a:buAutoNum"), {"type": "arabicPeriod"})
        # OOXML CT_TextParagraphProperties requires the bullet group to come
        # BEFORE a:tabLst / a:defRPr / a:extLst. A real PowerPoint paragraph
        # usually already has an a:defRPr, so a naive append produces an
        # out-of-order child that PowerPoint refuses to open. Insert before
        # the first trailing element instead.
        _trailing = (qn("a:tabLst"), qn("a:defRPr"), qn("a:extLst"))
        anchor = next((c for c in pPr if c.tag in _trailing), None)
        if anchor is not None:
            anchor.addprevious(bu)
        else:
            pPr.append(bu)
        # A bullet with no hanging indent is drawn touching its text
        # ("•alpha" — rendered that way by PowerPoint itself), which reads
        # worse than the typed "- alpha" it replaces. Give it the indent
        # PowerPoint's own Bullets / Numbering buttons write, unless the author
        # already set one.
        if pPr.get("marL") is None and pPr.get("indent") is None:
            try:
                lvl = max(0, int(pPr.get("lvl") or 0))
            except ValueError:
                lvl = 0
            hang = 285750 if kind == "bullet" else 342900
            pPr.set("marL", str(hang + lvl * 457200))
            pPr.set("indent", str(-hang))

        # Strip the typed marker from the first non-empty run.
        for run in paragraph.runs:
            if run.text and run.text.strip():
                new_text = strip_fake_list_prefix(run.text)
                if new_text != run.text:
                    run.text = new_text
                break
        converted += 1

    if converted == 0:
        skipped.append({"target_id": node.id, "reason": "no typed list lines found"})
        return
    applied.append({
        "kind": "list_conversion",
        "target_id": node.id,
        "summary": f"converted {converted} typed lines to real bullets",
    })
