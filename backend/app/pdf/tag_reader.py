"""Read an EXISTING PDF structure tree for analysis.

Untagged PDFs get deep treatment (the ua_tagger reconstructs a tree), but a
TAGGED-yet-badly-tagged PDF — extremely common in government archives — used
to get nearly a free pass: headings came from a text heuristic, tables were
never itemised, and a Figure whose /Alt lives on the StructElem (the
standards-correct place) was falsely flagged as missing alt because only the
XObject was consulted.

This module walks the existing tree and returns what the parser needs:

  - ``headings``: [{level, text, page}] from H1..H6 tags (RoleMap-resolved,
    one hop), text recovered from each heading's marked-content (MCID) spans;
  - ``figure_alt_by_xobject``: {xobject_name: alt} for Figures that DO carry
    /Alt, matched to the image XObject drawn inside their MCID span — fixing
    the tagged-figure false positive;
  - ``tables``: [{page, rows: [[cell_s, …], …]}] with per-cell tag names
    (TH/TD) so TABLE_MISSING_HEADERS can fire on header-less tagged tables.

Read-only and defensive: any structural surprise degrades to ``None`` /
partial results rather than failing the parse.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from pypdf import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

logger = logging.getLogger(__name__)

_HEADING_TAGS = {f"H{i}": i for i in range(1, 7)}


def _resolve(obj: Any) -> Any:
    if isinstance(obj, IndirectObject):
        try:
            return obj.get_object()
        except Exception:
            return None
    return obj


def _norm_s(s: Any, role_map: Dict[str, str]) -> str:
    name = str(s or "").lstrip("/")
    mapped = role_map.get(name)
    return mapped if mapped else name


def _page_index_for(pg: Any, page_ids: Dict[int, int]) -> Optional[int]:
    if isinstance(pg, IndirectObject):
        return page_ids.get(pg.idnum)
    return None


def _mcid_content(reader: PdfReader, page_index: int) -> Dict[int, Dict[str, Any]]:
    """``{mcid: {"text": str, "xobjects": set[str]}}`` for one page.

    Tracks BDC/BMC..EMC nesting; show-text and Do operators inside a marked-
    content span are attributed to its MCID.
    """

    from pypdf.generic import ContentStream  # local import keeps module light

    from app.pdf.text_decode import FontDecoder, FontState, operand_bytes, show_strings

    out: Dict[int, Dict[str, Any]] = {}
    try:
        page = reader.pages[page_index]
        ops = ContentStream(page.get_contents(), reader).operations
    except Exception:
        return out
    try:
        decoder: Optional[FontDecoder] = FontDecoder(page)
    except Exception:
        decoder = None
    font_state = FontState()

    def text_of(operands: Any, op: bytes) -> str:
        """Decoded text of one show op. Composite-font text goes through the
        font's ToUnicode map; text we cannot decode contributes NOTHING — a
        heading must never read back as the Python repr of glyph bytes
        ("b'\\x00:\\x00L...'"), which is what ``str(ByteStringObject)`` gave."""
        parts: List[str] = []
        for s in show_strings(operands, op):
            if decoder is not None and decoder.is_composite(font_state.font):
                t = decoder.decode(font_state.font, operand_bytes(s))
                if t:
                    parts.append(t)
                continue
            # Simple fonts: the FONT's encoding (WinAnsi 0x95 is a bullet),
            # as extract_text reads it — pypdf's own str() of the operand is
            # PDFDocEncoding, which turns bullets and curly quotes into other
            # characters.
            t = decoder.decode_unicode(font_state.font, operand_bytes(s)) if decoder is not None else None
            if t:
                parts.append(t)
            elif isinstance(s, str):
                parts.append(str(s))
            else:
                parts.append(operand_bytes(s).decode("latin-1", "ignore"))
        return "".join(parts)

    stack: List[Optional[int]] = []

    def current() -> Optional[int]:
        for v in reversed(stack):
            if v is not None:
                return v
        return None

    def bucket(mcid: int) -> Dict[str, Any]:
        if mcid not in out:
            # "order": where the MCID first paints in the content stream, so a
            # caller can rebuild an element's text in STREAM order.
            out[mcid] = {"text": "", "xobjects": set(), "order": len(out)}
        return out[mcid]

    for operands, op in ops:
        try:
            font_state.feed(operands, op)
            if op == b"BDC":
                mcid = None
                if len(operands) >= 2:
                    props = _resolve(operands[1])
                    if isinstance(props, DictionaryObject) and "/MCID" in props:
                        try:
                            mcid = int(props["/MCID"])
                        except (TypeError, ValueError):
                            mcid = None
                stack.append(mcid)
            elif op == b"BMC":
                stack.append(None)
            elif op == b"EMC":
                if stack:
                    stack.pop()
            elif op in (b"Tj", b"'", b'"', b"TJ"):
                m = current()
                if m is not None and operands:
                    bucket(m)["text"] += text_of(operands, op) + " "
            elif op == b"Do":
                m = current()
                if m is not None and operands:
                    bucket(m)["xobjects"].add(str(operands[0]).lstrip("/"))
        except Exception:
            continue
    return out


def read_struct_info(reader: PdfReader) -> Optional[Dict[str, Any]]:
    """Walk the existing structure tree; None when absent/unreadable."""

    try:
        root = _resolve(reader.trailer.get("/Root"))
        st = _resolve(root.get("/StructTreeRoot")) if root else None
        if not isinstance(st, DictionaryObject):
            return None
        rm = _resolve(st.get("/RoleMap"))
        role_map: Dict[str, str] = {}
        if isinstance(rm, DictionaryObject):
            for k, v in rm.items():
                role_map[str(k).lstrip("/")] = str(v).lstrip("/")
        page_ids: Dict[int, int] = {}
        for idx, page in enumerate(reader.pages):
            ref = page.indirect_reference
            if ref is not None:
                page_ids[ref.idnum] = idx
    except Exception:
        return None

    headings: List[Dict[str, Any]] = []
    figures: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    lists: List[Dict[str, Any]] = []

    def kid_mcids(elem: Any, page_ctx: Optional[int]) -> List[Tuple[int, int]]:
        """(page_index, mcid) pairs for an element's direct content kids."""
        got: List[Tuple[int, int]] = []
        pg = _page_index_for(elem.get("/Pg"), page_ids) if isinstance(elem, DictionaryObject) else None
        page = pg if pg is not None else page_ctx
        k = _resolve(elem.get("/K")) if isinstance(elem, DictionaryObject) else None
        kids = k if isinstance(k, (list, ArrayObject)) else ([k] if k is not None else [])
        for kid in kids:
            kr = _resolve(kid)
            if isinstance(kr, (int, float)) and page is not None:
                got.append((page, int(kr)))
            elif isinstance(kr, DictionaryObject) and "/MCID" in kr:
                kp = _page_index_for(kr.get("/Pg"), page_ids)
                p2 = kp if kp is not None else page
                if p2 is not None:
                    try:
                        got.append((p2, int(kr["/MCID"])))
                    except (TypeError, ValueError):
                        pass
        return got

    def first_page(elem: Any, depth: int = 0) -> Optional[int]:
        """Page of an element's first descendant that says which page it is on.

        Containers (/Table, /L) routinely carry no /Pg of their own — the
        cells and items do. Without this a table on page 2 was attributed to
        page 1 (``None`` coerced to 0 by the parser)."""
        if depth > 12:
            return None
        el = _resolve(elem)
        if not isinstance(el, DictionaryObject):
            return None
        pg = _page_index_for(el.get("/Pg"), page_ids)
        if pg is not None:
            return pg
        k = _resolve(el.get("/K"))
        kids = k if isinstance(k, (list, ArrayObject)) else ([k] if k is not None else [])
        for kid in kids[:8]:
            kr = _resolve(kid)
            if isinstance(kr, DictionaryObject):
                got = first_page(kr, depth + 1)
                if got is not None:
                    return got
        return None

    def all_mcids(elem: Any, page_ctx: Optional[int], depth: int = 0) -> List[Tuple[int, int]]:
        """Every (page, mcid) under ``elem`` (cells of a table, items of a
        list), bounded like :func:`walk`. Used only to LOCATE the element."""
        if depth > 16:
            return []
        el = _resolve(elem)
        if not isinstance(el, DictionaryObject):
            return []
        pg = _page_index_for(el.get("/Pg"), page_ids)
        page = pg if pg is not None else page_ctx
        got = kid_mcids(el, page)
        k = _resolve(el.get("/K"))
        kids = k if isinstance(k, (list, ArrayObject)) else ([k] if k is not None else [])
        for kid in kids[:2000]:
            kr = _resolve(kid)
            if isinstance(kr, DictionaryObject) and "/MCID" not in kr and str(kr.get("/Type") or "") != "/OBJR":
                got.extend(all_mcids(kr, page, depth + 1))
        return got

    truncated = {"hit": False, "max_depth": 0}

    def walk(elem: Any, page_ctx: Optional[int], depth: int = 0) -> None:
        # Bounds recursion on pathologically nested trees. `depth` used to be
        # dropped on the recursive call, so this guard never fired: the walk
        # ran until Python's RecursionError, which the outer `except` below
        # swallowed into "return None" — and the parser then treated a TAGGED
        # PDF as untagged, discarding every /Alt the author had written, with
        # no disclosure. Now the guard actually bounds it and the truncation
        # is reported so the caller can say so.
        if depth > truncated["max_depth"]:
            truncated["max_depth"] = depth
        if depth > 64:
            truncated["hit"] = True
            return
        el = _resolve(elem)
        if not isinstance(el, DictionaryObject):
            return
        s = _norm_s(el.get("/S"), role_map)
        pg = _page_index_for(el.get("/Pg"), page_ids)
        if pg is None and s in ("Table", "L"):
            pg = first_page(el)
        page = pg if pg is not None else page_ctx

        if s in _HEADING_TAGS:
            headings.append({"level": _HEADING_TAGS[s], "mcids": kid_mcids(el, page), "page": page})
        elif s == "Figure":
            alt = el.get("/Alt")
            figures.append(
                {"alt": (str(alt) if alt is not None else None), "mcids": kid_mcids(el, page), "page": page}
            )
        elif s == "L":
            # Record each structural kid's tag name so the parser can build a
            # ListNode — kids that aren't /LI mark a malformed tagged list.
            kid_names: List[str] = []
            k = _resolve(el.get("/K"))
            l_kids = k if isinstance(k, (list, ArrayObject)) else ([k] if k is not None else [])
            for lk in l_kids:
                lr = _resolve(lk)
                if isinstance(lr, DictionaryObject) and "/MCID" not in lr and str(lr.get("/Type") or "") != "/OBJR":
                    kid_names.append(_norm_s(lr.get("/S"), role_map))
            if kid_names:
                lists.append({"page": page, "kids": kid_names, "_mcids": all_mcids(el, page)})
        elif s == "Table":
            rows: List[List[str]] = []
            k = _resolve(el.get("/K"))
            row_kids = k if isinstance(k, (list, ArrayObject)) else ([k] if k is not None else [])
            for rk in row_kids:
                rr = _resolve(rk)
                if not isinstance(rr, DictionaryObject):
                    continue
                if _norm_s(rr.get("/S"), role_map) != "TR":
                    continue
                cells: List[str] = []
                ck = _resolve(rr.get("/K"))
                cell_kids = ck if isinstance(ck, (list, ArrayObject)) else ([ck] if ck is not None else [])
                for c in cell_kids:
                    cr = _resolve(c)
                    if isinstance(cr, DictionaryObject):
                        cs = _norm_s(cr.get("/S"), role_map)
                        if cs in ("TH", "TD"):
                            cells.append(cs)
                if cells:
                    rows.append(cells)
            if rows:
                tables.append({"page": page, "rows": rows, "_mcids": all_mcids(el, page)})

        # Recurse into structural kids (skip MCIDs/OBJRs).
        k = _resolve(el.get("/K"))
        kids = k if isinstance(k, (list, ArrayObject)) else ([k] if k is not None else [])
        for kid in kids:
            kr = _resolve(kid)
            if isinstance(kr, DictionaryObject) and "/MCID" not in kr and str(kr.get("/Type") or "") != "/OBJR":
                walk(kr, page, depth + 1)

    try:
        k = _resolve(st.get("/K"))
        top = k if isinstance(k, (list, ArrayObject)) else ([k] if k is not None else [])
        for elem in top:
            walk(elem, None)
    except Exception:
        # The tree EXISTS but could not be walked. Say so, rather than
        # returning None and letting the parser treat the document as
        # untagged. An empty-but-marked result keeps the caller honest.
        return {
            "headings": [],
            "figure_alt_by_xobject": {},
            "tables": [],
            "lists": [],
            "struct_tree_unreadable": True,
        }

    # Recover heading text + figure->xobject mapping from marked content.
    needed_pages: Set[int] = set()
    for h in headings:
        needed_pages.update(p for p, _m in h["mcids"])
    for f in figures:
        if f["alt"]:
            needed_pages.update(p for p, _m in f["mcids"])
    for container in tables + lists:
        if container.get("page") is not None and container.get("_mcids"):
            needed_pages.add(container["page"])
    mc_by_page: Dict[int, Dict[int, Dict[str, Any]]] = {
        p: _mcid_content(reader, p) for p in needed_pages
    }

    # A table's / list's text in CONTENT-STREAM order (its own page only).
    # The parser matches this as ONE contiguous run against the page's
    # positioned words to say where the element is — a whole table's text is
    # specific enough that a match is the element, and no match means no
    # location rather than a guessed one.
    for container in tables + lists:
        mcids = container.pop("_mcids", None) or []
        pg = container.get("page")
        if pg is None:
            continue
        content = mc_by_page.get(pg, {})
        spans = sorted(
            {m for p, m in mcids if p == pg and m in content},
            key=lambda m: content[m].get("order", 0),
        )
        text = " ".join((content[m].get("text") or "").strip() for m in spans).strip()
        if text:
            container["text"] = text

    headings_out: List[Dict[str, Any]] = []
    for h in headings:
        text = " ".join(
            (mc_by_page.get(p, {}).get(m, {}).get("text", "") or "").strip()
            for p, m in h["mcids"]
        ).strip()
        page = h["page"] if h["page"] is not None else (h["mcids"][0][0] if h["mcids"] else None)
        headings_out.append({"level": h["level"], "text": text, "page": page})

    figure_alt_by_xobject: Dict[str, str] = {}
    for f in figures:
        if not f["alt"]:
            continue
        for p, m in f["mcids"]:
            for xname in mc_by_page.get(p, {}).get(m, {}).get("xobjects", set()):
                figure_alt_by_xobject[xname] = f["alt"]

    out: Dict[str, Any] = {
        "headings": headings_out,
        "figure_alt_by_xobject": figure_alt_by_xobject,
        "tables": tables,
        "lists": lists,
    }
    if truncated["hit"]:
        # Some of the tree was beyond the depth bound and was NOT read. The
        # parts we did read are real; the caller must not conclude that
        # anything absent from them is absent from the document.
        out["struct_tree_truncated"] = True
        out["struct_tree_max_depth"] = truncated["max_depth"]
    return out
