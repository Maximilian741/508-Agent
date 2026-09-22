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
* **One walk names every element.**  The parser mints deterministic ids
  (``docx-h-1``, ``docx-img-2``, ...).  The writer runs the parser's OWN walk
  (:meth:`DOCXParser.parse_document`) over the copy it edits, with a register
  callback that hands back, for each id, the live object it names.  There is
  no second walk to keep in step — hand-written mirrors of the parser drifted
  (alt text landed on the next picture after an image in a heading) and never
  covered headers, footers, bullets or table-cell pictures at all.
* **Look is preserved.**  Giving a paragraph a Heading style must not change
  how it looks: size, weight, colour, font, alignment, borders and spacing
  the old style supplied are pinned onto the paragraph, and a heading style
  that auto-numbers is switched off for it (numId 0) so no number is added
  to the text.
* **Never ship what we cannot open.**  After saving, every XML part must
  parse and the package must load; otherwise the source bytes are restored
  and a ``failed_to_save`` reason tells the pipeline not to charge.
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

import copy
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
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph
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
from docx.shared import RGBColor

from app.parsers.docx_parser import (
    _NOTE_PARTS_KEY,
    DOCXParser,
    DocxStyleResolver,
    _derive_sdt_label,
    _docx_default_lang,
    core_title_and_language,
    has_core_properties,
    _docx_theme_colors,
    _paragraph_caption_text,
    _run_color_hex,
    strip_fake_list_prefix,
    visible_runs,
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

    doc = Document(str(output_path))

    # Step 2: run the PARSER'S OWN walk over the copy we are about to edit,
    # with a register callback. That yields (a) a reference tree with exactly
    # the ids the caller's tree carries and (b) for every id, the live object
    # in THIS document it names. There is no second, hand-maintained walk to
    # drift out of step: the old per-kind mirrors did drift (a picture inside
    # a heading shifted every later alt text onto the wrong picture; links in
    # bullets and all header/footer content had no mirror at all).
    registry = _Registry()
    try:
        reference_result = DOCXParser().parse_document(doc, str(output_path), register=registry)
        reference_tree = reference_result.tree
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to re-parse source for id alignment: %s", exc)
        reference_tree = None

    mutated_index = _index_tree(tree)
    reference_index = _index_tree(reference_tree) if reference_tree is not None else {}

    elements = registry.by_id
    paragraph_by_id = elements
    image_by_rid: Dict[str, Any] = {}  # no rId fallback: the registry names every instance
    image_by_id = elements
    table_rows_by_id = elements
    table_cells_by_id = elements
    tables_by_id = elements
    hyperlink_by_id = elements
    note_parts = registry.note_parts
    note_links = [k for k in elements if k.startswith("docx-fnlink-")]
    styles = DocxStyleResolver(doc)

    # Step 4: walk the mutated tree and apply each supported mutation.  Order
    # is intentional — document-level metadata first, then per-node updates.
    _apply_document_metadata(doc, tree.root, applied, skipped)
    _apply_form_field_labels(doc, tree.root, applied, skipped)

    for node in mutated_index.values():
        if isinstance(node, ImageNode):
            _apply_image(node, image_by_id, image_by_rid, applied, skipped,
                         reference=reference_index.get(node.id))
        elif isinstance(node, LinkNode):
            _apply_link(node, hyperlink_by_id, applied, skipped)
        elif isinstance(node, HeadingNode):
            _apply_heading(doc, styles, node, paragraph_by_id, applied, skipped,
                           reference=reference_index.get(node.id))
        elif isinstance(node, ParagraphNode) and (node.metadata.properties or {}).get("promote_to_heading_level"):
            # A paragraph that only LOOKED like a heading (big/bold/Title text)
            # which the PROMOTE_HEADING executor approved — give it a real
            # Heading style so it joins the navigation outline. Checked BEFORE
            # list-conversion: the parser makes these mutually exclusive (a
            # styled heading is never tagged as a fake list), but if both ever
            # co-occur, promotion must win — a heading is not a bullet.
            _apply_promote_heading(doc, styles, node, paragraph_by_id, applied, skipped)
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
                reference=reference_index.get(node.id),
            )
        elif isinstance(node, TableNode):
            # A header-less table may have had a SYNTHESIZED header row inserted
            # at the top of the tree by AddTableHeadersExecutor. That row has no
            # source element, so insert a real <w:tr> (tblHeader + bold cells)
            # into the source table — otherwise the "fix" never reaches the file.
            _apply_synthetic_table_header(node, tables_by_id, applied, skipped)
            # An AI-generated caption (GENERATE_TABLE_CAPTION) is materialized as
            # a Caption-styled <w:p> immediately above the <w:tbl>.
            _apply_table_caption(doc, node, tables_by_id, applied, skipped)

    # Contrast recolour: the FixContrastExecutor leaves a {old_hex: new_hex} map
    # on each approved node naming only the runs that failed AA. Recolour just
    # those runs (each to its own nearest AA-passing shade), so colours that
    # already pass are left untouched. Resolved via the same paragraph-id index
    # the heading/list fixes use.
    _contrast_theme_colors: Optional[Dict[str, str]] = None
    for node in mutated_index.values():
        color_map = (node.metadata.properties or {}).get("contrast_fix_colors")
        if not color_map:
            continue
        if _contrast_theme_colors is None:
            try:
                _contrast_theme_colors = _docx_theme_colors(str(source_path))
            except Exception:  # pragma: no cover - defensive
                _contrast_theme_colors = {}
        _apply_contrast(node, paragraph_by_id, color_map, _contrast_theme_colors, applied, skipped)

    # Footnote/endnote rewrites happened on trees parsed from the note
    # parts' blobs — write them back so the changes reach the file. (A note
    # part python-docx loaded as XML serializes its live element on save.)
    if note_links and any(
        str(a.get("target_id", "")).startswith("docx-fnlink") for a in applied
    ):
        for note_part, note_root in note_parts:
            if getattr(note_part, "element", None) is note_root:
                continue
            try:
                note_part._blob = lxml_etree.tostring(  # noqa: SLF001
                    note_root, xml_declaration=True, encoding="UTF-8", standalone=True
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("failed to re-serialize note part: %s", exc)

    # Step 5: persist.
    doc.save(str(output_path))

    # Step 6: never hand back a file we cannot open ourselves. Every XML part
    # must parse and the package must load again; otherwise restore the
    # original bytes and report a failed save — the pipeline then refuses the
    # job and charges nothing, instead of delivering a file Word calls
    # "unreadable content".
    problem = _output_problem(output_path)
    if problem:
        logger.error("docx_writer: output failed validation (%s); restoring the source", problem)
        try:
            shutil.copyfile(source_path, output_path)
        except Exception:  # pragma: no cover - defensive
            pass
        return {
            "applied": [],
            "skipped": skipped + [{"target_id": "document", "reason": f"failed_to_save: {problem}"}],
        }

    return {"applied": applied, "skipped": skipped}


class _Registry:
    """Collects ``node_id -> live object`` from the parser's walk (see
    :meth:`DOCXParser.parse_document`)."""

    def __init__(self) -> None:
        self.by_id: Dict[str, Any] = {}
        self.note_parts: List[Tuple[Any, Any]] = []

    def __call__(self, node_id: str, obj: Any) -> None:
        if node_id == _NOTE_PARTS_KEY:
            self.note_parts.append(obj)
        else:
            self.by_id[node_id] = obj


def _output_problem(path: Path) -> Optional[str]:
    """Why the written .docx must not be delivered, or None when it is sound:
    the zip opens, every XML part parses, and python-docx loads it."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
            if bad:
                return f"corrupt zip member {bad}"
            for name in z.namelist():
                if name.endswith((".xml", ".rels")):
                    try:
                        lxml_etree.fromstring(z.read(name))
                    except Exception as exc:
                        return f"{name} is not well-formed XML ({exc})"
        Document(str(path))
    except Exception as exc:
        return f"output does not re-open ({exc})"
    return None


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
        # CT_DocDefaults is a sequence: rPrDefault BEFORE pPrDefault. Appending
        # after an existing pPrDefault is out of order — Word calls it damaged.
        rpr_default = OxmlElement("w:rPrDefault")
        doc_defaults.insert(0, rpr_default)
    rpr = rpr_default.find(qn("w:rPr"))
    if rpr is None:
        rpr = OxmlElement("w:rPr")
        rpr_default.append(rpr)
    lang = rpr.find(qn("w:lang"))
    if lang is None:
        lang = OxmlElement("w:lang")
        # w:lang has a fixed slot in CT_RPr (after sz/u/…, before
        # eastAsianLayout/specVanish/oMath) — not simply the end.
        _set_ordered_child(rpr, lang, _RPR_ORDER)
    existing = (lang.get(qn("w:val")) or "").strip()
    # Never DOWNGRADE: if the document already says "en-US" and we detected
    # "en", the existing tag is the same language and more specific — keep it.
    # Only overwrite when the existing value is empty or a different language.
    if existing and existing.lower().split("-")[0] == (language or "").lower().split("-")[0] and len(existing) >= len(language or ""):
        return
    lang.set(qn("w:val"), language)


def _apply_document_metadata(
    doc,
    root: DocumentNode,
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Sync ``DocumentNode`` metadata (language, title) onto core properties.

    Each is written only when it CHANGED from what the source states, and the
    core-properties part is only touched then: python-docx creates a missing
    part pre-filled with title "Word Document" / author "python-docx", which
    used to land in the customer's file (and an unchanged title was
    re-"applied" on every run, inflating the applied list)."""

    core_existed = has_core_properties(doc)
    source_title, source_dc_language = core_title_and_language(doc)
    core_state: Dict[str, Any] = {}

    def _core():
        if "core" not in core_state:
            core = doc.core_properties
            if not core_existed:
                # Freshly created by python-docx: drop its invented values.
                core.title = ""
                core.last_modified_by = ""
            core_state["core"] = core
        return core_state["core"]

    language = _xml_safe((root.metadata.language or "").strip()).strip()
    # Write the language only when it CHANGED from what the source already
    # declares — dc:language, else the styles.xml w:lang default, the parser's
    # own order. Re-asserting it copied a w:lang-derived "en-US" into
    # dc:language on documents where nobody approved a language fix, so an
    # approve-nothing download came back altered. SET_DOCUMENT_LANGUAGE only
    # runs when the source declares none, so a real fix always differs.
    try:
        source_language = source_dc_language or (_docx_default_lang(doc) or "")
    except Exception:  # pragma: no cover - defensive
        source_language = ""
    if language and language != source_language.strip():
        try:
            _core().language = language
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
    title = _xml_safe(str(title).strip()).strip()
    if title and title != source_title:
        try:
            _core().title = title
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
    image_by_id: Dict[str, Any],
    image_by_rid: Dict[str, Any],
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
    reference: Optional[ImageNode] = None,
) -> None:
    """Apply alt text / decorative-flag mutations to ONE image instance's docPr.

    Targets the node's own visual instance (``image_by_id``), never every
    docPr sharing its rId — Word dedupes identical bytes to one rId, so a
    pasted logo is many instances with one rId and each has its own alt.

    Also a no-op for nodes the pipeline did not change: the writer is handed
    every ImageNode in the tree, and re-writing an unchanged node's alt onto
    its docPr is harmless for its OWN docPr but was how sibling instances got
    clobbered under the old rId keying. With ``reference`` (the pre-mutation
    node) available we skip untouched nodes outright, so a human author's
    existing description is never so much as rewritten.
    """
    if reference is not None:
        ref_alt = (reference.alt_text or "").strip()
        ref_dec = bool(reference.is_decorative)
        cur_alt = (image.alt_text or "").strip()
        cur_dec = bool(image.is_decorative)
        if ref_alt == cur_alt and ref_dec == cur_dec:
            return  # untouched — leave the author's docPr exactly as it was

    doc_pr = image_by_id.get(image.id)
    if doc_pr is None or getattr(doc_pr, "tag", None) != f"{_DRAWING_NS}docPr":
        # No rId fallback: an rId names the image BYTES, not the picture —
        # it is shared by every copy of a pasted logo, and a header's rIds
        # are a different namespace from the body's. The registry names every
        # instance the parser saw; anything else is not ours to guess at.
        skipped.append({"target_id": image.id, "reason": "image_instance_not_found_in_source"})
        return
    rid = (image.metadata.properties or {}).get("image_rid") or "?"

    if image.is_decorative:
        # Decorative: descr must be empty AND hidden=1 on THIS instance.
        doc_pr.set("descr", "")
        if doc_pr.get("title"):
            doc_pr.set("title", "")
        if doc_pr.get("hidden") not in {"1", "true"}:
            doc_pr.set("hidden", "1")
        applied.append(
            {
                "kind": "image_decorative",
                "target_id": image.id,
                "summary": f"docPr[{image.id} rid={rid}] descr='' hidden=1",
            }
        )
        return

    alt_text = _xml_safe((image.alt_text or "").strip()).strip()
    if not alt_text:
        # Nothing to write — leave as-is and record the skip so callers can
        # see we deliberately did not blank an existing description.
        skipped.append({"target_id": image.id, "reason": "alt_text_empty_and_not_decorative"})
        return

    doc_pr.set("descr", alt_text)
    if doc_pr.get("hidden") in {"1", "true"}:
        doc_pr.set("hidden", "0")
    applied.append(
        {
            "kind": "image_alt_text",
            "target_id": image.id,
            "summary": f"docPr[{image.id} rid={rid}] descr={alt_text!r}",
        }
    )


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
    if getattr(hyperlink, "tag", None) not in (qn("w:hyperlink"), qn("w:fldSimple")):
        skipped.append({"target_id": link.id, "reason": "hyperlink_not_found_in_source"})
        return
    new_text = _xml_safe((link.content.text or "").strip()).strip() if link.content else ""
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


def _norm_hex(value: Any) -> str:
    return str(value or "").lstrip("#").upper()


def _apply_contrast(
    node: Any,
    paragraph_by_id: Dict[str, Any],
    color_map: Dict[str, str],
    theme_colors: Dict[str, str],
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Recolour a paragraph's failing runs to their AA-passing shades.

    ``color_map`` is ``{old_hex: new_hex}`` (no ``#``) for the runs the analyzer
    flagged. We resolve each run's effective colour exactly as the analyzer did
    (``_run_color_hex`` — explicit sRGB or resolved theme colour) and rewrite
    only the runs whose colour is in the map, writing an explicit ``w:color`` so
    it overrides any theme/inherited colour. Runs that already pass are left
    alone, so a mixed-colour paragraph keeps the colours that were fine.
    """
    paragraph = paragraph_by_id.get(node.id)
    if not isinstance(paragraph, Paragraph):
        skipped.append({"target_id": node.id, "reason": "contrast_paragraph_not_resolved"})
        return
    norm_map = {_norm_hex(k): _norm_hex(v) for k, v in color_map.items() if v}
    changed = 0
    for run in getattr(paragraph, "runs", []) or []:
        cur = _run_color_hex(run, theme_colors)
        if not cur:
            continue
        new = norm_map.get(_norm_hex(cur))
        if not new:
            continue
        try:
            run.font.color.rgb = RGBColor.from_string(new)
            changed += 1
        except Exception as exc:  # pragma: no cover - defensive
            skipped.append({"target_id": node.id, "reason": f"failed_to_set_run_color:{exc}"})
    if changed:
        applied.append({"action": "FIX_CONTRAST", "target_id": node.id})
    else:
        # Approved but nothing was rewritten (no run matched) — record it so the
        # caller never credits/charges a recolour that didn't reach the bytes.
        skipped.append({"target_id": node.id, "reason": "contrast_no_matching_run"})


# ---------------------------------------------------------------------------
# Heading styles: make sure they exist, apply them, keep the look
# ---------------------------------------------------------------------------

# Schema order of CT_PPr / CT_RPr children (ECMA-376 §17.3.1.26 / §17.3.2.28).
# Word rejects a file whose property children are out of order ("unreadable
# content"), so every element we add goes into its slot.
_PPR_ORDER = [
    "pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl", "numPr",
    "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens", "kinsoku", "wordWrap",
    "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN", "bidi", "adjustRightInd",
    "snapToGrid", "spacing", "ind", "contextualSpacing", "mirrorIndents", "suppressOverlap", "jc",
    "textDirection", "textAlignment", "textboxTightWrap", "outlineLvl", "divId", "cnfStyle", "rPr",
    "sectPr", "pPrChange",
]
_RPR_ORDER = [
    "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline",
    "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish", "webHidden", "color", "spacing",
    "w", "kern", "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText",
    "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath", "rPrChange",
]
# What a heading style may change that a reader SEES. Pagination hints
# (keepNext/keepLines) and the outline level are what we want from it. A
# template's "page break before" on Heading 1 would push a promoted line onto
# a new page, and a style-level bidi flips an Arabic/Hebrew paragraph's
# direction — both are kept as they were.
_PIN_PPR = ("pageBreakBefore", "pBdr", "shd", "bidi", "spacing", "ind", "contextualSpacing", "jc")
_PIN_RPR = ("rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline",
            "shadow", "emboss", "imprint", "vanish", "color", "spacing", "w", "kern", "position", "sz",
            "szCs", "highlight", "u", "shd", "vertAlign", "em")
_TOGGLES = {"b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline", "shadow",
            "emboss", "imprint", "vanish", "contextualSpacing", "pageBreakBefore", "bidi"}
_W_NS_URI = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _local(el) -> Optional[str]:
    try:
        q = lxml_etree.QName(el)
    except Exception:
        return None
    return q.localname if q.namespace == _W_NS_URI else None


def _set_ordered_child(parent, new_el, order: List[str]) -> None:
    """Put ``new_el`` into ``parent`` replacing any same-named child, else in
    its schema slot (before the first child that must follow it)."""
    name = _local(new_el)
    existing = parent.find(qn(f"w:{name}"))
    if existing is not None:
        parent.replace(existing, new_el)
        return
    later = set(order[order.index(name) + 1:]) if name in order else set()
    for child in parent:
        cname = _local(child)
        if cname is None or cname in later:
            child.addprevious(new_el)
            return
    parent.append(new_el)


def _sig(el):
    """Namespace-declaration-independent identity of a property element."""
    return (el.tag, tuple(sorted(el.attrib.items())), tuple(_sig(c) for c in el if isinstance(c.tag, str)))


def _same_el(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return _sig(a) == _sig(b)


def _toggle_el(tag: str, on: bool):
    el = OxmlElement(f"w:{tag}")
    if not on:
        el.set(qn("w:val"), "0")
    return el


def _neutral_el(tag: str, new_el, run_level: bool):
    """The element that cancels what a new style adds where the old look had
    nothing (Word's built-in defaults), or None when it cannot be cancelled."""
    if new_el is None:
        return None
    el = OxmlElement(f"w:{tag}")
    if run_level:
        if tag == "color":
            el.set(qn("w:val"), "auto")
        elif tag in ("sz", "szCs"):
            el.set(qn("w:val"), "20")  # 10pt: Word's size when nothing sets one
        elif tag == "u":
            el.set(qn("w:val"), "none")
        elif tag in ("kern", "position", "spacing"):
            el.set(qn("w:val"), "0")
        elif tag == "w":
            el.set(qn("w:val"), "100")
        elif tag == "highlight":
            el.set(qn("w:val"), "none")
        elif tag == "vertAlign":
            el.set(qn("w:val"), "baseline")
        elif tag == "em":
            el.set(qn("w:val"), "none")
        elif tag == "shd":
            el.set(qn("w:val"), "clear")
            el.set(qn("w:color"), "auto")
            el.set(qn("w:fill"), "auto")
        else:
            return None  # rFonts: no neutral value to write
        return el
    if tag == "spacing":
        el.set(qn("w:before"), "0")
        el.set(qn("w:after"), "0")
        el.set(qn("w:line"), "240")
        el.set(qn("w:lineRule"), "auto")
    elif tag == "ind":
        el.set(qn("w:left"), "0")
        el.set(qn("w:right"), "0")
        el.set(qn("w:firstLine"), "0")
    elif tag == "jc":
        el.set(qn("w:val"), "left")
    elif tag == "shd":
        el.set(qn("w:val"), "clear")
        el.set(qn("w:color"), "auto")
        el.set(qn("w:fill"), "auto")
    elif tag == "pBdr":
        for side in new_el:
            nm = _local(side)
            if nm:
                b = OxmlElement(f"w:{nm}")
                b.set(qn("w:val"), "nil")
                el.append(b)
    else:
        return None
    return el


def _ensure_heading_style(doc, styles: DocxStyleResolver, level: int) -> str:
    """Style id of the paragraph style named ``heading {level}``, created when
    the document does not define it.

    Word writes only the styles a document uses into styles.xml, so a real
    file that never used Heading 2 has no such style — and a ``w:pStyle``
    naming a missing style is silently ignored by Word (the paragraph stays
    body text). python-docx refused the assignment outright, the writer
    skipped it, and the promotion was still counted. The created style is
    the minimal built-in definition — outline level ``level-1`` (what makes
    it a heading to Word, screen readers and PDF export), keep-with-next —
    and adds no formatting of its own.
    """
    wanted = f"heading {level}"
    styles_el = doc.styles.element
    for st in styles_el.iterfind(qn("w:style")):
        if st.get(qn("w:type")) != "paragraph":
            continue
        name = st.find(qn("w:name"))
        if name is not None and (name.get(qn("w:val")) or "").strip().lower() == wanted:
            return st.get(qn("w:styleId"))
    taken = {st.get(qn("w:styleId")) for st in styles_el.iterfind(qn("w:style"))}
    style_id = f"Heading{level}"
    n = 1
    while style_id in taken:
        n += 1
        style_id = f"Heading{level}x{n}"
    style = OxmlElement("w:style")
    style.set(qn("w:type"), "paragraph")
    style.set(qn("w:styleId"), style_id)
    name = OxmlElement("w:name")
    name.set(qn("w:val"), wanted)
    style.append(name)
    base = styles.default_paragraph_style_id
    if base:
        based = OxmlElement("w:basedOn")
        based.set(qn("w:val"), base)
        style.append(based)
        nxt = OxmlElement("w:next")
        nxt.set(qn("w:val"), base)
        style.append(nxt)
    ui = OxmlElement("w:uiPriority")
    ui.set(qn("w:val"), "9")
    style.append(ui)
    if level > 1:
        style.append(OxmlElement("w:unhideWhenUsed"))
    style.append(OxmlElement("w:qFormat"))
    ppr = OxmlElement("w:pPr")
    ppr.append(OxmlElement("w:keepNext"))
    ppr.append(OxmlElement("w:keepLines"))
    lvl = OxmlElement("w:outlineLvl")
    lvl.set(qn("w:val"), str(level - 1))
    ppr.append(lvl)
    style.append(ppr)
    styles_el.append(style)
    styles.reload()
    return style_id


def _num_id_of(numpr) -> Optional[str]:
    if numpr is None:
        return None
    el = numpr.find(qn("w:numId"))
    return (el.get(qn("w:val")) or "").strip() if el is not None else None


def _style_linked_ilvl(doc, num_id: Optional[str], style_id: Optional[str]) -> Optional[str]:
    """The ``w:ilvl`` of the numbering level linked to ``style_id``
    (``<w:lvl><w:pStyle w:val=style_id/>``) in num ``num_id``'s abstract
    definition, or None. Read without creating a numbering part."""
    if not num_id or not style_id:
        return None
    try:
        numbering_el = doc.part.part_related_by(OPC_RT.NUMBERING).element
    except Exception:
        return None
    abstract_id = None
    for num in numbering_el.iterfind(qn("w:num")):
        if (num.get(qn("w:numId")) or "").strip() == num_id:
            ref = num.find(qn("w:abstractNumId"))
            abstract_id = ref.get(qn("w:val")) if ref is not None else None
            break
    if abstract_id is None:
        return None
    for absn in numbering_el.iterfind(qn("w:abstractNum")):
        if absn.get(qn("w:abstractNumId")) != abstract_id:
            continue
        for lvl in absn.iterfind(qn("w:lvl")):
            ps = lvl.find(qn("w:pStyle"))
            if ps is not None and ps.get(qn("w:val")) == style_id:
                return lvl.get(qn("w:ilvl"))
    return None


def _set_heading_style(doc, styles: DocxStyleResolver, paragraph, level: int) -> Tuple[str, int]:
    """Give ``paragraph`` the ``Heading {level}`` style WITHOUT changing how
    it looks. Returns ``(style_id, properties_pinned)``.

    A heading style carries its own look (Heading 1: 14pt bold blue Calibri
    Light, space before…). Switching a 26pt centred Title, or a black 14pt
    bold line, to it visibly restyled the customer's document. So every
    visible property the OLD style supplied and the new one would change is
    written directly onto the paragraph/runs (or cancelled where the old look
    had none), and a heading style that auto-numbers is switched off for this
    paragraph (numId 0) — otherwise "2.1 Data Sources" would read
    "1.1 2.1 Data Sources".
    """
    p_el = paragraph._p  # noqa: SLF001
    old_sid = styles.paragraph_style_id(p_el)
    new_sid = _ensure_heading_style(doc, styles, level)
    if old_sid == new_sid:
        return new_sid, 0

    runs = visible_runs(p_el)
    old_ppr = {t: styles.paragraph_element(p_el, old_sid, qn(f"w:{t}"), direct=False) for t in _PIN_PPR}
    old_num = styles.paragraph_element(p_el, old_sid, qn("w:numPr"), direct=False)
    old_rpr = [(r, {t: styles.run_element(r, old_sid, qn(f"w:{t}")) for t in _PIN_RPR}) for r in runs]
    # Copies: the old values may live in a style element we must not alias.
    old_ppr = {t: (copy.deepcopy(e) if e is not None else None) for t, e in old_ppr.items()}
    old_rpr = [(r, {t: (copy.deepcopy(e) if e is not None else None) for t, e in d.items()}) for r, d in old_rpr]

    pPr = p_el.get_or_add_pPr()
    pPr.style = new_sid
    # A direct outline level would override the style's (level 9 = body
    # text), leaving it invisible to navigation. The style decides now.
    direct_lvl = pPr.find(qn("w:outlineLvl"))
    if direct_lvl is not None:
        pPr.remove(direct_lvl)

    pinned = 0
    for tag, old_el in old_ppr.items():
        if pPr.find(qn(f"w:{tag}")) is not None:
            continue  # direct formatting already decides it, before and after
        new_el = styles.paragraph_element(p_el, new_sid, qn(f"w:{tag}"), direct=False)
        if tag in _TOGGLES:
            if _toggle_on_el(old_el) == _toggle_on_el(new_el):
                continue
            pin = _toggle_el(tag, _toggle_on_el(old_el))
        else:
            if _same_el(old_el, new_el):
                continue
            pin = copy.deepcopy(old_el) if old_el is not None else _neutral_el(tag, new_el, run_level=False)
        if pin is not None:
            _set_ordered_child(pPr, pin, _PPR_ORDER)
            pinned += 1

    new_num = styles.paragraph_element(p_el, new_sid, qn("w:numPr"), direct=False)
    if (
        pPr.find(qn("w:numPr")) is None
        and _num_id_of(new_num) not in (None, "", "0")
        and _num_id_of(old_num) in (None, "", "0")
    ):
        numpr = OxmlElement("w:numPr")
        nid = OxmlElement("w:numId")
        nid.set(qn("w:val"), "0")
        numpr.append(nid)
        _set_ordered_child(pPr, numpr, _PPR_ORDER)
        pinned += 1
    elif (
        pPr.find(qn("w:numPr")) is None
        and _num_id_of(old_num) not in (None, "", "0")
        and _num_id_of(new_num) != _num_id_of(old_num)
    ):
        # The OLD style numbered this line ("3." from a numbered "Section
        # Title" style). The heading style does not, so the number would
        # silently vanish from the page — a visible content change. Keep it,
        # at the level the old style drew it: an explicit ilvl, else the
        # numbering level linked to the old style, else level 0 (what Word
        # uses for a style's numPr without either).
        numpr = copy.deepcopy(old_num)
        if numpr.find(qn("w:ilvl")) is None:
            ilvl = OxmlElement("w:ilvl")
            ilvl.set(qn("w:val"), _style_linked_ilvl(doc, _num_id_of(old_num), old_sid) or "0")
            numpr.insert(0, ilvl)
        _set_ordered_child(pPr, numpr, _PPR_ORDER)
        pinned += 1

    for r, olds in old_rpr:
        rPr = r.find(qn("w:rPr"))
        for tag, old_el in olds.items():
            if rPr is not None and rPr.find(qn(f"w:{tag}")) is not None:
                continue
            new_el = styles.run_element(r, new_sid, qn(f"w:{tag}"))
            if tag in _TOGGLES:
                if _toggle_on_el(old_el) == _toggle_on_el(new_el):
                    continue
                pin = _toggle_el(tag, _toggle_on_el(old_el))
            else:
                if _same_el(old_el, new_el):
                    continue
                pin = copy.deepcopy(old_el) if old_el is not None else _neutral_el(tag, new_el, run_level=True)
            if pin is None:
                continue
            if rPr is None:
                rPr = OxmlElement("w:rPr")
                r.insert(0, rPr)
            _set_ordered_child(rPr, pin, _RPR_ORDER)
            pinned += 1
    return new_sid, pinned


def _toggle_on_el(el) -> bool:
    if el is None:
        return False
    val = (el.get(qn("w:val")) or "").strip().lower()
    return val not in ("0", "false", "off", "none")


def _apply_heading(
    doc,
    styles: DocxStyleResolver,
    heading: HeadingNode,
    paragraph_by_id: Dict[str, Any],
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
    reference: Optional[HeadingNode] = None,
) -> None:
    """Restyle a heading whose LEVEL changed (NORMALIZE_HEADING_LEVEL).

    Headings the pipeline did not change are left exactly as they are — the
    writer is handed every heading in the tree, and re-asserting "Heading N"
    on each one replaced authors' own heading styles ("Heading 2 Agency")
    with the built-in one on runs where nobody approved a heading fix.
    """
    if reference is not None and int(reference.level) == int(heading.level):
        return
    paragraph = paragraph_by_id.get(heading.id)
    if not isinstance(paragraph, Paragraph):
        skipped.append({"target_id": heading.id, "reason": "paragraph_not_found_for_heading"})
        return
    level = max(1, min(6, int(heading.level)))
    try:
        _sid, pinned = _set_heading_style(doc, styles, paragraph, level)
    except Exception as exc:  # pragma: no cover - defensive
        skipped.append({"target_id": heading.id, "reason": f"failed_to_set_style:{exc}"})
        return
    applied.append(
        {
            "kind": "heading_level",
            "target_id": heading.id,
            "summary": f"paragraph style = 'Heading {level}' (look kept: {pinned} properties pinned)",
        }
    )


def _apply_promote_heading(
    doc,
    styles: DocxStyleResolver,
    paragraph_node: ParagraphNode,
    paragraph_by_id: Dict[str, Any],
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Style a styled-but-fake-heading paragraph as a real ``Heading {level}``.

    The level was chosen by ``PromoteHeadingExecutor`` and stashed on the node
    as ``promote_to_heading_level``. The style is created if the document
    lacks it and the paragraph keeps its look; a re-parse of the output emits
    a real ``HeadingNode`` and the TEXT_STYLED_AS_HEADING flag clears. The
    applied entry carries ``action: PROMOTE_HEADING`` so the pipeline counts
    exactly the promotions that reached the file.
    """

    paragraph = paragraph_by_id.get(paragraph_node.id)
    if not isinstance(paragraph, Paragraph):
        skipped.append({"target_id": paragraph_node.id, "reason": "paragraph_not_found_for_promotion"})
        return
    props = paragraph_node.metadata.properties or {}
    try:
        level = max(1, min(6, int(props.get("promote_to_heading_level") or 1)))
    except (TypeError, ValueError):
        level = 1
    try:
        _sid, pinned = _set_heading_style(doc, styles, paragraph, level)
    except Exception as exc:  # pragma: no cover - defensive
        skipped.append({"target_id": paragraph_node.id, "reason": f"failed_to_set_style:{exc}"})
        return
    applied.append(
        {
            "kind": "promote_heading",
            "action": "PROMOTE_HEADING",
            "target_id": paragraph_node.id,
            "summary": f"paragraph style = 'Heading {level}' (promoted fake heading; look kept: {pinned} properties pinned)",
        }
    )


def _apply_table_cell(
    cell: TableCellNode,
    reference_index: Dict[str, Any],
    table_rows_by_id: Dict[str, Any],
    table_cells_by_id: Dict[str, Tuple[Any, Any]],
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
    reference: Optional[TableCellNode] = None,
) -> None:
    """Promote a cell's row to a repeating header row when ``cell_type`` is HEADER.

    DOCX has no per-cell "this is a header" boolean; the canonical way to
    mark header cells is to set ``<w:trPr><w:tblHeader/></w:trPr>`` on the
    *row*.  We therefore skip data cells entirely and, for header cells,
    promote the row.  Repeated promotions on the same row are idempotent.

    Only cells the pipeline CHANGED to headers are written: the parser types
    a small table's first row as a header by default, and re-asserting that
    on every save added tblHeader to layout tables nobody approved a fix for.
    """

    if cell.cell_type != TableCellType.HEADER:
        return  # nothing to do — we only promote rows for header cells
    if reference is not None and reference.cell_type == TableCellType.HEADER:
        return  # already a header in the source — nothing was approved here
    if (cell.metadata.properties or {}).get("synthesized") if cell.metadata else False:
        # A cell of a header row the executor SYNTHESIZED: it has no source
        # element by design, and _apply_synthetic_table_header inserts the
        # whole row. Reporting each such cell as "not found" was noise in the
        # skipped list the customer sees.
        return

    pair = table_cells_by_id.get(cell.id)
    if not (isinstance(pair, tuple) and len(pair) == 2):
        skipped.append({"target_id": cell.id, "reason": "cell_not_found_in_source"})
        return
    row, _docx_cell = pair

    tr = row._tr
    trPr = tr.get_or_add_trPr()
    th = trPr.find(qn("w:tblHeader"))
    if th is None:
        th = OxmlElement("w:tblHeader")
        # CT_TrPr: the row-property choices come first, then the tracked-
        # change markers (w:ins / w:del / w:trPrChange) — appending after
        # those is out of schema order and Word reports the file damaged.
        tail = next(
            (c for c in trPr if _local(c) in ("ins", "del", "trPrChange")), None
        )
        if tail is not None:
            tail.addprevious(th)
        else:
            trPr.append(th)
        summary = "trPr/tblHeader added"
    elif not _toggle_on_el(th):
        th.attrib.pop(qn("w:val"), None)  # explicitly switched off -> on
        summary = "trPr/tblHeader switched on"
    else:
        # The row is already a header row — set by an earlier cell of this
        # same row in this run, or by the source. Nothing changed, so nothing
        # is reported: one entry per promoted ROW, not one per cell (a
        # 3-column promotion used to read as "3 changes" in the applied list
        # the UI counts).
        return
    applied.append({"kind": "table_header_row", "target_id": cell.id, "summary": summary})



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
    if not isinstance(docx_table, DocxTable):
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


def _xml_safe(text: str) -> str:
    """Drop characters XML 1.0 forbids (NUL + C0 controls except tab/CR/LF,
    lone surrogates, U+FFFE / U+FFFF).

    lxml raises ``ValueError: All strings must be XML compatible`` when a w:t
    ``.text`` or an attribute contains them — an AI-provided caption, alt text,
    link text or title could carry a stray control byte, which aborted the
    whole write: the customer got a 422 and no file for one bad character.
    """
    if not text:
        return text
    return "".join(
        ch for ch in text
        if (ch in ("\t", "\n", "\r") or ord(ch) >= 0x20)
        and not (0xD800 <= ord(ch) <= 0xDFFF)
        and ch not in ("￾", "￿")
    )


def _ensure_caption_style(doc) -> None:
    """Guarantee the document defines a paragraph style with styleId "Caption".

    A ``w:pStyle`` referencing a styleId absent from ``styles.xml`` is IGNORED
    by Word (the paragraph falls back to Normal), so an inserted caption would
    not be a real, programmatically-associated caption. Word only adds the
    built-in Caption style the first time a user inserts a caption, so many
    authored docs lack it — we add a minimal definition on demand (mirrors
    :func:`_ensure_list_numbering`). Idempotent + memoized per Document.
    """
    if getattr(doc, "_a508_caption_style_ensured", False):
        return
    styles_el = doc.styles.element
    for st in styles_el.findall(qn("w:style")):
        if (st.get(qn("w:styleId")) or "").strip().lower() == "caption":
            doc._a508_caption_style_ensured = True
            return
    style = OxmlElement("w:style")
    style.set(qn("w:type"), "paragraph")
    style.set(qn("w:styleId"), "Caption")
    name = OxmlElement("w:name")
    name.set(qn("w:val"), "Caption")
    style.append(name)
    based = OxmlElement("w:basedOn")
    based.set(qn("w:val"), "Normal")
    style.append(based)
    nxt = OxmlElement("w:next")
    nxt.set(qn("w:val"), "Normal")
    style.append(nxt)
    style.append(OxmlElement("w:qFormat"))
    rpr = OxmlElement("w:rPr")
    rpr.append(OxmlElement("w:i"))  # Word's Caption default: italic, muted, smaller
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "44546A")
    rpr.append(color)
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), "18")
    rpr.append(sz)
    style.append(rpr)
    styles_el.append(style)
    doc._a508_caption_style_ensured = True


def _apply_table_caption(
    doc,
    table: TableNode,
    tables_by_id: Dict[str, Any],
    applied: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> None:
    """Insert a Caption-styled ``<w:p>`` above the table when one was generated.

    ``GenerateTableCaptionExecutor`` stores the caption on
    ``TableNode.metadata.properties['caption']``. We materialise it as a real
    Word caption — a paragraph styled "Caption" placed immediately before the
    ``<w:tbl>`` (where the Accessibility Checker and screen readers expect it),
    ensuring the Caption style is actually defined so the reference resolves.
    Re-parsing the output reads that paragraph back via
    :func:`_paragraph_caption_text`, so ``TABLE_CAPTION_MISSING`` clears.

    Idempotent and source-safe: skips (recording it, so it is never
    credited/charged) when the table can't be resolved or already has an
    adjacent Caption paragraph — so a caption that came from the SOURCE document
    is never duplicated.
    """
    caption = (table.metadata.properties or {}).get("caption")
    if not (isinstance(caption, str) and caption.strip()):
        return
    caption = _xml_safe(caption.strip())
    if not caption:
        return  # caption was nothing but control characters

    docx_table = tables_by_id.get(table.id)
    if not isinstance(docx_table, DocxTable):
        skipped.append({"target_id": table.id, "reason": "table_not_found_in_source"})
        return

    tbl = docx_table._tbl
    if _paragraph_caption_text(tbl.getprevious()) or _paragraph_caption_text(tbl.getnext()):
        # Already captioned in the source — never double it (and never credit it).
        skipped.append({"target_id": table.id, "reason": "caption_already_present"})
        return

    _ensure_caption_style(doc)
    p = OxmlElement("w:p")
    pPr = OxmlElement("w:pPr")
    pStyle = OxmlElement("w:pStyle")
    pStyle.set(qn("w:val"), "Caption")
    pPr.append(pStyle)
    p.append(pPr)
    run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = caption
    run.append(t)
    p.append(run)
    tbl.addprevious(p)
    applied.append({"action": "GENERATE_TABLE_CAPTION", "target_id": table.id})


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
        t.text = _xml_safe(text)
        run.append(t)
        para.append(run)
        tc.append(para)
        tr.append(tc)

    # The new row goes first: right after <w:tblGrid> (CT_Tbl is tblPr,
    # tblGrid, then the rows). Placing it before the first DIRECT <w:tr> put
    # it at the very END of a table whose rows all live in a repeating-section
    # content control (<w:sdt>), i.e. a "header" under the last data row.
    anchor = tbl.find(qn("w:tblGrid"))
    if anchor is None:
        anchor = tbl.find(qn("w:tblPr"))
    if anchor is not None:
        anchor.addnext(tr)
    else:
        tbl.insert(0, tr)


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
    mac = numbering_el.find(qn("w:numIdMacAtCleanup"))
    if first_num is not None:
        first_num.addprevious(abstract)
    elif mac is not None:
        mac.addprevious(abstract)
    else:
        numbering_el.append(abstract)

    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    ref = OxmlElement("w:abstractNumId")
    ref.set(qn("w:val"), str(abstract_id))
    num.append(ref)
    # CT_Numbering ends with an optional w:numIdMacAtCleanup (Word for Mac
    # writes it); a w:num after it is out of schema order.
    mac = numbering_el.find(qn("w:numIdMacAtCleanup"))
    if mac is not None:
        mac.addprevious(num)
    else:
        numbering_el.append(num)

    cache[kind] = num_id
    return num_id


def _apply_list_conversion(doc, node, paragraph_by_id, applied, skipped) -> None:
    """Persist a fake-list paragraph's conversion: add ``w:numPr`` and strip
    the literal typed marker ("- ", "1. ") from the run text."""

    paragraph = paragraph_by_id.get(node.id)
    if not isinstance(paragraph, Paragraph):
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
