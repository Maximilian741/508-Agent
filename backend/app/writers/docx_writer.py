"""DOCX writer — applies :class:`AccessibilityTree` mutations back to a .docx.

The companion :mod:`app.parsers.docx_parser` module turns a Word document into
an :class:`AccessibilityTree`.  After analyzers / executors mutate that tree
(for example, filling in alt text, normalizing heading levels, or marking a
table's first row as a header), this module is responsible for materializing
those mutations back onto a copy of the original ``.docx`` file.

Design notes
------------

* **Always copy first.**  The source ``.docx`` is duplicated to the output
  path *before* any mutation; the source file itself is never written to.
* **Re-parse for id alignment.**  The parser uses a stateful ``_IdCounter``
  to mint stable ids (``docx-h-1``, ``docx-img-2``, ...).  Because the
  counter is deterministic, re-parsing the source produces the exact same
  ids in the same structural order as the tree the caller hands us.  We
  rely on that property: when looking up the source counterpart of a node
  in the (mutated) tree, we walk the source's paragraphs and tables in
  document order and assign them ids using the same counter — then match.
* **Defensive matching.**  If a tree node cannot be paired to anything in
  the source (rId no longer present, heading index drifted, etc.), we log
  it under ``skipped`` and keep going.  We never crash the writer for one
  bad mutation.
* **Minimal-touch XML.**  Where python-docx exposes a setter (paragraph
  style, core property), we use it.  For things python-docx does not
  expose cleanly (``descr``/``hidden`` attributes on ``<wp:docPr>``,
  ``<w:tblHeader/>`` inside ``<w:trPr>``) we manipulate the lxml elements
  directly.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from app.models.accessibility import (
    AccessibilityTree,
    DocumentNode,
    HeadingNode,
    ImageNode,
    LinkNode,
    ParagraphNode,
    TableCellNode,
    TableCellType,
    TableNode,
    TableRowNode,
)
from app.parsers.docx_parser import DOCXParser, _IdCounter, _heading_level_from_style

logger = logging.getLogger(__name__)


# Drawing / picture XML namespaces used while looking up <wp:docPr> elements
# corresponding to a particular image rId.  These mirror the constants in
# :mod:`app.parsers.docx_parser`.
_DRAWING_NS = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_DRAWINGML_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_PIC_NS = "{http://schemas.openxmlformats.org/drawingml/2006/picture}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def write_remediated_docx(
    source_path: Path,
    tree: AccessibilityTree,
    output_path: Path,
) -> dict:
    """Apply ``tree`` mutations to a copy of ``source_path`` and write it out.

    Parameters
    ----------
    source_path:
        Path to the original ``.docx`` file that produced ``tree``.
    tree:
        The (possibly mutated) :class:`AccessibilityTree` whose state should
        be reflected in the output document.
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

    # Step 1: copy source -> output before mutating anything.  This protects
    # the source from accidental edits and lets python-docx open the output
    # path directly.
    shutil.copyfile(source_path, output_path)

    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    # Step 2: re-parse the source to obtain a tree with the same id sequence.
    # This gives us a "reference tree" we can walk in lock-step with the
    # mutated tree to pair each node-id with its source counterpart.
    try:
        reference_result = DOCXParser().parse_to_tree(str(source_path))
        reference_tree = reference_result.tree
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to re-parse source for id alignment: %s", exc)
        reference_tree = None

    mutated_index = _index_tree(tree)
    reference_index = _index_tree(reference_tree) if reference_tree is not None else {}

    doc = Document(str(output_path))

    # Step 3: build per-node-id lookups against the *source* document.  The
    # iterators below assign the same ids as the parser would, which lets us
    # pair tree-node-ids to lxml elements in the source XML.
    paragraph_by_id = _index_paragraphs_by_parser_id(doc)
    image_by_rid = _index_image_doc_pr_by_rid(doc)
    table_rows_by_id = _index_table_rows_by_parser_id(doc)
    table_cells_by_id = _index_table_cells_by_parser_id(doc)
    hyperlink_by_id = _index_hyperlinks_by_parser_id(doc)

    # Step 4: walk the mutated tree and apply each supported mutation.  Order
    # is intentional — document-level metadata first, then per-node updates.
    _apply_document_metadata(doc, tree.root, applied, skipped)

    for node in mutated_index.values():
        if isinstance(node, ImageNode):
            _apply_image(node, image_by_rid, applied, skipped)
        elif isinstance(node, LinkNode):
            _apply_link(node, hyperlink_by_id, applied, skipped)
        elif isinstance(node, HeadingNode):
            _apply_heading(node, paragraph_by_id, applied, skipped)
        elif isinstance(node, TableCellNode):
            # Cells are only meaningful via their parent row; we mark the
            # row as a repeating header row whenever any cell in the first
            # row is HEADER.  We resolve the row by looking up the cell's
            # parent in the reference tree (since the mutated tree may not
            # have a back-pointer).
            _apply_table_cell(
                node,
                reference_index,
                table_rows_by_id,
                table_cells_by_id,
                applied,
                skipped,
            )

    # Step 5: persist.
    doc.save(str(output_path))

    return {"applied": applied, "skipped": skipped}


# ---------------------------------------------------------------------------
# Tree indexing helpers
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
        # Children go onto the stack in reverse so the iteration order is
        # stable / left-to-right when callers iterate ``out.values()``.
        for child in reversed(node.children):
            stack.append(child)
    return out


# ---------------------------------------------------------------------------
# Source-document indexing — re-mints the parser's ids against the source XML
# so we can match tree-node-ids to lxml elements.
# ---------------------------------------------------------------------------


def _index_paragraphs_by_parser_id(doc) -> Dict[str, Any]:
    """Map ``docx-h-N`` / ``docx-p-N`` ids to python-docx Paragraph objects.

    This re-implements the *paragraph half* of ``parse_to_tree`` so the ids
    line up with what the parser produced.  We do not need to be perfect —
    only the heading and paragraph counters matter for the writer's lookups.
    Lists and inline images are intentionally skipped here because the writer
    does not currently mutate them.
    """

    ids = _IdCounter()
    out: Dict[str, Any] = {}
    for paragraph in doc.paragraphs:
        style_name = (paragraph.style.name or "") if paragraph.style else ""
        text = (paragraph.text or "").strip()

        # The parser groups numbered/bulleted runs into list nodes; those do
        # NOT consume an `docx-p` id.  Mirror that here.
        pPr = paragraph._p.find(qn("w:pPr"))
        is_list = pPr is not None and pPr.find(qn("w:numPr")) is not None
        if is_list:
            continue

        heading_level = _heading_level_from_style(style_name)
        if heading_level:
            out[ids("docx-h")] = paragraph
            continue

        # The parser only mints a `docx-p` id when the paragraph has text and
        # no hyperlink runs.  We mirror that condition exactly so the
        # counter advances in lock-step.
        has_hyperlink = paragraph._p.find(
            f".//{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}hyperlink"
        ) is not None
        if text and not has_hyperlink:
            out[ids("docx-p")] = paragraph
    return out


def _index_image_doc_pr_by_rid(doc) -> Dict[str, Any]:
    """Map image rIds to their corresponding ``<wp:docPr>`` lxml element.

    Note an rId may appear multiple times in the body (the same image
    inserted twice).  We keep the *first* occurrence — downstream lookups
    match by ``image_rid`` which the parser also reads off the first hit.
    Callers that need to update every visual instance should iterate the
    document themselves.
    """

    out: Dict[str, Any] = {}
    body = doc.element.body
    for drawing in body.iter(f"{_DRAWING_NS}inline"):
        _record_doc_pr(drawing, out)
    for drawing in body.iter(f"{_DRAWING_NS}anchor"):
        _record_doc_pr(drawing, out)
    return out


def _record_doc_pr(drawing, out: Dict[str, Any]) -> None:
    blip = drawing.find(f".//{_DRAWINGML_NS}blip")
    if blip is None:
        blip = drawing.find(f".//{_PIC_NS}blip")
    if blip is None:
        return
    rid = blip.get(f"{_REL_NS}embed") or blip.get(f"{_REL_NS}link")
    if not rid:
        return
    doc_pr = drawing.find(f".//{_DRAWING_NS}docPr")
    if doc_pr is None:
        doc_pr = drawing.find(f".//{_DRAWINGML_NS}docPr")
    if doc_pr is None:
        return
    # Collect EVERY occurrence — the same image (rId) may be inserted multiple
    # times, and each visual instance needs its own docPr updated.
    out.setdefault(rid, []).append(doc_pr)


def _index_table_rows_by_parser_id(doc) -> Dict[str, Any]:
    """Map ``docx-row-N`` ids to python-docx _Row objects."""

    ids = _IdCounter()
    out: Dict[str, Any] = {}
    # The parser walks paragraphs first (which mint cell ids only inside
    # tables) then iterates ``doc.tables`` to assign row/cell ids.  Mirror
    # that order: we visit tables top-level, allocating cells then rows
    # exactly like ``_table_to_node`` does.
    for table in doc.tables:
        for row in table.rows:
            for _ in row.cells:
                ids("docx-cell")
            out[ids("docx-row")] = row
        ids("docx-table")
    return out


def _index_table_cells_by_parser_id(doc) -> Dict[str, Tuple[Any, Any]]:
    """Map ``docx-cell-N`` ids to ``(row, cell)`` tuples."""

    ids = _IdCounter()
    out: Dict[str, Tuple[Any, Any]] = {}
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                out[ids("docx-cell")] = (row, cell)
            ids("docx-row")
        ids("docx-table")
    return out


# ---------------------------------------------------------------------------
# Mutation appliers
# ---------------------------------------------------------------------------


def _set_docx_default_lang(doc, language: str) -> None:
    """Set ``w:lang`` on the document defaults (``styles.xml``).

    Screen readers and the Word Accessibility Checker read the document language
    from ``w:lang`` (docDefaults / run properties), NOT from the Dublin Core
    ``dc:language`` core property. Setting only the latter does not make content
    speak in the right language — so we set the run-default ``w:lang`` here.
    """
    styles_el = doc.styles.element  # <w:styles>
    doc_defaults = styles_el.find(qn("w:docDefaults"))
    if doc_defaults is None:
        doc_defaults = OxmlElement("w:docDefaults")
        styles_el.insert(0, doc_defaults)
    rpr_default = doc_defaults.find(qn("w:rPrDefault"))
    if rpr_default is None:
        rpr_default = OxmlElement("w:rPrDefault")
        doc_defaults.append(rpr_default)
    rpr = rpr_default.find(qn("w:rPr"))
    if rpr is None:
        rpr = OxmlElement("w:rPr")
        rpr_default.append(rpr)
    lang = rpr.find(qn("w:lang"))
    if lang is None:
        lang = OxmlElement("w:lang")
        rpr.append(lang)
    lang.set(qn("w:val"), language)


def _apply_document_metadata(
    doc,
    root: DocumentNode,
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Sync ``DocumentNode`` metadata (language, title) onto core properties."""

    core = doc.core_properties

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
        except Exception as exc:  # pragma: no cover - python-docx setter rarely fails
            skipped.append(
                {"target_id": root.id, "reason": f"failed_to_set_language: {exc}"}
            )
        # The accessibility-relevant location: w:lang on the document defaults.
        try:
            _set_docx_default_lang(doc, language)
            applied.append(
                {
                    "kind": "document_language_wlang",
                    "target_id": root.id,
                    "summary": f"w:lang = {language!r}",
                }
            )
        except Exception as exc:
            skipped.append(
                {"target_id": root.id, "reason": f"failed_to_set_wlang: {exc}"}
            )

    title = (root.metadata.properties.get("title") if root.metadata.properties else None) or ""
    title = title.strip()
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
                {"target_id": root.id, "reason": f"failed_to_set_title: {exc}"}
            )


def _apply_image(
    image: ImageNode,
    image_by_rid: Dict[str, Any],
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Apply alt text / decorative-flag mutations to an image's docPr.

    Edge case: an image referenced from a different rId (e.g. the parser
    assigned one but the source has been edited since) cannot be paired and
    will be skipped.  Headers/footers also live on a different part and are
    out of scope for this writer.
    """

    rid = (image.metadata.properties or {}).get("image_rid")
    if not rid:
        skipped.append({"target_id": image.id, "reason": "image_rid_missing_on_node"})
        return
    doc_prs = image_by_rid.get(rid)
    if not doc_prs:
        skipped.append({"target_id": image.id, "reason": f"image_rid_not_found_in_source:{rid}"})
        return
    if not isinstance(doc_prs, list):  # back-compat if ever a single element
        doc_prs = [doc_prs]

    if image.is_decorative:
        # Decorative: descr must be empty AND hidden=1 on every instance.
        for doc_pr in doc_prs:
            doc_pr.set("descr", "")
            if doc_pr.get("title"):
                doc_pr.set("title", "")
            if doc_pr.get("hidden") not in {"1", "true"}:
                doc_pr.set("hidden", "1")
        applied.append(
            {
                "kind": "image_decorative",
                "target_id": image.id,
                "summary": f"docPr[@rid={rid}] x{len(doc_prs)} descr='' hidden=1",
            }
        )
        return

    alt_text = (image.alt_text or "").strip()
    if not alt_text:
        # Nothing to write — leave as-is and record the skip so callers can
        # see we deliberately did not blank an existing description.
        skipped.append({"target_id": image.id, "reason": "alt_text_empty_and_not_decorative"})
        return

    # Write the alt text to EVERY visual instance of this image.
    for doc_pr in doc_prs:
        doc_pr.set("descr", alt_text)
        if doc_pr.get("hidden") in {"1", "true"}:
            doc_pr.set("hidden", "0")
    applied.append(
        {
            "kind": "image_alt_text",
            "target_id": image.id,
            "summary": f"docPr[@rid={rid}] x{len(doc_prs)} descr={alt_text!r}",
        }
    )


def _index_hyperlinks_by_parser_id(doc) -> Dict[str, Any]:
    """Map ``docx-link-N`` ids to their ``<w:hyperlink>`` element.

    Mirrors the parser walk exactly (``doc.paragraphs`` then ``.//w:hyperlink``,
    skipping hyperlinks with no visible text) so ids align.
    """
    out: Dict[str, Any] = {}
    n = 0
    for para in doc.paragraphs:
        for hyperlink in para._p.iterfind(f".//{qn('w:hyperlink')}"):
            text = "".join((t.text or "") for t in hyperlink.iterfind(f".//{qn('w:t')}")).strip()
            if not text:
                continue
            n += 1
            out[f"docx-link-{n}"] = hyperlink
    return out


def _apply_link(
    link: LinkNode,
    hyperlink_by_id: Dict[str, Any],
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Rewrite a hyperlink's display text (e.g. 'click here' -> 'Visit example.com').

    The new text replaces the first text run; remaining runs in the hyperlink are
    cleared. The link target and the first run's formatting are preserved.
    """
    hyperlink = hyperlink_by_id.get(link.id)
    if hyperlink is None:
        skipped.append({"target_id": link.id, "reason": "hyperlink_not_found_in_source"})
        return
    new_text = (link.content.text or "").strip() if link.content else ""
    if not new_text:
        skipped.append({"target_id": link.id, "reason": "empty_link_text"})
        return
    t_elems = list(hyperlink.iterfind(f".//{qn('w:t')}"))
    if not t_elems:
        skipped.append({"target_id": link.id, "reason": "no_text_run_in_hyperlink"})
        return
    current = "".join((t.text or "") for t in t_elems).strip()
    if current == new_text:
        skipped.append({"target_id": link.id, "reason": "no_change_required"})
        return
    t_elems[0].text = new_text
    t_elems[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for t in t_elems[1:]:
        t.text = ""
    applied.append({"kind": "link_text", "target_id": link.id, "summary": f"{current!r} -> {new_text!r}"})


def _apply_heading(
    heading: HeadingNode,
    paragraph_by_id: Dict[str, Any],
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Update a heading paragraph's style to ``Heading {level}``.

    Edge case: headings nested inside table cells are not currently exposed
    by the parser via ``docx-h-N`` ids, so they would not be paired here.
    They will appear in ``skipped`` if a caller manufactures such a node.
    """

    paragraph = paragraph_by_id.get(heading.id)
    if paragraph is None:
        skipped.append({"target_id": heading.id, "reason": "paragraph_not_found_for_heading"})
        return
    level = max(1, min(6, int(heading.level)))
    style_name = f"Heading {level}"
    try:
        paragraph.style = paragraph.part.document.styles[style_name]
    except KeyError:
        # The document doesn't define that built-in style yet; assigning by
        # name forces python-docx to look it up and create-if-needed.
        try:
            paragraph.style = style_name
        except Exception as exc:
            skipped.append(
                {"target_id": heading.id, "reason": f"failed_to_set_style:{exc}"}
            )
            return
    except Exception as exc:  # pragma: no cover
        skipped.append({"target_id": heading.id, "reason": f"failed_to_set_style:{exc}"})
        return
    applied.append(
        {
            "kind": "heading_level",
            "target_id": heading.id,
            "summary": f"paragraph.style = {style_name!r}",
        }
    )


def _apply_table_cell(
    cell: TableCellNode,
    reference_index: Dict[str, Any],
    table_rows_by_id: Dict[str, Any],
    table_cells_by_id: Dict[str, Tuple[Any, Any]],
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Promote a cell's row to a repeating header row when ``cell_type`` is HEADER.

    DOCX has no per-cell "this is a header" boolean; the canonical way to
    mark header cells is to set ``<w:trPr><w:tblHeader/></w:trPr>`` on the
    *row*.  We therefore skip data cells entirely and, for header cells,
    promote the row.  Repeated promotions on the same row are idempotent.
    """

    if cell.cell_type != TableCellType.HEADER:
        return  # nothing to do — we only promote rows for header cells

    pair = table_cells_by_id.get(cell.id)
    if pair is None:
        skipped.append({"target_id": cell.id, "reason": "cell_not_found_in_source"})
        return
    row, _docx_cell = pair

    tr = row._tr
    trPr = tr.find(qn("w:trPr"))
    if trPr is None:
        # python-docx exposes a helper for this on _Tr; fall back to manual
        # element creation if the helper is not present in this version.
        get_or_add = getattr(tr, "get_or_add_trPr", None)
        if callable(get_or_add):
            trPr = get_or_add()
        else:  # pragma: no cover - very old python-docx
            from lxml import etree
            trPr = etree.SubElement(tr, qn("w:trPr"))
            tr.insert(0, trPr)

    if trPr.find(qn("w:tblHeader")) is None:
        from lxml import etree
        etree.SubElement(trPr, qn("w:tblHeader"))
        applied.append(
            {
                "kind": "table_header_row",
                "target_id": cell.id,
                "summary": "trPr/tblHeader added",
            }
        )
    else:
        # Already a header row — record as applied so callers can see the
        # mutation was honored even if the XML was untouched.
        applied.append(
            {
                "kind": "table_header_row",
                "target_id": cell.id,
                "summary": "trPr/tblHeader already present",
            }
        )
