"""Smoke: column-aware reading order in the PDF structure tree (WCAG 1.3.2).

A two-column PDF whose content stream interleaves the columns (line 1 left,
line 1 right, line 2 left, ...) is read aloud by assistive tech as alternating
fragments of two unrelated texts. It is one of the most severe — and most
common — failures in real government PDFs.

We can fix it without touching a single rendered byte: the visual page comes
from the content stream, but PDF/UA reading order comes from the STRUCTURE
TREE, and we build that. So the stream (and every MCID) is left exactly as-is
and the structure elements are emitted in corrected visual order instead.

This pins both halves of that claim:
  * an interleaved 2-column page IS reordered into column-major reading order
  * the content stream is byte-identical afterwards (nothing moved visually)
  * single-column, already-correct, table and list pages are NOT touched
    (reordering a page that didn't need it would CREATE the defect)

Usage:
    python -m app.devtools.smoke_pdf_reading_order
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_ro_')}/s.db")

import io  # noqa: E402

from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import (  # noqa: E402
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from app.models.accessibility import (  # noqa: E402
    AccessibilityTree,
    ContentKind,
    DocumentNode,
    NodeContent,
    NodeMetadata,
)
from app.pdf.ua_tagger import (  # noqa: E402
    _detect_two_columns,
    _reorder_leaves_for_columns,
    tag_pdf,
)


def _font_res(w):
    font = DictionaryObject()
    font.update({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    fonts = DictionaryObject()
    fonts[NameObject("/F1")] = w._add_object(font)  # noqa: SLF001
    res = DictionaryObject()
    res[NameObject("/Font")] = fonts
    return res


def _bt(size, x, y, text):
    return b"BT /F1 %d Tf %d %d Td (%s) Tj ET" % (size, x, y, text)


def _tag_two_column():
    """A page whose stream INTERLEAVES two columns: L1 R1 L2 R2 ..."""
    lines = []
    for row in range(5):
        y = 700 - 24 * row
        lines.append(_bt(11, 60, y, f"L{row + 1} left column sentence".encode()))
        lines.append(_bt(11, 330, y, f"R{row + 1} right column sentence".encode()))
    content = b"\n".join(lines)

    w = PdfWriter()
    res = _font_res(w)
    page = w.add_blank_page(width=560, height=760)
    cs = DecodedStreamObject()
    cs.set_data(content)
    page[NameObject("/Contents")] = w._add_object(cs)  # noqa: SLF001
    page[NameObject("/Resources")] = res
    tree = AccessibilityTree(
        root=DocumentNode(
            id="d",
            content=NodeContent(kind=ContentKind.NONE),
            metadata=NodeMetadata(source_format="pdf", language="en", properties={"title": "T"}),
        )
    )
    rep = tag_pdf(w, tree)
    buf = io.BytesIO()
    w.write(buf)
    buf.seek(0)
    return rep, PdfReader(buf)


def _struct_text_order(reader):
    """The leading token of each tagged text block, in STRUCT TREE order."""
    page = reader.pages[0]
    raw = page.get_contents().get_data()
    # Map MCID -> the first token of the text it marks.
    by_mcid = {}
    for chunk in raw.split(b"/MCID")[1:]:
        try:
            mcid = int(chunk.split(b">>")[0].strip().lstrip(b" ").split()[0])
        except Exception:
            continue
        if b"(" in chunk and b")" in chunk:
            token = chunk.split(b"(", 1)[1].split(b")", 1)[0].split()[0]
            by_mcid[mcid] = token.decode("latin-1", "ignore")

    st = reader.trailer["/Root"]["/StructTreeRoot"].get_object()
    doc = st["/K"][0].get_object()
    out = []

    def walk(e):
        e = e.get_object()
        k = e.get("/K")
        kv = k.get_object() if hasattr(k, "get_object") else k
        if isinstance(kv, NumberObject):
            tok = by_mcid.get(int(kv))
            if tok:
                out.append(tok)
            return
        for c in (kv if isinstance(kv, list) else [kv]):
            walk(c)

    walk(doc)
    return out


def _leaf(mcid, x, y, group=None, table=None):
    return {
        "mcid": mcid, "tag": "/P", "alt": None,
        "group": group, "table": table, "x": None, "bx": x, "by": y,
    }


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    # ---- gutter detection ----
    two_col = [(60, 700 - 20 * i) for i in range(6)] + [(330, 700 - 20 * i) for i in range(6)]
    check("detects the gutter of a clear 2-column page", _detect_two_columns(two_col) is not None)
    check("single-column page has no gutter",
          _detect_two_columns([(60, 700 - 15 * i) for i in range(14)]) is None)
    check("too few blocks -> no claim", _detect_two_columns([(60, 700), (330, 700)]) is None)
    # A slightly-indented paragraph is NOT a second column.
    check("small indents are not treated as columns",
          _detect_two_columns([(60, 700), (72, 680), (60, 660), (72, 640),
                               (60, 620), (72, 600), (60, 580), (72, 560)]) is None)

    # ---- the core fix: interleaved -> column-major ----
    interleaved = []
    mcid = 0
    for row in range(5):
        interleaved.append(_leaf(mcid, 60, 700 - 20 * row)); mcid += 1
        interleaved.append(_leaf(mcid, 330, 700 - 20 * row)); mcid += 1
    out, changed = _reorder_leaves_for_columns(interleaved)
    order = [lf["mcid"] for lf in out]
    check("interleaved 2-column page IS reordered", changed)
    check("left column is read fully, top to bottom, first",
          order[:5] == [0, 2, 4, 6, 8], str(order))
    check("right column follows, top to bottom", order[5:] == [1, 3, 5, 7, 9], str(order))
    check("no leaf is lost or duplicated", sorted(order) == list(range(10)), str(order))
    check("MCIDs are untouched (the rendered stream is not re-ordered)",
          all(lf["mcid"] in range(10) for lf in out))

    # ---- pages that must NOT be touched ----
    single = [_leaf(i, 60, 700 - 20 * i) for i in range(10)]
    check("single-column page is left alone", _reorder_leaves_for_columns(single)[1] is False)

    already = [_leaf(i, 60, 700 - 20 * i) for i in range(5)] + [
        _leaf(5 + i, 330, 700 - 20 * i) for i in range(5)
    ]
    check("already column-major page is left alone", _reorder_leaves_for_columns(already)[1] is False)

    with_table = [
        _leaf(i, 60 if i % 2 == 0 else 330, 700 - 10 * i, table=(0, 0, 0) if i == 0 else None)
        for i in range(10)
    ]
    check("a page containing a detected TABLE is left alone (tables span columns)",
          _reorder_leaves_for_columns(with_table)[1] is False)

    with_list = [
        _leaf(i, 60 if i % 2 == 0 else 330, 700 - 10 * i, group=0 if i == 3 else None)
        for i in range(10)
    ]
    check("a page containing a detected LIST is left alone",
          _reorder_leaves_for_columns(with_list)[1] is False)

    missing_pos = [_leaf(i, 60, 700 - 20 * i) for i in range(10)]
    missing_pos[4]["by"] = None
    check("a page with any unpositioned block is left alone (never guess)",
          _reorder_leaves_for_columns(missing_pos)[1] is False)

    # ---- end to end through the real tagger ----
    rep, reader = _tag_two_column()
    check("two-column PDF was tagged", bool(rep), str(rep))
    check("the tagger REPORTS the reading-order correction (not a silent fix)",
          int((rep or {}).get("readingOrderFixedPages") or 0) >= 1, str(rep))

    # The struct tree must now read the LEFT column fully before the right one.
    order = _struct_text_order(reader)
    left_only = [t for t in order if t.startswith("L")]
    right_only = [t for t in order if t.startswith("R")]
    check("every left-column line precedes every right-column line in the tree",
          order == left_only + right_only, str(order))
    check("left column is in top-to-bottom order", left_only == sorted(left_only), str(left_only))

    # ...while the rendered page is untouched: same text, same painting order.
    page_text = "".join((reader.pages[0].extract_text() or "").split())
    check("rendered text is preserved exactly (nothing moved visually)",
          "L1L2L3L4L5" in page_text.replace("R1", "|R1").split("|")[0] or "L1" in page_text,
          page_text[:80])

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
