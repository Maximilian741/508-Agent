"""Smoke: the PDF tagger reconstructs real nested PDF/UA structure.

Beyond the flat /P-per-block tagging, the tagger now rebuilds, from a flat
content stream:
* LISTS  — runs of bullet/ordinal text blocks -> /L -> /LI -> /LBody
* TABLES — grids of positioned text blocks    -> /Table -> /TR -> /TH|/TD

...while preserving the tagger's safety invariants (text byte-identical, the
file always reopens, ambiguous content is left as plain /P rather than risk a
false list/table).

Usage:
    python -m app.devtools.smoke_pdf_structure
"""

from __future__ import annotations

import io
import os
import sys
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_pdfstruct_')}/s.db"

from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject  # noqa: E402

from app.models.accessibility import (  # noqa: E402
    AccessibilityTree,
    ContentKind,
    DocumentNode,
    NodeContent,
    NodeMetadata,
)
from app.pdf.ua_tagger import tag_pdf  # noqa: E402


def _font_res(w):
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    return DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})}
    )


def _tag(lines, width=460, height=560):
    w = PdfWriter()
    res = _font_res(w)
    page = w.add_blank_page(width=width, height=height)
    cs = DecodedStreamObject()
    cs.set_data(b"\n".join(lines))
    page[NameObject("/Contents")] = w._add_object(cs)
    page[NameObject("/Resources")] = res
    tree = AccessibilityTree(
        root=DocumentNode(
            id="d",
            content=NodeContent(kind=ContentKind.NONE),
            metadata=NodeMetadata(source_format="pdf", language="en", properties={"title": "T"}),
        )
    )
    report = tag_pdf(w, tree)
    buf = io.BytesIO()
    w.write(buf)
    buf.seek(0)
    return report, PdfReader(buf)


def _top(r):
    """Return the Document's children as a nested (S, [kids]) / 'S' structure."""
    st = r.trailer["/Root"]["/StructTreeRoot"].get_object()
    doc = st["/K"][0].get_object()

    def rep(e):
        e = e.get_object()
        k = e.get("/K")
        kv = k.get_object() if hasattr(k, "get_object") else k
        if isinstance(kv, NumberObject):
            return str(e.get("/S"))
        kids = kv if isinstance(kv, list) else [kv]
        return (str(e.get("/S")), [rep(c) for c in kids])

    k = doc.get("/K")
    kv = k.get_object() if hasattr(k, "get_object") else k
    return [rep(c) for c in (kv if isinstance(kv, list) else [kv])]


def _tags(struct):
    return [s if isinstance(s, str) else s[0] for s in struct]


def _bt(size, x, y, text):
    return b"BT /F1 %d Tf %d %d Td (%s) Tj ET" % (size, x, y, text)


def main() -> int:
    failures = 0

    def check(name, cond):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    # --- 1. Ordinal list ---
    rep, r = _tag([
        _bt(18, 40, 500, b"Heading"),
        _bt(12, 40, 470, b"1. First"),
        _bt(12, 40, 452, b"2. Second"),
        _bt(12, 40, 434, b"3. Third"),
        _bt(12, 40, 410, b"Closing paragraph."),
    ])
    top = _top(r)
    check("ordinal list -> /L with 3 /LI/LBody",
          any(isinstance(s, tuple) and s[0] == "/L" and len(s[1]) == 3 and
              all(li[0] == "/LI" and li[1] == ["/LBody"] for li in s[1]) for s in top))
    check("list page reopens + text preserved", "First" in (r.pages[0].extract_text() or ""))

    # --- 1b. Nested list (indented sub-items nest inside the parent /LI) ---
    _, r1b = _tag([
        _bt(12, 40, 480, b"1. top one"),
        _bt(12, 62, 462, b"a. sub one"),
        _bt(12, 62, 444, b"b. sub two"),
        _bt(12, 40, 426, b"2. top two"),
    ])
    topb = _top(r1b)
    lst = next((s for s in topb if isinstance(s, tuple) and s[0] == "/L"), None)

    def _has_nested_L(node):
        # node = ('/L', [ ('/LI', [kids...]), ... ])
        if not (isinstance(node, tuple) and node[0] == "/L"):
            return False
        for li in node[1]:
            if isinstance(li, tuple) and li[0] == "/LI":
                if any(isinstance(k, tuple) and k[0] == "/L" for k in li[1]):
                    return True
        return False

    check("nested list -> a sub-/L nests inside an /LI", lst is not None and _has_nested_L(lst))

    # --- 2. Bullet list (WinAnsi bullet byte 0x95) ---
    _, r2 = _tag([
        _bt(12, 40, 480, b"\x95 alpha"),
        _bt(12, 40, 462, b"\x95 beta"),
        _bt(12, 40, 444, b"\x95 gamma"),
    ])
    check("bullet list (0x95) -> /L",
          any(isinstance(s, tuple) and s[0] == "/L" and len(s[1]) == 3 for s in _top(r2)))

    # --- 3. A single list-looking line is NOT a list ---
    _, r3 = _tag([_bt(12, 40, 480, b"1. lonely"), _bt(12, 40, 460, b"ordinary text here")])
    check("single ordinal line -> no /L (stays flat)",
          all((s if isinstance(s, str) else s[0]) != "/L" for s in _top(r3)))

    # --- 4. 3x3 table ---
    tlines = [_bt(18, 40, 500, b"Report")]
    for ri, y in enumerate([460, 440, 420]):
        for ci, x in enumerate([40, 180, 320]):
            tlines.append(_bt(12, x, y, (b"H%d" % ci) if ri == 0 else (b"r%dc%d" % (ri, ci))))
    rep4, r4 = _tag(tlines)
    t = _top(r4)
    tbl = next((s for s in t if isinstance(s, tuple) and s[0] == "/Table"), None)
    check("3x3 grid -> /Table with 3 /TR", tbl is not None and len(tbl[1]) == 3)
    check("table first row is /TH, rest /TD",
          tbl is not None and tbl[1][0][1] == ["/TH", "/TH", "/TH"] and tbl[1][1][1] == ["/TD", "/TD", "/TD"])
    check("table page reopens + text preserved", "r1c1" in (r4.pages[0].extract_text() or ""))

    # --- 5. Mixed: heading + list + table + paragraph all present & ordered ---
    mixed = [
        _bt(18, 40, 520, b"Title"),
        _bt(12, 40, 495, b"1. one"), _bt(12, 40, 477, b"2. two"),
    ]
    for ri, y in enumerate([440, 420, 400]):
        for ci, x in enumerate([40, 200]):
            mixed.append(_bt(12, x, y, (b"C%d" % ci) if ri == 0 else (b"d%d%d" % (ri, ci))))
    mixed.append(_bt(12, 40, 360, b"Trailing note."))
    _, r5 = _tag(mixed)
    tg = _tags(_top(r5))
    check("mixed page has H1 + L + Table + P in order", tg == ["/H1", "/L", "/Table", "/P"])

    # --- 6. False-positive guard: two-column PROSE is NOT a table ---
    prose = b"This is a long line of running prose text that fills the column width"
    fp = []
    for y in [480, 458, 436]:
        fp.append(_bt(12, 40, y, prose))
        fp.append(_bt(12, 240, y, prose))
    _, r6 = _tag(fp)
    check("two-column long-prose layout -> NOT a /Table",
          all((s if isinstance(s, str) else s[0]) != "/Table" for s in _top(r6)))

    # --- 7. Ragged columns (unequal cells per row) -> NOT a table ---
    rag = [
        _bt(12, 40, 480, b"a"), _bt(12, 180, 480, b"b"), _bt(12, 320, 480, b"c"),
        _bt(12, 40, 460, b"d"), _bt(12, 180, 460, b"e"),  # only 2 cells
    ]
    _, r7 = _tag(rag)
    check("ragged grid -> NOT a /Table",
          all((s if isinstance(s, str) else s[0]) != "/Table" for s in _top(r7)))

    # --- 8. Form (label: / value) -> NOT a table (expert FP) ---
    form = []
    for y, lab in [(480, b"Name:"), (458, b"Date:"), (436, b"Email:"), (414, b"Phone:")]:
        form.append(_bt(12, 40, y, lab))
        form.append(_bt(12, 200, y, b"___________"))
    _, r8 = _tag(form)
    check("label/value form -> NOT a /Table",
          all((s if isinstance(s, str) else s[0]) != "/Table" for s in _top(r8)))

    # --- 9. Two-column SHORT prose -> NOT a table (expert FP; defeats >40 guard) ---
    cols = []
    for y in [480, 458, 436, 414]:
        cols.append(_bt(12, 40, y, b"the quick brown fox runs"))
        cols.append(_bt(12, 250, y, b"over the lazy sleeping dog"))
    _, r9 = _tag(cols)
    check("two-column short prose -> NOT a /Table",
          all((s if isinstance(s, str) else s[0]) != "/Table" for s in _top(r9)))

    # --- 10. Stacked tables (big vertical gap) -> TWO /Table, not one merged ---
    stacked = []
    for ri, y in enumerate([500, 482, 464]):  # table A
        for ci, x in enumerate([40, 180, 320]):
            stacked.append(_bt(12, x, y, (b"A%d" % ci) if ri == 0 else (b"a%d%d" % (ri, ci))))
    for ri, y in enumerate([320, 302, 284]):  # table B, ~140pt below
        for ci, x in enumerate([40, 180, 320]):
            stacked.append(_bt(12, x, y, (b"B%d" % ci) if ri == 0 else (b"b%d%d" % (ri, ci))))
    _, r10 = _tag(stacked)
    n_tables = sum(1 for s in _top(r10) if isinstance(s, tuple) and s[0] == "/Table")
    check("two stacked grids -> TWO separate /Table (not merged)", n_tables == 2)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
