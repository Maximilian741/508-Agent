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

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.oxml.ns import qn

from app.models.accessibility import (
    AccessibilityTree,
    DocumentNode,
    ImageNode,
    TableCellNode,
    TableCellType,
)
from app.parsers.pptx_parser import PPTXParser, _IdCounter

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
    image_by_node_id, cell_by_node_id = _index_shapes_by_parser_id(prs)

    # Step 4 — document-level metadata (title, language) first.
    _apply_document_metadata(prs, tree.root, applied, skipped)

    # Step 5 — per-node mutations.  We only touch nodes whose mutation kinds
    # this writer supports; everything else passes silently (it is not an
    # error for the tree to contain unsupported node types).
    for node in mutated_index.values():
        if isinstance(node, ImageNode):
            _apply_image(node, image_by_node_id, applied, skipped)
        elif isinstance(node, TableCellNode):
            _apply_table_cell(
                node,
                reference_index,
                cell_by_node_id,
                applied,
                skipped,
            )

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


def _index_shapes_by_parser_id(
    prs,
) -> Tuple[Dict[str, Any], Dict[str, Tuple[Any, int, int]]]:
    """Walk ``prs`` exactly the way :func:`PPTXParser.parse_to_tree` does and
    pair each minted id with the underlying python-pptx object.

    Returns two dicts:

    * ``image_by_node_id`` — ``{node_id: shape}`` for picture shapes.
    * ``cell_by_node_id`` — ``{node_id: (table_shape, row_index, col_index)}``
      for table cells.  We carry the table shape (so we can reach
      ``table._tbl``) plus the cell's grid position so the table-header
      mutation can promote the right row.
    """

    image_by_node_id: Dict[str, Any] = {}
    cell_by_node_id: Dict[str, Tuple[Any, int, int]] = {}

    ids = _IdCounter()

    for slide_index, slide in enumerate(prs.slides, start=1):
        # The parser mints a section + (optional) heading id BEFORE iterating
        # shapes.  We mirror that to keep the counter in lock-step.
        ids(f"slide-{slide_index}-section")
        slide_title = ""
        try:
            if slide.shapes.title and slide.shapes.title.text:
                slide_title = slide.shapes.title.text.strip()
        except Exception:
            slide_title = ""
        if slide_title:
            ids(f"slide-{slide_index}-h")

        for shape in slide.shapes:
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
                ids(f"slide-{slide_index}-table")
                continue
            if hasattr(shape, "text_frame") and shape.text_frame:
                text = (shape.text or "").strip()
                if not text:
                    continue
                # Paragraph id allocated by the parser; we don't need it but
                # the counter must advance.
                ids(f"slide-{slide_index}-p")
                # Hyperlinks inside runs each consume a link-id.
                try:
                    for paragraph in shape.text_frame.paragraphs:
                        for run in paragraph.runs:
                            try:
                                href = run.hyperlink.address
                            except Exception:
                                href = None
                            if href:
                                ids(f"slide-{slide_index}-link")
                except Exception:
                    # Defensive — text_frame parsing rarely fails but a
                    # corrupted shape shouldn't take down id alignment.
                    logger.debug(
                        "skip_text_frame_walk slide=%s", slide_index, exc_info=True
                    )

    return image_by_node_id, cell_by_node_id


# ---------------------------------------------------------------------------
# Mutation appliers
# ---------------------------------------------------------------------------


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
        # Clear any existing alt text — decorative shapes must not announce.
        try:
            shape.alternative_text = ""
        except Exception as exc:  # pragma: no cover
            skipped.append(
                {"target_id": image.id, "reason": f"failed_to_clear_alt:{exc}"}
            )
            return
        marked = _mark_shape_decorative(shape)
        applied.append(
            {
                "kind": "image_decorative",
                "target_id": image.id,
                "summary": (
                    "alternative_text='' " + ("decorative='1'" if marked else "(decorative attr unavailable)")
                ),
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

    try:
        shape.alternative_text = alt_text
    except Exception as exc:
        skipped.append(
            {"target_id": image.id, "reason": f"failed_to_set_alt:{exc}"}
        )
        return

    # If the shape was previously marked decorative, clear that attribute so
    # screen readers will announce the new alt text.
    _unmark_shape_decorative(shape)

    applied.append(
        {
            "kind": "image_alt_text",
            "target_id": image.id,
            "summary": f"alternative_text={alt_text!r}",
        }
    )


def _mark_shape_decorative(shape) -> bool:
    """Set ``decorative="1"`` on the picture's ``<p:cNvPr>`` element.

    PowerPoint stores the "Mark as decorative" checkbox as an extension
    list entry on ``cNvPr``.  python-pptx does not expose this directly,
    so we patch the lxml element.  Returns ``True`` if the attribute was
    set (or already present); ``False`` if the element layout was not what
    we expected and we declined to touch it.
    """

    try:
        element = shape._element
        # ``cNvPr`` lives at <p:nvSpPr|nvPicPr>/<p:cNvPr> on a Picture shape.
        cnv_pr = None
        for nv in element.iter():
            tag = nv.tag
            if isinstance(tag, str) and tag.endswith("}cNvPr"):
                cnv_pr = nv
                break
        if cnv_pr is None:
            return False
        # The decorative attribute is unprefixed in the OOXML schema PowerPoint
        # writes when a user clicks "Mark as decorative".  Setting it as a
        # plain attribute matches what PowerPoint round-trips.
        cnv_pr.set("decorative", "1")
        return True
    except Exception:  # pragma: no cover - defensive
        logger.debug("mark_decorative_failed", exc_info=True)
        return False


def _unmark_shape_decorative(shape) -> None:
    """Best-effort removal of the ``decorative`` attribute on ``cNvPr``."""

    try:
        element = shape._element
        for nv in element.iter():
            tag = nv.tag
            if isinstance(tag, str) and tag.endswith("}cNvPr"):
                if nv.get("decorative") is not None:
                    del nv.attrib["decorative"]
                break
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
