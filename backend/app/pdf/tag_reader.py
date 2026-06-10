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

    out: Dict[int, Dict[str, Any]] = {}
    try:
        page = reader.pages[page_index]
        ops = ContentStream(page.get_contents(), reader).operations
    except Exception:
        return out

    stack: List[Optional[int]] = []

    def current() -> Optional[int]:
        for v in reversed(stack):
            if v is not None:
                return v
        return None

    def bucket(mcid: int) -> Dict[str, Any]:
        if mcid not in out:
            out[mcid] = {"text": "", "xobjects": set()}
        return out[mcid]

    for operands, op in ops:
        try:
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
            elif op in (b"Tj", b"'", b'"'):
                m = current()
                if m is not None and operands:
                    bucket(m)["text"] += str(operands[0] if op != b'"' else operands[2]) + " "
            elif op == b"TJ":
                m = current()
                if m is not None and operands and isinstance(operands[0], (list, ArrayObject)):
                    bucket(m)["text"] += "".join(
                        str(x) for x in operands[0] if not isinstance(x, (int, float))
                    ) + " "
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

    def walk(elem: Any, page_ctx: Optional[int], depth: int = 0) -> None:
        if depth > 64:
            return
        el = _resolve(elem)
        if not isinstance(el, DictionaryObject):
            return
        s = _norm_s(el.get("/S"), role_map)
        pg = _page_index_for(el.get("/Pg"), page_ids)
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
                lists.append({"page": page, "kids": kid_names})
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
                tables.append({"page": page, "rows": rows})

        # Recurse into structural kids (skip MCIDs/OBJRs).
        k = _resolve(el.get("/K"))
        kids = k if isinstance(k, (list, ArrayObject)) else ([k] if k is not None else [])
        for kid in kids:
            kr = _resolve(kid)
            if isinstance(kr, DictionaryObject) and "/MCID" not in kr and str(kr.get("/Type") or "") != "/OBJR":
                walk(kr, page)

    try:
        k = _resolve(st.get("/K"))
        top = k if isinstance(k, (list, ArrayObject)) else ([k] if k is not None else [])
        for elem in top:
            walk(elem, None)
    except Exception:
        return None

    # Recover heading text + figure->xobject mapping from marked content.
    needed_pages: Set[int] = set()
    for h in headings:
        needed_pages.update(p for p, _m in h["mcids"])
    for f in figures:
        if f["alt"]:
            needed_pages.update(p for p, _m in f["mcids"])
    mc_by_page: Dict[int, Dict[int, Dict[str, Any]]] = {
        p: _mcid_content(reader, p) for p in needed_pages
    }

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

    return {
        "headings": headings_out,
        "figure_alt_by_xobject": figure_alt_by_xobject,
        "tables": tables,
        "lists": lists,
    }
