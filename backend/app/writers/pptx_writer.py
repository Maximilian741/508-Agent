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

import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
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
    _iter_hyperlink_groups,
    _iter_shapes_recursive,
    _paragraph_has_real_bullet,
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
            # An untitled slide the SET_SLIDE_TITLE executor approved — insert a
            # real title placeholder carrying the derived text.
            _apply_slide_title(node, section_by_node_id, applied, skipped)
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

    # Step 6 — persist.
    try:
        prs.save(str(output_path))
    except Exception as exc:
        logger.exception("Failed to save remediated pptx: %s", exc)
        skipped.append(
            {"target_id": tree.root.id, "reason": f"failed_to_save_pptx:{exc}"}
        )

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
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
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


def _ensure_slide_title(slide, text: str) -> Optional[str]:
    """Make ``slide`` carry a real title placeholder containing ``text``.

    Three tiers, so this essentially never fails (keeping the executor's
    SUCCESS honest — a fix that's always written, never just claimed):
      1. A title placeholder already on the slide (e.g. present but empty) —
         just set its text.
      2. Clone the layout's title placeholder onto the slide.
      3. No layout title placeholder (e.g. a Blank layout) — build a title
         placeholder (idx 0) from scratch.
    Returns a short tag on success, or None if every tier failed.
    """
    try:
        existing = slide.shapes.title
    except Exception:
        existing = None
    if existing is not None:
        try:
            existing.text = text
            return "set_existing"
        except Exception:
            return None

    layout_title = _layout_title_placeholder(slide)
    if layout_title is not None:
        try:
            slide.shapes.clone_placeholder(layout_title)
            title = slide.shapes.title
            if title is not None:
                title.text = text
                return "cloned_from_layout"
        except Exception:
            logger.debug("clone_placeholder failed; falling back to scratch title", exc_info=True)

    # Tier 3 — construct a title placeholder directly.
    try:
        shapes = slide.shapes
        id_ = shapes._next_shape_id  # noqa: SLF001 - mirrors clone_placeholder
        name = f"Title {id_}"
        # orient/sz must be valid ST strings ("horz"/"full") — new_placeholder_sp
        # assigns them unconditionally and rejects None.
        shapes._spTree.add_placeholder(id_, name, PP_PLACEHOLDER.TITLE, "horz", "full", 0)  # noqa: SLF001
        title = slide.shapes.title
        if title is not None:
            title.text = text
            return "built_from_scratch"
    except Exception:
        logger.debug("scratch title placeholder build failed", exc_info=True)
    return None


def _apply_slide_title(
    section: SectionNode,
    section_by_node_id: Dict[str, Any],
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Insert/set a real title placeholder on the slide for ``section``."""
    slide = section_by_node_id.get(section.id)
    if slide is None:
        skipped.append({"target_id": section.id, "reason": "slide_not_found_for_section"})
        return
    title = (section.metadata.properties or {}).get("set_slide_title")
    if not title:
        skipped.append({"target_id": section.id, "reason": "no_title_text"})
        return
    result = _ensure_slide_title(slide, str(title))
    if result is None:
        skipped.append({"target_id": section.id, "reason": "could_not_create_title_placeholder"})
        return
    applied.append(
        {
            "kind": "slide_title",
            "target_id": section.id,
            "summary": f"slide title = {str(title)!r} ({result})",
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

    title = ""
    if root.metadata.properties:
        title = (root.metadata.properties.get("title") or "").strip()
    if title:
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
    if language:
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
        applied.append(
            {
                "kind": "table_first_row_header",
                "target_id": cell.id,
                "summary": "tblPr/@firstRow already 1",
            }
        )
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
