"""Smoke: a deeply nested structure tree is bounded and DISCLOSED, not dropped.

tag_reader's walk had a `depth > 64` guard that never fired: `depth` was
dropped on the recursive call, so it was always 0. The walk ran until
Python's RecursionError (~1000 levels), which the outer `except` swallowed
into "return None" — and the parser then treated a TAGGED PDF as untagged,
discarding every /Alt the author had written, and said nothing.

Pinned here on a 1-page tagged PDF whose /Figure (with /Alt) sits under a
chain of /Div elements:
  * depth 5 and depth 60: the alt is read (guard not involved)
  * depth 1200: the walk is BOUNDED (no RecursionError, returns promptly),
    the result carries struct_tree_truncated, and the parser marks the root
    struct_tree_partial — while pdf_tagged stays True so the document is not
    misreported as untagged

Usage:
    python -m app.devtools.smoke_pdf_deep_struct_tree
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import time

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_deep_')}/s.db")

from pathlib import Path  # noqa: E402

from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import (  # noqa: E402
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

from app.parsers import parse_to_tree  # noqa: E402
from app.pdf.tag_reader import read_struct_info  # noqa: E402


def _tagged_pdf(depth: int) -> bytes:
    """One page: an H1 (MCID 0) and, under `depth` nested /Div, a /Figure with
    /Alt (MCID 1) that draws image XObject /Im1."""
    w = PdfWriter()
    page = w.add_blank_page(width=300, height=300)
    font = DictionaryObject()
    font.update({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    img = DecodedStreamObject()
    img.set_data(b"\xff" * (4 * 4 * 3))
    img.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(4), NameObject("/Height"): NumberObject(4),
                NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8)})
    fonts = DictionaryObject(); fonts[NameObject("/F1")] = w._add_object(font)  # noqa: SLF001
    xo = DictionaryObject(); xo[NameObject("/Im1")] = w._add_object(img)  # noqa: SLF001
    res = DictionaryObject(); res[NameObject("/Font")] = fonts; res[NameObject("/XObject")] = xo
    page[NameObject("/Resources")] = res
    cs = DecodedStreamObject()
    cs.set_data(
        b"/H1 <</MCID 0>> BDC BT /F1 14 Tf 20 270 Td (Heading) Tj ET EMC\n"
        b"/Figure <</MCID 1>> BDC q 100 0 0 100 100 100 cm /Im1 Do Q EMC"
    )
    page[NameObject("/Contents")] = w._add_object(cs)  # noqa: SLF001

    def elem(s, parent_ref, kids=None, alt=None, mcid=None):
        e = DictionaryObject({NameObject("/Type"): NameObject("/StructElem"), NameObject("/S"): NameObject(s), NameObject("/P"): parent_ref})
        if alt is not None:
            e[NameObject("/Alt")] = TextStringObject(alt)
        if mcid is not None:
            e[NameObject("/Pg")] = page.indirect_reference
            e[NameObject("/K")] = NumberObject(mcid)
        ref = w._add_object(e)  # noqa: SLF001
        if kids is not None:
            e[NameObject("/K")] = ArrayObject(kids)
        return ref, e

    st = DictionaryObject({NameObject("/Type"): NameObject("/StructTreeRoot")})
    st_ref = w._add_object(st)  # noqa: SLF001
    doc_ref, doc = elem("/Document", st_ref)
    h1_ref, _ = elem("/H1", doc_ref, mcid=0)
    # Build the Div chain top-down so each /P points at its real parent.
    parent_ref = doc_ref
    chain = []
    for _ in range(depth):
        d_ref, d = elem("/Div", parent_ref)
        chain.append((d_ref, d))
        parent_ref = d_ref
    fig_ref, _ = elem("/Figure", parent_ref, alt="Bar chart of quarterly revenue", mcid=1)
    # wire /K downwards
    if chain:
        doc[NameObject("/K")] = ArrayObject([h1_ref, chain[0][0]])
        for i, (d_ref, d) in enumerate(chain):
            nxt = chain[i + 1][0] if i + 1 < len(chain) else fig_ref
            d[NameObject("/K")] = ArrayObject([nxt])
    else:
        doc[NameObject("/K")] = ArrayObject([h1_ref, fig_ref])
    st[NameObject("/K")] = ArrayObject([doc_ref])
    # ParentTree so MCIDs resolve
    nums = ArrayObject([NumberObject(0), ArrayObject([h1_ref, fig_ref])])
    st[NameObject("/ParentTree")] = w._add_object(DictionaryObject({NameObject("/Nums"): nums}))  # noqa: SLF001
    page[NameObject("/StructParents")] = NumberObject(0)
    w._root_object[NameObject("/StructTreeRoot")] = st_ref  # noqa: SLF001
    w._root_object[NameObject("/MarkInfo")] = DictionaryObject({NameObject("/Marked"): NameObject("true")})  # noqa: SLF001
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="508_deep_"))

    for depth in (5, 60):
        p = tmp / f"d{depth}.pdf"
        p.write_bytes(_tagged_pdf(depth))
        info = read_struct_info(PdfReader(str(p)))
        alts = list((info or {}).get("figure_alt_by_xobject", {}).values())
        check(f"depth {depth}: figure alt is read from the tree",
              alts == ["Bar chart of quarterly revenue"], str(info and {k: v for k, v in info.items() if k != 'headings'}))
        check(f"depth {depth}: not marked truncated", not (info or {}).get("struct_tree_truncated"))

    p = tmp / "d1200.pdf"
    p.write_bytes(_tagged_pdf(1200))
    t0 = time.monotonic()
    info = read_struct_info(PdfReader(str(p)))
    dt = time.monotonic() - t0
    check("depth 1200: read_struct_info returns (no RecursionError swallowed into None)", info is not None, str(info))
    check("depth 1200: returns promptly (<5s)", dt < 5.0, f"{dt:.2f}s")
    check("depth 1200: the result is MARKED truncated (the guard actually fired)",
          bool((info or {}).get("struct_tree_truncated")), str(info))
    check("depth 1200: max depth reached is reported and is > 64",
          int((info or {}).get("struct_tree_max_depth") or 0) > 64, str((info or {}).get("struct_tree_max_depth")))

    res = parse_to_tree(str(p))
    props = res.tree.root.metadata.properties or {}
    check("depth 1200: parser marks the root struct_tree_partial", props.get("struct_tree_partial") is True, str({k: props.get(k) for k in ('struct_tree_partial', 'pdf_tagged')}))
    check("depth 1200: pdf_tagged stays True (NOT misreported as untagged)", props.get("pdf_tagged") is True, str(props.get("pdf_tagged")))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
