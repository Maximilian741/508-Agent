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
from docx.opc.constants import CONTENT_TYPE as OPC_CT
from docx.opc.constants import RELATIONSHIP_TYPE as OPC_RT
from docx.opc.packuri import PackURI
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.parts.numbering import NumberingPart
from lxml import etree as lxml_etree

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
from app.parsers.docx_parser import (
    DOCXParser,
    _IdCounter,
    _derive_sdt_label,
    _heading_level_from_style,
    _iter_note_parts,
    _iter_text_box_paragraphs,
    _link_elements_in_paragraph,
    _note_paragraphs,
    strip_fake_list_prefix,
)

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
    tables_by_id = _index_tables_by_parser_id(doc)
    hyperlink_by_id = _index_hyperlinks_by_parser_id(doc)
    note_links, note_parts = _index_note_links(doc)
    hyperlink_by_id.update(note_links)

    # Step 4: walk the mutated tree and apply each supported mutation.  Order
    # is intentional — document-level metadata first, then per-node updates.
    _apply_document_metadata(doc, tree.root, applied, skipped)
    _apply_form_field_labels(doc, tree.root, applied, skipped)

    for node in mutated_index.values():
        if isinstance(node, ImageNode):
            _apply_image(node, image_by_rid, applied, skipped)
        elif isinstance(node, LinkNode):
            _apply_link(node, hyperlink_by_id, applied, skipped)
        elif isinstance(node, HeadingNode):
            _apply_heading(node, paragraph_by_id, applied, skipped)
        elif isinstance(node, ParagraphNode) and (node.metadata.properties or {}).get("promote_to_heading_level"):
            # A paragraph that only LOOKED like a heading (big/bold/Title text)
            # which the PROMOTE_HEADING executor approved — give it a real
            # Heading style so it joins the navigation outline. Checked BEFORE
            # list-conversion: the parser makes these mutually exclusive (a
            # styled heading is never tagged as a fake list), but if both ever
            # co-occur, promotion must win — a heading is not a bullet.
            _apply_promote_heading(node, paragraph_by_id, applied, skipped)
        elif isinstance(node, ParagraphNode) and (node.metadata.properties or {}).get("convert_to_list"):
            # A typed fake-list paragraph the FIX_LIST_STRUCTURE executor
            # approved for conversion — give it real Word list semantics.
            _apply_list_conversion(doc, node, paragraph_by_id, applied, skipped)
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
        elif isinstance(node, TableNode):
            # A header-less table may have had a SYNTHESIZED header row inserted
            # at the top of the tree by AddTableHeadersExecutor. That row has no
            # source element, so insert a real <w:tr> (tblHeader + bold cells)
            # into the source table — otherwise the "fix" never reaches the file.
            _apply_synthetic_table_header(node, tables_by_id, applied, skipped)

    # Footnote/endnote rewrites happened on trees parsed from the note
    # parts' blobs — write them back so the changes reach the file.
    if note_links and any(
        str(a.get("target_id", "")).startswith("docx-fnlink") for a in applied
    ):
        for note_part, note_root in note_parts:
            try:
                note_part._blob = lxml_etree.tostring(  # noqa: SLF001
                    note_root, xml_declaration=True, encoding="UTF-8", standalone=True
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("failed to re-serialize note part: %s", exc)

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
        # emitted no link nodes.  Use the parser's own link predicate (visible
        # text required; HYPERLINK fldSimple counts; bare anchors don't) so
        # the counter advances in lock-step.
        if text and not _link_elements_in_paragraph(paragraph._p):
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


def _index_tables_by_parser_id(doc) -> Dict[str, Any]:
    """Map ``docx-table-N`` ids to python-docx Table objects.

    Mirrors the parser's id allocation order EXACTLY (cells, then row, per row;
    then the table id) so a ``TableNode.id`` resolves to its source ``<w:tbl>``.
    """

    ids = _IdCounter()
    out: Dict[str, Any] = {}
    for table in doc.tables:
        for row in table.rows:
            for _ in row.cells:
                ids("docx-cell")
            ids("docx-row")
        out[ids("docx-table")] = table
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


def _apply_form_field_labels(
    doc,
    root,
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Write a ``w:alias`` (accessible name) on each unlabeled content control
    that has a confident nearby label.

    Walks ``w:sdt`` elements in the SAME order the parser counted them and
    re-uses the parser's ``_derive_sdt_label`` so the number labeled equals the
    ``form_fields_derivable`` count the executor claimed (honest scoring).
    Controls with no clear label are left untouched for manual review.
    """
    if not (root.metadata.properties or {}).get("apply_form_field_labels"):
        return
    try:
        body = doc.element.body
    except Exception as exc:  # pragma: no cover - defensive
        skipped.append({"target_id": root.id, "reason": f"no_body_for_form_fields:{exc}"})
        return

    labeled = 0
    for sdt in body.iter(qn("w:sdt")):
        try:
            sdtPr = sdt.find(qn("w:sdtPr"))
            alias = sdtPr.find(qn("w:alias")) if sdtPr is not None else None
            existing = alias.get(qn("w:val")) if alias is not None else None
            if existing and str(existing).strip():
                continue  # already has a real accessible name
            label = _derive_sdt_label(sdt)
            if not label:
                continue  # no confident label — leave for manual review
            if alias is not None:
                # An empty/whitespace alias element already exists — OVERWRITE its
                # value. Appending a second <w:alias> would be schema-invalid
                # (maxOccurs=1) and real consumers (LibreOffice) drop both.
                alias.set(qn("w:val"), label)
            else:
                if sdtPr is None:
                    sdtPr = OxmlElement("w:sdtPr")
                    sdt.insert(0, sdtPr)  # sdtPr is the first child of w:sdt
                alias_el = OxmlElement("w:alias")
                alias_el.set(qn("w:val"), label)
                # In CT_SdtPr, <w:alias> follows an optional <w:rPr>; place it right
                # after rPr if present, else first.
                rpr = sdtPr.find(qn("w:rPr"))
                if rpr is not None:
                    rpr.addnext(alias_el)
                else:
                    sdtPr.insert(0, alias_el)
            labeled += 1
            applied.append(
                {
                    "kind": "form_field_label",
                    "target_id": root.id,
                    "summary": f"content control w:alias = {label!r}",
                }
            )
        except Exception as exc:  # pragma: no cover - defensive
            skipped.append({"target_id": root.id, "reason": f"failed_to_label_form_field:{exc}"})
            continue

    if labeled == 0:
        skipped.append({"target_id": root.id, "reason": "no_derivable_form_field_labels"})


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
    """Map ``docx-link-N`` ids to their link element (``<w:hyperlink>`` or a
    HYPERLINK ``<w:fldSimple>``).

    Mirrors the parser walk exactly via the shared
    :func:`_link_elements_in_paragraph` helper: body paragraphs first
    (heading-styled paragraphs emit no links, matching the parser's early
    ``continue``), then table cells in grid order with merged cells deduped.
    """
    out: Dict[str, Any] = {}
    n = 0

    def take(p_element) -> None:
        nonlocal n
        for _kind, el in _link_elements_in_paragraph(p_element):
            n += 1
            out[f"docx-link-{n}"] = el

    for para in doc.paragraphs:
        style_name = (para.style.name or "") if para.style else ""
        if _heading_level_from_style(style_name):
            continue  # parser's heading branch short-circuits before links
        pPr = para._p.find(qn("w:pPr"))
        if pPr is not None and pPr.find(qn("w:numPr")) is not None:
            continue  # list paragraphs likewise never reach link emission
        take(para._p)

    for table in doc.tables:
        seen_tc: set = set()
        for row in table.rows:
            for cell in row.cells:
                tc_key = id(cell._tc)
                if tc_key in seen_tc:
                    continue
                seen_tc.add(tc_key)
                for cell_para in cell.paragraphs:
                    take(cell_para._p)

    # Text-box links mint their own id space (docx-tblink-N) via the same
    # shared walk the parser uses, so rewrites inside sidebars/callouts
    # genuinely persist.
    n_tb = 0
    for tb_p in _iter_text_box_paragraphs(doc.element.body):
        for _kind, el in _link_elements_in_paragraph(tb_p):
            n_tb += 1
            out[f"docx-tblink-{n_tb}"] = el
    return out


def _index_note_links(doc) -> Tuple[Dict[str, Any], List[Tuple[Any, Any]]]:
    """``(link_map, [(part, root)])`` for footnote/endnote links.

    Note parts load as plain blob Parts, so the writer parses each blob once,
    rewrites the returned elements in place, and (when anything changed)
    re-serializes the root back into ``part._blob`` before save — mirroring
    the parser's ``_iter_note_parts`` walk so ``docx-fnlink-N`` ids align.
    """
    link_map: Dict[str, Any] = {}
    parts: List[Tuple[Any, Any]] = []
    n = 0
    for part, root in _iter_note_parts(doc):
        parts.append((part, root))
        for p_el in _note_paragraphs(root):
            for _kind, el in _link_elements_in_paragraph(p_el):
                n += 1
                link_map[f"docx-fnlink-{n}"] = el
    return link_map, parts


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


def _apply_promote_heading(
    paragraph_node: ParagraphNode,
    paragraph_by_id: Dict[str, Any],
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Style a styled-but-fake-heading paragraph as a real ``Heading {level}``.

    The level was chosen by ``PromoteHeadingExecutor`` and stashed on the node
    as ``promote_to_heading_level``. We re-use the same ``paragraph.style``
    assignment ``_apply_heading`` uses for genuine headings, so a re-parse of
    the output emits a real ``HeadingNode`` and the TEXT_STYLED_AS_HEADING flag
    clears.
    """

    paragraph = paragraph_by_id.get(paragraph_node.id)
    if paragraph is None:
        skipped.append({"target_id": paragraph_node.id, "reason": "paragraph_not_found_for_promotion"})
        return
    props = paragraph_node.metadata.properties or {}
    try:
        level = max(1, min(6, int(props.get("promote_to_heading_level") or 1)))
    except (TypeError, ValueError):
        level = 1
    style_name = f"Heading {level}"
    try:
        paragraph.style = paragraph.part.document.styles[style_name]
    except KeyError:
        try:
            paragraph.style = style_name
        except Exception as exc:
            skipped.append({"target_id": paragraph_node.id, "reason": f"failed_to_set_style:{exc}"})
            return
    except Exception as exc:  # pragma: no cover - defensive
        skipped.append({"target_id": paragraph_node.id, "reason": f"failed_to_set_style:{exc}"})
        return
    applied.append(
        {
            "kind": "promote_heading",
            "target_id": paragraph_node.id,
            "summary": f"paragraph.style = {style_name!r} (promoted fake heading)",
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


def _apply_synthetic_table_header(
    table: TableNode,
    tables_by_id: Dict[str, Any],
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Insert a real header ``<w:tr>`` when the tree's first row is synthesized.

    ``AddTableHeadersExecutor`` inserts a placeholder header row (tagged
    ``metadata.properties['synthesized']``) at the top of a header-less table's
    tree when its first data row doesn't read like a header. That row has no
    counterpart in the source document, so without this it would silently never
    reach the output. We materialise it as a genuine header row: a new
    ``<w:tr>`` with ``<w:trPr><w:tblHeader/></w:trPr>`` and one bold cell per
    column.
    """

    rows = [c for c in table.children if isinstance(c, TableRowNode)]
    if not rows:
        return
    first = rows[0]
    props = (first.metadata.properties if first.metadata else None) or {}
    if not props.get("synthesized"):
        return  # the executor promoted an existing row instead — nothing to insert

    docx_table = tables_by_id.get(table.id)
    if docx_table is None:
        skipped.append({"target_id": table.id, "reason": "table_not_found_in_source"})
        return

    header_cells = [c for c in first.children if isinstance(c, TableCellNode)]
    texts = [
        ((c.content.text or "").strip() if c.content else "") or " " for c in header_cells
    ]
    if not texts:
        return

    _insert_docx_header_row(docx_table, texts)
    applied.append(
        {
            "kind": "table_header_row_inserted",
            "target_id": table.id,
            "summary": f"inserted {len(texts)}-column header row (tblHeader)",
        }
    )


def _insert_docx_header_row(docx_table, texts: List[str]) -> None:
    """Build and insert a header ``<w:tr>`` at the top of ``docx_table``."""

    tbl = docx_table._tbl
    tr = OxmlElement("w:tr")
    trPr = OxmlElement("w:trPr")
    trPr.append(OxmlElement("w:tblHeader"))  # repeat-as-header-row = header semantics
    tr.append(trPr)

    for text in texts:
        tc = OxmlElement("w:tc")
        para = OxmlElement("w:p")
        run = OxmlElement("w:r")
        rpr = OxmlElement("w:rPr")
        rpr.append(OxmlElement("w:b"))  # bold, the visual header convention
        run.append(rpr)
        t = OxmlElement("w:t")
        t.set(qn("xml:space"), "preserve")
        t.text = text
        run.append(t)
        para.append(run)
        tc.append(para)
        tr.append(tc)

    # Insert before the first existing row (after <w:tblPr>/<w:tblGrid>).
    first_tr = tbl.find(qn("w:tr"))
    if first_tr is not None:
        first_tr.addprevious(tr)
    else:
        tbl.append(tr)


# ---------------------------------------------------------------------------
# Typed fake-list -> real Word list (w:numPr + numbering.xml)
# ---------------------------------------------------------------------------

_NUMBERING_SKELETON = (
    '<w:numbering xmlns:w='
    '"http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'
).encode("utf-8")


def _ensure_list_numbering(doc, kind: str) -> int:
    """Return a ``w:numId`` for ``kind`` ("bullet"|"decimal"), creating the
    numbering part and/or definitions on first use.

    The numbering part is found via its package relationship; documents that
    never contained a list get a fresh ``/word/numbering.xml``. Definitions
    are appended with ids above the document's existing maximum so nothing
    collides. Memoized per Document object so a whole run shares one num.
    """

    cache = getattr(doc, "_a508_list_num_ids", None)
    if cache is None:
        cache = {}
        setattr(doc, "_a508_list_num_ids", cache)
    if kind in cache:
        return cache[kind]

    try:
        numbering_part = doc.part.part_related_by(OPC_RT.NUMBERING)
    except KeyError:
        numbering_part = NumberingPart.load(
            PackURI("/word/numbering.xml"),
            OPC_CT.WML_NUMBERING,
            _NUMBERING_SKELETON,
            doc.part.package,
        )
        doc.part.relate_to(numbering_part, OPC_RT.NUMBERING)

    numbering_el = numbering_part.element

    # Pick ids above anything already present.
    max_abstract = -1
    for el in numbering_el.findall(qn("w:abstractNum")):
        try:
            max_abstract = max(max_abstract, int(el.get(qn("w:abstractNumId"))))
        except (TypeError, ValueError):
            continue
    max_num = 0
    for el in numbering_el.findall(qn("w:num")):
        try:
            max_num = max(max_num, int(el.get(qn("w:numId"))))
        except (TypeError, ValueError):
            continue
    abstract_id = max_abstract + 1
    num_id = max_num + 1

    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    mlt = OxmlElement("w:multiLevelType")
    mlt.set(qn("w:val"), "singleLevel")
    abstract.append(mlt)
    lvl = OxmlElement("w:lvl")
    lvl.set(qn("w:ilvl"), "0")
    start = OxmlElement("w:start")
    start.set(qn("w:val"), "1")
    lvl.append(start)
    fmt = OxmlElement("w:numFmt")
    fmt.set(qn("w:val"), "bullet" if kind == "bullet" else "decimal")
    lvl.append(fmt)
    lvl_text = OxmlElement("w:lvlText")
    lvl_text.set(qn("w:val"), "" if kind == "bullet" else "%1.")
    lvl.append(lvl_text)
    jc = OxmlElement("w:lvlJc")
    jc.set(qn("w:val"), "left")
    lvl.append(jc)
    ppr = OxmlElement("w:pPr")
    ind = OxmlElement("w:ind")
    ind.set(qn("w:left"), "720")
    ind.set(qn("w:hanging"), "360")
    ppr.append(ind)
    lvl.append(ppr)
    if kind == "bullet":
        rpr = OxmlElement("w:rPr")
        fonts = OxmlElement("w:rFonts")
        fonts.set(qn("w:ascii"), "Symbol")
        fonts.set(qn("w:hAnsi"), "Symbol")
        fonts.set(qn("w:hint"), "default")
        rpr.append(fonts)
        lvl.append(rpr)
    abstract.append(lvl)

    # Schema order: all w:abstractNum come before any w:num.
    first_num = numbering_el.find(qn("w:num"))
    if first_num is not None:
        first_num.addprevious(abstract)
    else:
        numbering_el.append(abstract)

    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    ref = OxmlElement("w:abstractNumId")
    ref.set(qn("w:val"), str(abstract_id))
    num.append(ref)
    numbering_el.append(num)

    cache[kind] = num_id
    return num_id


def _apply_list_conversion(doc, node, paragraph_by_id, applied, skipped) -> None:
    """Persist a fake-list paragraph's conversion: add ``w:numPr`` and strip
    the literal typed marker ("- ", "1. ") from the run text."""

    paragraph = paragraph_by_id.get(node.id)
    if paragraph is None:
        skipped.append({
            "target_id": node.id,
            "reason": "paragraph not found in source for list conversion",
        })
        return

    p = paragraph._p
    pPr = p.get_or_add_pPr()
    if pPr.find(qn("w:numPr")) is not None:
        skipped.append({
            "target_id": node.id,
            "reason": "paragraph already carries numbering",
        })
        return

    kind = (node.metadata.properties or {}).get("convert_to_list") or "bullet"
    num_id = _ensure_list_numbering(doc, kind)

    # python-docx's CT_PPr puts numPr in its correct schema slot for us.
    numPr = pPr.get_or_add_numPr()
    ilvl = OxmlElement("w:ilvl")
    ilvl.set(qn("w:val"), "0")
    num_ref = OxmlElement("w:numId")
    num_ref.set(qn("w:val"), str(num_id))
    numPr.append(ilvl)
    numPr.append(num_ref)

    # Strip the typed marker from the first non-empty text run so the output
    # doesn't render "• - item".  If the marker straddles runs unusually we
    # leave the text alone — the list semantics still land.
    stripped = False
    for t in p.iter(qn("w:t")):
        if t.text and t.text.strip():
            new_text = strip_fake_list_prefix(t.text)
            if new_text != t.text:
                t.text = new_text
                t.set(qn("xml:space"), "preserve")
                stripped = True
            break

    applied.append({
        "kind": "list_conversion",
        "target_id": node.id,
        "summary": (
            f"converted typed paragraph to real {kind} list item "
            f"(numId={num_id}, marker {'stripped' if stripped else 'left in place'})"
        ),
    })
