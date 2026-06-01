"""Add a real (basic) PDF/UA structure to a pypdf ``PdfWriter``.

This turns an *untagged* PDF into a *tagged* one without rewriting the page
content bytes (so the original rendering is preserved). It applies the
document-level PDF/UA essentials that are unambiguously correct, plus a linked
structure tree:

Document-level (always):
* ``/Lang`` on the catalog                                  (WCAG 3.1.1)
* ``/ViewerPreferences << /DisplayDocTitle true >>``        (WCAG 2.4.2 / PDF-UA 7.1)
* an XMP ``/Metadata`` stream carrying ``dc:title`` + ``pdfuaid:part 1``

Structure (best-effort, never corrupts):
* each page's content is wrapped in a marked-content sequence (``/P … BDC/EMC``)
  by *appending* prefix/suffix content streams — the existing streams are kept
  byte-for-byte, so nothing about the visible page changes.
* a ``/StructTreeRoot`` (Document → one P per page) is built and linked to that
  marked content via a ``/ParentTree``; the catalog is marked
  ``/MarkInfo << /Marked true >>``.

HONEST SCOPE (v1): the structure is page/paragraph-granular — every page becomes
one tagged paragraph. Fine-grained per-element tagging (real H1/H2, individual
``/Figure`` elements with their own marked content, ``/Table`` → TR/TH/TD) and
images tagged as Figures in the tree are a deliberate future step; today images
still carry ``/Alt`` on their XObject (written by the PDF writer). This is a real
improvement over untagged output, and we never claim full PDF/UA conformance.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List, Optional

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    ContentStream,
    DecodedStreamObject,
    DictionaryObject,
    IndirectObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

from app.models.accessibility import AccessibilityTree, DocumentNode

logger = logging.getLogger(__name__)


def _resolve(obj: Any) -> Any:
    if isinstance(obj, IndirectObject):
        try:
            return obj.get_object()
        except Exception:
            return None
    return obj


def _xmp_packet(title: Optional[str], language: Optional[str]) -> bytes:
    """Build a minimal, valid XMP packet with dc:title + pdfuaid:part."""
    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

    title_block = ""
    if title:
        title_block = (
            "<dc:title><rdf:Alt>"
            f'<rdf:li xml:lang="x-default">{esc(str(title))}</rdf:li>'
            "</rdf:Alt></dc:title>"
        )
    lang_attr = f' xml:lang="{esc(str(language))}"' if language else ""
    packet = (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        f'<rdf:Description rdf:about=""{lang_attr} '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/">'
        f"{title_block}"
        "<pdfuaid:part>1</pdfuaid:part>"
        "</rdf:Description>"
        "</rdf:RDF></x:xmpmeta>"
        '<?xpacket end="w"?>'
    )
    return packet.encode("utf-8")


def _wrap_page_marked_content(writer: PdfWriter, page: Any, mcid: int = 0) -> bool:
    """Wrap a page's content in a single ``/P <</MCID n>> BDC … EMC`` sequence.

    Implemented by prepending/appending NEW content streams so the original
    content streams are kept untouched (fidelity-preserving). Returns True on
    success.
    """
    prefix = DecodedStreamObject()
    prefix.set_data(("/P <</MCID %d>> BDC\n" % mcid).encode("latin-1"))
    suffix = DecodedStreamObject()
    suffix.set_data(b"\nEMC\n")
    pref_ref = writer._add_object(prefix)  # noqa: SLF001 - pypdf object table
    suf_ref = writer._add_object(suffix)  # noqa: SLF001

    contents = page.raw_get("/Contents") if "/Contents" in page else None
    if contents is None:
        page[NameObject("/Contents")] = ArrayObject([pref_ref, suf_ref])
        return True

    resolved = _resolve(contents)
    if isinstance(resolved, ArrayObject):
        # Keep the existing (indirect) stream refs; just bracket them.
        new_arr = ArrayObject([pref_ref] + list(resolved) + [suf_ref])
    else:
        # Single content stream: `contents` is the indirect ref to it.
        new_arr = ArrayObject([pref_ref, contents, suf_ref])
    page[NameObject("/Contents")] = new_arr
    return True


def _page_content_bytes(page: Any) -> bytes:
    """Best-effort decoded content bytes for a page (single or array streams)."""
    try:
        c = page.raw_get("/Contents") if "/Contents" in page else None
    except Exception:
        return b""
    c = _resolve(c)
    if c is None:
        return b""
    try:
        if isinstance(c, ArrayObject):
            parts = []
            for item in c:
                obj = _resolve(item)
                if obj is not None:
                    parts.append(obj.get_data())
            return b"\n".join(parts)
        return c.get_data()
    except Exception:
        return b""


def _is_already_tagged(catalog: DictionaryObject) -> bool:
    """True if the PDF already carries a structure tree / is marked Tagged.

    We must NOT touch already-tagged PDFs: overwriting an existing
    ``/StructTreeRoot`` destroys real structure, and prepending another
    marked-content sequence collides MCIDs. For those we apply only safe,
    additive document metadata and leave the structure alone.
    """
    try:
        if _resolve(catalog.get("/StructTreeRoot")) is not None:
            return True
        mi = _resolve(catalog.get("/MarkInfo"))
        if isinstance(mi, DictionaryObject) and bool(_resolve(mi.get("/Marked"))):
            return True
    except Exception:
        return True  # if unsure, treat as tagged (never risk corruption)
    return False


def _is_taggable_page(pdf, page: Any) -> bool:
    """A page we can safely wrap: has content and no existing marked content.

    The marked-content check is done at the OPERATOR level (via the tokenized
    content stream), not a raw-bytes substring — so a page whose *visible text*
    contains an acronym like "BDC" or "BMC" is not wrongly excluded.
    """
    if "/Contents" not in page:
        return False
    try:
        contents = page.get_contents()
        if contents is None:
            return False
        ops = ContentStream(contents, pdf).operations
    except Exception:
        return False
    if not ops:
        return False
    # Already tagged / has marked content or artifacts → leave it alone.
    if any(op in (b"BDC", b"BMC", b"EMC") for _, op in ops):
        return False
    # Require some actual text/drawing content to wrap.
    return any(op in (b"Tj", b"TJ", b"'", b'"', b"Do") for _, op in ops)


def _build_alt_by_xobject(tree: AccessibilityTree) -> Dict[str, str]:
    """Map image XObject name -> alt text, for tagging Figures in the tree."""
    out: Dict[str, str] = {}
    try:
        from app.models.accessibility import ImageNode, iter_reading_order

        for node in iter_reading_order(tree.root):
            if isinstance(node, ImageNode) and not node.is_decorative and node.alt_text:
                xname = (node.metadata.properties or {}).get("xobject")
                if isinstance(xname, str) and xname:
                    out[xname.lstrip("/")] = str(node.alt_text)
    except Exception:  # pragma: no cover - defensive
        pass
    return out


def _count_text_ops(ops) -> int:
    return sum(1 for _, opc in ops if opc in (b"Tj", b"TJ", b"'", b'"'))


def _block_font_size(block_ops) -> float:
    """Largest ``Tf`` font size used inside a BT..ET block (0 if none)."""
    size = 0.0
    for operands, op in block_ops:
        if op == b"Tf" and len(operands) >= 2:
            try:
                size = max(size, float(operands[1]))
            except (TypeError, ValueError):
                pass
    return size


def _heading_levels(sizes: List[float]) -> Dict[float, int]:
    """Map block font-size -> heading level (1..6); body-text sizes are absent.

    Body text is taken as the most common block size; sizes meaningfully larger
    than body are headings (largest -> H1). Returns {} when there is no clear
    body size (so a page with one size yields only /P, never spurious headings).
    """
    real = [round(s, 1) for s in sizes if s and s > 0]
    if len(real) < 2:
        return {}
    counts = Counter(real)
    top = counts.most_common(1)[0][1]
    # Body text = the most common size; on a tie (e.g. one title + one body line)
    # prefer the SMALLER size as body so the larger is recognised as a heading.
    body = min(s for s, c in counts.items() if c == top)
    heading_sizes = sorted({s for s in real if s > body * 1.15}, reverse=True)
    return {s: min(i + 1, 6) for i, s in enumerate(heading_sizes)}


def _tag_page_elements(pdf: PdfWriter, page, alt_by_xobject: Dict[str, str]):
    """Per-element marked content for one page.

    Segments the content into text blocks (BT..ET) and known-alt images (Do),
    tags each text block ``/H1``..``/H6`` (by relative font size) or ``/P``, and
    each image ``/Figure`` with ``/Alt``. Returns specs (mcid = list index) or
    ``None`` to signal the caller to fall back to safe page-level wrapping.
    """
    try:
        contents = page.get_contents()
        if contents is None:
            return None
        cs = ContentStream(contents, pdf)
        ops = cs.operations
    except Exception:
        return None
    if not ops:
        return None
    orig_text = _count_text_ops(ops)

    # Pass 1 — split into ordered segments, preserving non-block ("raw") ops.
    segments = []  # (kind, ops, meta);  kind in {"raw", "text", "figure"}
    raw = []
    block = None
    for operands, op in ops:
        if op == b"BT":
            if block is not None:
                return None  # nested BT (malformed text objects) — fall back
            if raw:
                segments.append(("raw", raw, None))
                raw = []
            block = [(operands, op)]
        elif op == b"ET" and block is not None:
            block.append((operands, op))
            segments.append(("text", block, _block_font_size(block)))
            block = None
        elif block is not None:
            block.append((operands, op))
        elif op == b"Do":
            xname = str(operands[0]).lstrip("/") if operands else ""
            alt = alt_by_xobject.get(xname)
            if alt:
                if raw:
                    segments.append(("raw", raw, None))
                    raw = []
                segments.append(("figure", [(operands, op)], alt))
            else:
                raw.append((operands, op))
        else:
            raw.append((operands, op))
    if block is not None:  # unterminated BT — don't risk it
        return None
    if raw:
        segments.append(("raw", raw, None))

    levels = _heading_levels([m for k, _, m in segments if k == "text" and m])

    # Pass 2 — emit marked content.
    new_ops = []
    specs = []
    mcid = 0
    for kind, seg_ops, meta in segments:
        if kind == "raw":
            new_ops.extend(seg_ops)
            continue
        if kind == "text":
            lvl = levels.get(round(meta, 1)) if meta else None
            tag = f"/H{lvl}" if lvl else "/P"
            alt = None
        else:  # figure
            tag = "/Figure"
            alt = meta
        new_ops.append(([NameObject(tag), DictionaryObject({NameObject("/MCID"): NumberObject(mcid)})], b"BDC"))
        new_ops.extend(seg_ops)
        new_ops.append(([], b"EMC"))
        specs.append({"s": tag, "alt": alt})
        mcid += 1

    if not specs:
        return None
    if _count_text_ops(new_ops) != orig_text:  # text must be preserved exactly
        return None
    # Every original op must survive exactly once; we add only 2 ops (BDC+EMC)
    # per tagged segment. Anything else means an operator was dropped/duplicated.
    if len(new_ops) != len(ops) + 2 * len(specs):
        return None

    cs.operations = new_ops
    try:
        new_data = cs.get_data()
    except Exception:
        return None
    ns = DecodedStreamObject()
    ns.set_data(new_data)
    page[NameObject("/Contents")] = pdf._add_object(ns)  # noqa: SLF001
    return specs


def tag_pdf(writer: PdfWriter, tree: AccessibilityTree) -> Dict[str, Any]:
    """Add PDF/UA document metadata + a basic structure tree to ``writer``.

    Never raises. Returns a report dict. Safe by construction:
    * already-tagged PDFs keep their structure (only additive metadata is set);
    * pages with existing marked content or no content are skipped;
    * the structure step is isolated so a failure still lands metadata and
      never corrupts the file.
    """
    report: Dict[str, Any] = {"applied": [], "structTree": False}
    applied: List[str] = report["applied"]

    title: Optional[str] = None
    language: Optional[str] = None
    if isinstance(tree.root, DocumentNode):
        props = tree.root.metadata.properties or {}
        title = props.get("title")
        language = tree.root.metadata.language

    try:
        catalog = writer._root_object  # noqa: SLF001 - pypdf exposes intentionally
    except Exception as exc:  # pragma: no cover - defensive
        report["error"] = f"no_catalog: {exc}"
        return report

    # ---- Document-level essentials (additive, safe even when tagged) ------
    try:
        if language:
            catalog[NameObject("/Lang")] = TextStringObject(str(language))
            applied.append("lang")
    except Exception as exc:
        logger.debug("set /Lang failed: %s", exc)

    # Only declare DisplayDocTitle when there is actually a title to display,
    # otherwise the viewer shows an empty title bar.
    if title:
        try:
            vp = _resolve(catalog.get("/ViewerPreferences"))
            if not isinstance(vp, DictionaryObject):
                vp = DictionaryObject()
                catalog[NameObject("/ViewerPreferences")] = vp
            vp[NameObject("/DisplayDocTitle")] = BooleanObject(True)
            applied.append("display_doc_title")
        except Exception as exc:
            logger.debug("set DisplayDocTitle failed: %s", exc)

    try:
        meta = DecodedStreamObject()
        meta.set_data(_xmp_packet(title, language))
        meta[NameObject("/Type")] = NameObject("/Metadata")
        meta[NameObject("/Subtype")] = NameObject("/XML")
        catalog[NameObject("/Metadata")] = writer._add_object(meta)  # noqa: SLF001
        applied.append("xmp_metadata")
    except Exception as exc:
        logger.debug("write XMP failed: %s", exc)

    # ---- Never modify an already-tagged document's structure --------------
    if _is_already_tagged(catalog):
        report["alreadyTagged"] = True
        return report

    # ---- Structure tree over taggable pages only --------------------------
    try:
        taggable = [p for p in writer.pages if _is_taggable_page(writer, p)]
        if not taggable:
            report["structSkipped"] = "no_taggable_pages"
            return report

        struct_root = DictionaryObject()
        struct_root_ref = writer._add_object(struct_root)  # noqa: SLF001
        doc_elem = DictionaryObject()
        doc_elem_ref = writer._add_object(doc_elem)  # noqa: SLF001

        alt_by_xobject = _build_alt_by_xobject(tree)
        elem_refs: List[IndirectObject] = []  # all struct elems, reading order
        nums = ArrayObject()
        figures = 0
        per_element_pages = 0
        for key, page in enumerate(taggable):
            specs = _tag_page_elements(writer, page, alt_by_xobject)
            if specs is None:
                # Safe fallback: page-level single /P (original bytes untouched).
                _wrap_page_marked_content(writer, page, mcid=0)
                specs = [{"s": "/P", "alt": None}]
            else:
                per_element_pages += 1

            page_refs: List[IndirectObject] = []
            for mcid, spec in enumerate(specs):
                elem = DictionaryObject(
                    {
                        NameObject("/Type"): NameObject("/StructElem"),
                        NameObject("/S"): NameObject(spec["s"]),
                        NameObject("/P"): doc_elem_ref,
                        NameObject("/Pg"): page.indirect_reference,
                        NameObject("/K"): NumberObject(mcid),
                    }
                )
                if spec.get("alt"):
                    elem[NameObject("/Alt")] = TextStringObject(str(spec["alt"]))
                    figures += 1
                ref = writer._add_object(elem)  # noqa: SLF001
                page_refs.append(ref)
                elem_refs.append(ref)

            page[NameObject("/StructParents")] = NumberObject(key)
            page[NameObject("/Tabs")] = NameObject("/S")
            nums.append(NumberObject(key))
            nums.append(ArrayObject(page_refs))

        doc_elem.update(
            {
                NameObject("/Type"): NameObject("/StructElem"),
                NameObject("/S"): NameObject("/Document"),
                NameObject("/P"): struct_root_ref,
                NameObject("/K"): ArrayObject(elem_refs),
            }
        )
        parent_tree = DictionaryObject({NameObject("/Nums"): nums})
        parent_tree_ref = writer._add_object(parent_tree)  # noqa: SLF001
        struct_root.update(
            {
                NameObject("/Type"): NameObject("/StructTreeRoot"),
                NameObject("/K"): ArrayObject([doc_elem_ref]),
                NameObject("/ParentTree"): parent_tree_ref,
                NameObject("/ParentTreeNextKey"): NumberObject(len(taggable)),
            }
        )
        catalog[NameObject("/StructTreeRoot")] = struct_root_ref
        # Only NOW mark the document Tagged — there is a real, linked tree.
        catalog[NameObject("/MarkInfo")] = DictionaryObject(
            {NameObject("/Marked"): BooleanObject(True)}
        )
        report["structTree"] = True
        report["pages"] = len(taggable)
        report["elements"] = len(elem_refs)
        report["figures"] = figures
        report["perElementPages"] = per_element_pages
        applied.append("struct_tree")
        applied.append("mark_info")
    except Exception as exc:
        logger.warning("PDF struct-tree tagging failed (essentials still applied): %s", exc)
        report["structError"] = str(exc)

    return report


__all__ = ["tag_pdf"]
