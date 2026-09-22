"""Smoke: merged-cell PDF tables (/ColSpan, /RowSpan) — PDF/UA 7.5.

Merged cells were the last big gap in PDF table tagging. Previously a bordered
grid with a spanning header emitted one /TD per grid position, inventing cells
the page does not have and misaligning every data cell from its header.

The fix reads spans from GROUND TRUTH rather than guessing: a merged cell is one
whose separating rule was never drawn. If no vertical line exists between column
c and c+1 across a row's band, that cell genuinely continues into the next
column, so /ColSpan is evidence off the page — not an inference.

Pinned here:
  * a header spanning two columns emits ONE /TH with /ColSpan 2
  * the position it absorbs emits NO phantom cell
  * a plain grid still emits plain cells with NO span attributes
  * a row-spanning stub cell emits /RowSpan
  * a rule drawn in SEGMENTS (per-cell stroking, very common) still counts as
    a rule — measuring the longest single segment read it as absent, and an
    absent rule means "merged", so ordinary tables grew phantom spans
  * an unmeasurable band fails CLOSED (no merge), because a merge is the
    answer that can swallow content
  * a full-width TITLE band above a table is not mistaken for its header row:
    it stays /TD without a /Scope, and the real header row beneath it keeps
    its /TH — previously the title took the header role and the genuine
    header was demoted to data

Usage:
    python -m app.devtools.smoke_pdf_merged_cells
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_mc_')}/s.db")

from app.pdf.ua_tagger import _cell_spans  # noqa: E402


def _grid(xs, ys, v_lines, h_lines):
    return {
        "xs": sorted(xs),
        "ys": sorted(ys, reverse=True),
        "v_lines": v_lines,
        "h_lines": h_lines,
        "x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys),
    }


def _v(x, y0, y1):
    """A drawn vertical rule: (x, y_low, x, y_high)."""
    return (x, y0, x, y1)


def _h(y, x0, x1):
    """A drawn horizontal rule: (x0, y, x1, y)."""
    return (x0, y, x1, y)


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    # A 2-row x 2-col grid. Columns at x=0,100,200; rows at y=200,100,0.
    xs = [0.0, 100.0, 200.0]
    ys = [200.0, 100.0, 0.0]
    all_h = [_h(200, 0, 200), _h(100, 0, 200), _h(0, 0, 200)]

    # --- fully ruled grid: every cell is 1x1 -------------------------------
    full_v = [_v(0, 0, 200), _v(100, 0, 200), _v(200, 0, 200)]
    g_full = _grid(xs, ys, full_v, all_h)
    for r in range(2):
        for c in range(2):
            check(f"plain grid: cell ({r},{c}) has no span",
                  _cell_spans(g_full, r, c, 2, 2) == (1, 1),
                  str(_cell_spans(g_full, r, c, 2, 2)))

    # --- header spanning both columns: the middle rule is missing in row 0 --
    # Vertical at x=100 only exists in the LOWER band (y 0..100).
    span_v = [_v(0, 0, 200), _v(100, 0, 100), _v(200, 0, 200)]
    g_span = _grid(xs, ys, span_v, all_h)
    check("merged header: row 0 col 0 reports ColSpan 2",
          _cell_spans(g_span, 0, 0, 2, 2) == (2, 1), str(_cell_spans(g_span, 0, 0, 2, 2)))
    check("merged header: the data row below is NOT merged",
          _cell_spans(g_span, 1, 0, 2, 2) == (1, 1), str(_cell_spans(g_span, 1, 0, 2, 2)))
    check("merged header: the second data cell is normal",
          _cell_spans(g_span, 1, 1, 2, 2) == (1, 1), str(_cell_spans(g_span, 1, 1, 2, 2)))

    # --- a rule drawn in PIECES is still a rule ----------------------------
    # Tables are routinely stroked cell by cell, so the divider between two
    # columns exists as N short segments rather than one long line. Measuring
    # only the longest segment reported the rule ABSENT — and an absent rule is
    # read as a merge, so an ordinary per-cell-ruled table grew phantom
    # /ColSpans and lost the cells they absorbed.
    pieced_v = [
        _v(0, 0, 200), _v(200, 0, 200),
        _v(100, 0, 95), _v(100, 105, 200),   # same divider, drawn in two goes
    ]
    g_pieced = _grid(xs, ys, pieced_v, all_h)
    check("a divider drawn in two segments is NOT read as a merge",
          _cell_spans(g_pieced, 0, 0, 2, 2) == (1, 1),
          str(_cell_spans(g_pieced, 0, 0, 2, 2)))
    pieced_h = [
        _h(200, 0, 200), _h(0, 0, 200),
        _h(100, 0, 95), _h(100, 105, 200),
    ]
    g_pieced_h = _grid(xs, ys, full_v, pieced_h)
    check("a row rule drawn in two segments is NOT read as a merge",
          _cell_spans(g_pieced_h, 0, 0, 2, 2) == (1, 1),
          str(_cell_spans(g_pieced_h, 0, 0, 2, 2)))

    # A genuinely absent divider must still be detected — the union test has to
    # stay capable of finding a real merge, not just refuse them all.
    check("a genuinely missing divider is still read as a merge",
          _cell_spans(g_full, 0, 0, 2, 2) == (1, 1)
          and _cell_spans(_grid(xs, ys, [_v(0, 0, 200), _v(200, 0, 200)], all_h),
                          0, 0, 2, 2) == (2, 1))

    # A degenerate (zero-height) band can't be measured. It must fail CLOSED —
    # "rule present, no merge" — because a merge is the answer that deletes
    # content, and an unmeasurable band is never evidence for one.
    check("an unmeasurable band never produces a merge",
          _cell_spans(_grid(xs, [200.0, 200.0, 0.0], [], all_h), 0, 0, 2, 2) == (1, 1))

    # --- stub column spanning both rows: the middle horizontal is missing ---
    # Horizontal at y=100 only exists on the RIGHT half (x 100..200).
    rowspan_h = [_h(200, 0, 200), _h(100, 100, 200), _h(0, 0, 200)]
    g_rowspan = _grid(xs, ys, full_v, rowspan_h)
    check("merged stub: row 0 col 0 reports RowSpan 2",
          _cell_spans(g_rowspan, 0, 0, 2, 2) == (1, 2), str(_cell_spans(g_rowspan, 0, 0, 2, 2)))
    check("merged stub: the right column is unaffected",
          _cell_spans(g_rowspan, 0, 1, 2, 2) == (1, 1), str(_cell_spans(g_rowspan, 0, 1, 2, 2)))

    # --- end to end: the struct tree carries the attributes ----------------
    import io

    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

    from app.models.accessibility import (
        AccessibilityTree,
        ContentKind,
        DocumentNode,
        NodeContent,
        NodeMetadata,
    )
    from app.pdf.ua_tagger import tag_pdf

    def _bt(size, x, y, text):
        return b"BT /F1 %d Tf %d %d Td (%s) Tj ET" % (size, x, y, text)

    # A bordered 3x2 table whose TOP row is one merged header cell:
    # the vertical divider is drawn only across the two data rows.
    content = b"\n".join([
        # outer box + row rules
        b"40 400 240 120 re S",
        b"40 440 m 280 440 l S",     # under the merged header
        b"40 480 m 280 480 l S",     # header bottom is the same line set
        # the column divider exists ONLY below the header
        b"160 400 m 160 440 l S",
        _bt(10, 60, 495, b"Quarterly results summary"),   # merged header
        _bt(10, 60, 450, b"North"), _bt(10, 180, 450, b"120"),
        _bt(10, 60, 410, b"South"), _bt(10, 180, 410, b"90"),
    ])
    w = PdfWriter()
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
    page = w.add_blank_page(width=360, height=560)
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
    reader = PdfReader(buf)
    check("merged-cell PDF still tags cleanly (no corruption)", bool(rep and rep.get("structTree")), str(rep))
    check("page text is preserved", "North" in (reader.pages[0].extract_text() or ""))

    # Any /ColSpan or /RowSpan present must be a positive integer > 1 — never a
    # zero/one attribute, which would be meaningless noise in the tree.
    raw = io.BytesIO()
    w.write(raw)
    blob = raw.getvalue()
    ok_spans = True
    for token in (b"/ColSpan", b"/RowSpan"):
        i = blob.find(token)
        while i != -1:
            tail = blob[i + len(token): i + len(token) + 8].strip()
            digits = b"".join(ch.to_bytes(1, "big") for ch in tail if 48 <= ch <= 57)
            if digits and int(digits) < 2:
                ok_spans = False
            i = blob.find(token, i + 1)
    check("no /ColSpan or /RowSpan is ever written as 0 or 1", ok_spans)

    # Reporting honesty: "tables tagged" must never be read as "all your tables
    # were handled", so declined candidates are counted SEPARATELY — and the
    # counter has to be real, not a placeholder that mirrors the tagged count.
    import inspect

    from app.pdf import ua_tagger as _uat

    src_text = inspect.getsource(_uat)
    check("the declined-tables counter is actually incremented somewhere",
          'stats["declined"] = stats.get("declined"' in src_text)
    check("the report exposes declined tables separately from tagged ones",
          "tablesDeclined" in src_text and "tablesDetected" not in src_text)
    check("a clean tagged table reports zero declined", rep.get("tablesDeclined", 0) == 0, str(rep))

    # tablesDeclined must not count the RULING around a table the text-geometry
    # detector already tagged. It did: those blocks are excluded from the grid
    # pass, so the grid looked empty, was "declined", and the report told the
    # user "N table-like grids too sparse to tag — check by hand" for the very
    # tables it had tagged, on every tabular document.
    ruled_data = b"\n".join([
        b"40 180 m 320 180 l S", b"40 220 m 320 220 l S", b"40 260 m 320 260 l S",
        b"40 300 m 320 300 l S", b"40 340 m 320 340 l S",
        b"40 180 m 40 340 l S", b"160 180 m 160 340 l S", b"240 180 m 240 340 l S", b"320 180 m 320 340 l S",
        _bt(10, 48, 315, b"Region"), _bt(10, 168, 315, b"Q1"), _bt(10, 248, 315, b"Q2"),
        _bt(10, 48, 275, b"North"), _bt(10, 168, 275, b"120"), _bt(10, 248, 275, b"140"),
        _bt(10, 48, 235, b"South"), _bt(10, 168, 235, b"90"), _bt(10, 248, 235, b"110"),
        _bt(10, 48, 195, b"East"), _bt(10, 168, 195, b"75"), _bt(10, 248, 195, b"95"),
    ])
    w4 = PdfWriter()
    f4 = DictionaryObject()
    f4.update({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    fs4 = DictionaryObject()
    fs4[NameObject("/F1")] = w4._add_object(f4)  # noqa: SLF001
    r4 = DictionaryObject()
    r4[NameObject("/Font")] = fs4
    p4 = w4.add_blank_page(width=400, height=400)
    c4 = DecodedStreamObject()
    c4.set_data(ruled_data)
    p4[NameObject("/Contents")] = w4._add_object(c4)  # noqa: SLF001
    p4[NameObject("/Resources")] = r4
    rep4 = tag_pdf(w4, tree)
    check("a RULED data table is tagged once and declined ZERO times (no double count)",
          rep4.get("tables") == 1 and rep4.get("tablesDeclined", 0) == 0,
          f"tables={rep4.get('tables')} declined={rep4.get('tablesDeclined')}")

    # The merged band here is a TITLE ("Quarterly results summary"), not a
    # column header. Typing it /TH with /Scope=/Column would claim a title
    # labels one column of the table.
    check("a full-width title band is never given /Scope=/Column",
          b"/Scope" not in blob)

    # --- title band ABOVE a real header row --------------------------------
    # The regression this locks: row 0 spanned the table, took /TH + /Scope for
    # itself, and pushed the genuine header row down to /TD — so the table came
    # out with a fabricated header and no real one.
    titled = b"\n".join([
        b"40 400 240 120 re S",
        b"40 440 m 280 440 l S",
        b"40 480 m 280 480 l S",
        b"160 400 m 160 480 l S",      # divider spans the header + data rows only
        _bt(10, 60, 495, b"Regional summary table"),      # full-width title band
        _bt(10, 60, 455, b"Region"), _bt(10, 180, 455, b"Total"),   # real header
        _bt(10, 60, 415, b"North"), _bt(10, 180, 415, b"120"),      # data
    ])
    w2 = PdfWriter()
    f2 = DictionaryObject()
    f2.update({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    fs2 = DictionaryObject()
    fs2[NameObject("/F1")] = w2._add_object(f2)  # noqa: SLF001
    r2 = DictionaryObject()
    r2[NameObject("/Font")] = fs2
    p2 = w2.add_blank_page(width=360, height=560)
    c2 = DecodedStreamObject()
    c2.set_data(titled)
    p2[NameObject("/Contents")] = w2._add_object(c2)  # noqa: SLF001
    p2[NameObject("/Resources")] = r2
    rep2 = tag_pdf(w2, tree)
    b2 = io.BytesIO()
    w2.write(b2)
    blob2 = b2.getvalue()
    check("title-band table still tags", bool(rep2 and rep2.get("structTree")), str(rep2))
    check("the title band is emitted as a spanning cell", b"/ColSpan" in blob2)

    # Walk the actual structure tree — byte-counting "/TH" would also hit the
    # matching BDC operator in the content stream and double every cell.
    b2.seek(0)
    rows = []   # per /TR: the list of (structure type, colspan) for its cells

    def _walk(node, out_rows):
        node = node.get_object()
        s = str(node.get("/S") or "")
        if s == "/TR":
            cells = []
            kids = node.get("/K")
            kids = kids.get_object() if hasattr(kids, "get_object") else kids
            for kid in (kids if isinstance(kids, list) else [kids]):
                kid = kid.get_object()
                attrs = kid.get("/A")
                attrs = attrs.get_object() if hasattr(attrs, "get_object") else (attrs or {})
                cells.append((str(kid.get("/S") or ""), str(attrs.get("/Scope") or "")))
            out_rows.append(cells)
            return
        kids = node.get("/K")
        kids = kids.get_object() if hasattr(kids, "get_object") else kids
        if kids is None or isinstance(kids, (int, NumberObject)):
            return
        for kid in (kids if isinstance(kids, list) else [kids]):
            if hasattr(kid, "get_object") and isinstance(kid.get_object(), DictionaryObject):
                _walk(kid, out_rows)

    st2 = PdfReader(b2).trailer["/Root"]["/StructTreeRoot"].get_object()
    _walk(st2["/K"][0], rows)
    check("the table emits three rows (title band, header, data)", len(rows) == 3, str(rows))
    check("the TITLE band is NOT a header cell",
          bool(rows) and all(s == "/TD" for s, _sc in rows[0]), str(rows[:1]))
    check("the REAL header row beneath it IS /TH with a column scope",
          len(rows) > 1 and len(rows[1]) == 2
          and all(s == "/TH" and sc == "/Column" for s, sc in rows[1]), str(rows[1:2]))
    check("the data row stays /TD",
          len(rows) > 2 and all(s == "/TD" for s, _sc in rows[2]), str(rows[2:3]))

    # ...and the relocated header needs EVIDENCE, not just position. The
    # labels-on-top convention is what licenses typing row 0 as a header; it
    # does not license promoting an arbitrary interior row. A title band over
    # rows of plain text must yield NO header rather than an invented one.
    prose_titled = b"\n".join([
        b"40 400 240 120 re S",
        b"40 440 m 280 440 l S",
        b"40 480 m 280 480 l S",
        b"160 400 m 160 480 l S",
        _bt(10, 60, 495, b"Team directory"),
        _bt(10, 60, 455, b"Alice Nguyen"), _bt(10, 180, 455, b"Operations"),
        _bt(10, 60, 415, b"Bob Carter"), _bt(10, 180, 415, b"Finance"),
    ])
    w3 = PdfWriter()
    f3 = DictionaryObject()
    f3.update({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    fs3 = DictionaryObject()
    fs3[NameObject("/F1")] = w3._add_object(f3)  # noqa: SLF001
    r3 = DictionaryObject()
    r3[NameObject("/Font")] = fs3
    p3 = w3.add_blank_page(width=360, height=560)
    c3 = DecodedStreamObject()
    c3.set_data(prose_titled)
    p3[NameObject("/Contents")] = w3._add_object(c3)  # noqa: SLF001
    p3[NameObject("/Resources")] = r3
    tag_pdf(w3, tree)
    b3 = io.BytesIO()
    w3.write(b3)
    rows3 = []
    b3.seek(0)
    st3 = PdfReader(b3).trailer["/Root"]["/StructTreeRoot"].get_object()
    _walk(st3["/K"][0], rows3)
    check("a title band over PLAIN TEXT rows invents no header",
          bool(rows3) and all(s == "/TD" for row in rows3 for s, _sc in row), str(rows3))


    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
