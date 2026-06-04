"""Smoke: ruling-line / border-anchored table detection in the PDF tagger.

Bordered tables draw their cell structure with stroked rectangles or m/l/S
line sequences. Detecting these lines and reconstructing the grid catches what
pure text-geometry can't:

* TABLES WHOSE CELLS CONTAIN PROSE — the text heuristic intentionally rejects
  these (to avoid forms-as-tables FPs) but a real ruling grid is unambiguous.
* TABLES WITH ONLY 2 ROWS — borders raise confidence enough to relax row count.

Adds tags, never removes; existing text-geometry tables aren't re-tagged.

Usage:
    python -m app.devtools.smoke_pdf_ruling_tables
"""

from __future__ import annotations

import io
import os
import sys
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_pdfrule_')}/s.db"

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


def _tag(content_bytes, width=460, height=560):
    w = PdfWriter()
    res = _font_res(w)
    page = w.add_blank_page(width=width, height=height)
    cs = DecodedStreamObject()
    cs.set_data(content_bytes)
    page[NameObject("/Contents")] = w._add_object(cs)
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


def _top(r):
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


def _bt(size, x, y, text):
    return b"BT /F1 %d Tf %d %d Td (%s) Tj ET" % (size, x, y, text)


def _rect_border(x, y, w, h):
    """A stroked rectangle (re ... S) — emits 4 ruling lines."""
    return b"%d %d %d %d re S" % (x, y, w, h)


def _cell_grid(x0, y0, cell_w, cell_h, ncols, nrows):
    """Emit a grid of stroked rectangles (one per cell)."""
    out = []
    for r in range(nrows):
        for c in range(ncols):
            out.append(_rect_border(x0 + c * cell_w, y0 - (r + 1) * cell_h, cell_w, cell_h))
    return b"\n".join(out)


def main() -> int:
    failures = 0

    def check(name, cond):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    # --- 1. Bordered 2x3 table whose cells are PROSE-LIKE (text heuristic
    # would reject) — ruling lines catch it. ---
    grid_bytes = _cell_grid(40, 500, 120, 40, 3, 2)
    text = b"\n".join([
        _bt(10, 50, 470, b"Region of operation"),
        _bt(10, 170, 470, b"Annual quota for Q4"),
        _bt(10, 290, 470, b"Variance vs target"),
        _bt(10, 50, 430, b"Northwest sales region"),
        _bt(10, 170, 430, b"Forty two thousand units"),
        _bt(10, 290, 430, b"Slightly above forecast"),
    ])
    _, r1 = _tag(grid_bytes + b"\n" + text)
    t1 = next((s for s in _top(r1) if isinstance(s, tuple) and s[0] == "/Table"), None)
    check("bordered 2x3 prose-cell table -> /Table (text heuristic would skip)", t1 is not None)
    if t1:
        check("ruling table: 2 rows", len(t1[1]) == 2)
        check("ruling table: row 0 is /TH, row 1 is /TD",
              t1[1][0][1] == ["/TH", "/TH", "/TH"] and t1[1][1][1] == ["/TD", "/TD", "/TD"])

    # --- 2. A SINGLE bordered panel around one paragraph is NOT a table. ---
    panel = _rect_border(40, 400, 360, 80)
    panel_txt = _bt(11, 60, 460, b"This paragraph sits inside a bordered callout box.")
    _, r2 = _tag(panel + b"\n" + panel_txt)
    check("single bordered panel (1x1) -> NOT a /Table",
          all((s if isinstance(s, str) else s[0]) != "/Table" for s in _top(r2)))

    # --- 3. Three unrelated bordered cards on a page (no real grid alignment
    # at the cell-fill level) should NOT become one merged /Table. ---
    cards = b"\n".join([
        _rect_border(40, 460, 100, 60),
        _rect_border(170, 460, 100, 60),
        _rect_border(300, 460, 100, 60),
    ])
    card_txts = b"\n".join([
        _bt(10, 50, 480, b"Alpha"),
        _bt(10, 180, 480, b"Beta"),
        _bt(10, 310, 480, b"Gamma"),
    ])
    _, r3 = _tag(cards + b"\n" + card_txts)
    n_tables = sum(1 for s in _top(r3) if isinstance(s, tuple) and s[0] == "/Table")
    # Three side-by-side bordered cards = 1 row of 3 cells = nrows=1 -> rejected
    # by the nrows>=2 guard. Acceptable: stays as plain content, not a /Table.
    check("three bordered cards (single row) -> NOT a /Table", n_tables == 0)

    # --- 4. Text-geometry tables still work alongside ruling-line tables on
    # the same page (no double-tagging, no collision). ---
    # Text-geometry 3x3 (top of page) + bordered 2x2 prose grid (bottom).
    tg = b"\n".join([
        _bt(12, 40, 540, b"H0"), _bt(12, 180, 540, b"H1"), _bt(12, 320, 540, b"H2"),
        _bt(12, 40, 520, b"a0"), _bt(12, 180, 520, b"a1"), _bt(12, 320, 520, b"a2"),
        _bt(12, 40, 500, b"b0"), _bt(12, 180, 500, b"b1"), _bt(12, 320, 500, b"b2"),
    ])
    bordered = _cell_grid(40, 400, 180, 40, 2, 2) + b"\n" + b"\n".join([
        _bt(10, 50, 370, b"Long prose header for cell"),
        _bt(10, 230, 370, b"Another long prose header"),
        _bt(10, 50, 330, b"Multi-word value cell content"),
        _bt(10, 230, 330, b"Yet more prose-style cell"),
    ])
    _, r4 = _tag(tg + b"\n" + bordered, height=600)
    tags4 = [s if isinstance(s, str) else s[0] for s in _top(r4)]
    table_count = sum(1 for t in tags4 if t == "/Table")
    check("text-geom + ruling tables coexist (>=1 of each kind detected)", table_count >= 2)

    # --- 5. CORRUPTION safety: ruling tables don't break reopen or text. ---
    full_text = (r1.pages[0].extract_text() or "").replace("\n", " ")
    check("bordered-table page reopens + text preserved",
          "Northwest" in full_text and "Variance" in full_text)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
