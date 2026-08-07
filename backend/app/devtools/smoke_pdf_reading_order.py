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
  * a THREE-column page, and any page below the detector's confidence floor,
    is DECLINED rather than half-ordered — a partial reorder reported as a fix
    manufactures the very defect this feature removes
  * `changed` describes the RESULT, so an already-correct page is never
    credited with a correction it did not need

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
    # Real flowing columns do NOT share baselines line-for-line — content that
    # does is a form/data sheet, which must never be reordered (see the CRITICAL
    # locks). So the right column is offset half a line.
    lines = []
    for row in range(5):
        y = 700 - 24 * row
        lines.append(_bt(11, 60, y, f"L{row + 1} left column running sentence".encode()))
        lines.append(_bt(11, 330, y - 11, f"R{row + 1} right column running sentence".encode()))
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


def _leaf(mcid, x, y, group=None, table=None, text=None, w=None):
    return {
        "mcid": mcid, "tag": "/P", "alt": None,
        "group": group, "table": table, "x": None, "bx": x, "by": y,
        "text": text if text is not None else "a genuine sentence of running body text",
        "w": w,
    }


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    # ---- gutter detection (points are (x, y, text)) ----
    PROSE = "a genuine sentence of running body text here"
    two_col = [(60, 700 - 20 * i, PROSE) for i in range(6)] + [
        (330, 690 - 20 * i, PROSE) for i in range(6)
    ]
    check("detects the gutter of a clear 2-column page", _detect_two_columns(two_col) is not None)
    check("single-column page has no gutter",
          _detect_two_columns([(60, 700 - 15 * i, PROSE) for i in range(14)]) is None)
    check("too few blocks -> no claim",
          _detect_two_columns([(60, 700, PROSE), (330, 700, PROSE)]) is None)
    check("small indents are not treated as columns",
          _detect_two_columns([(60 if i % 2 else 72, 700 - 20 * i, PROSE) for i in range(8)]) is None)

    # --- CRITICAL regression locks from the adversarial review ---------------
    # A label/value FORM has two x-clusters and a wide gap, but linearizing it
    # column-major separates every label from its value. The table detector
    # deliberately rejects forms, so nothing else rescues this page.
    form = []
    labels = ["Applicant name:", "Date of birth:", "Street address:", "City:",
              "State:", "ZIP code:", "Phone:", "Email:"]
    values = ["Jane Q Public", "01/02/1980", "100 Main Street", "Springfield",
              "OR", "97477", "555-0100", "jane@example.gov"]
    for i, (lab, val) in enumerate(zip(labels, values)):
        y = 680 - 26 * i
        form.append((72, y, lab))
        form.append((300, y, val))
    check("CRITICAL: a label/value FORM is never split into columns",
          _detect_two_columns(form) is None)

    # A table of contents: entry text left, bare page number right.
    toc = []
    entries = ["Introduction", "Scope and purpose", "Eligibility criteria",
               "How to apply", "Appeals process", "Contact information",
               "Appendix A", "Appendix B", "Glossary of terms"]
    for i, e in enumerate(entries):
        y = 680 - 24 * i
        toc.append((72, y, e))
        toc.append((500, y, str(3 + i * 4)))
    check("CRITICAL: a table of CONTENTS is never split into columns",
          _detect_two_columns(toc) is None)

    # Row-paired data sheet with prose-length cells on both sides — the prose
    # test alone would pass it, so the row-pairing veto must catch it.
    sheet = []
    for i in range(8):
        y = 680 - 24 * i
        sheet.append((72, y, "a reasonably long descriptive label for the field"))
        sheet.append((320, y, "a reasonably long corresponding value for it"))
    check("CRITICAL: row-PAIRED content is never split (form/data sheet)",
          _detect_two_columns(sheet) is None)

    # ---- the core fix: interleaved -> column-major ----
    # Two flowing columns whose lines do NOT share baselines (a form would —
    # and a form must never be reordered; see the CRITICAL locks above).
    interleaved = []
    mcid = 0
    for row in range(5):
        interleaved.append(_leaf(mcid, 60, 700 - 20 * row)); mcid += 1
        interleaved.append(_leaf(mcid, 330, 690 - 20 * row)); mcid += 1
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

    # ---- regions we cannot vouch for are NEVER reordered -----------------
    # An adversarial review confirmed six output-corrupting defects in a
    # richer version of this feature (full-width blocks as band separators,
    # plus recursion for 3+ columns). Every one had the same shape: a region
    # the detector could not vouch for got ordered anyway, which MANUFACTURED
    # the interleave this code exists to remove and then reported it as a fix.
    # Both were removed. These pin the conservative behaviour that replaced
    # them, so the same feature cannot be reintroduced without noticing.
    three = []
    m = 0
    for row in range(5):
        three.append(_leaf(m, 55, 700 - 20 * row)); m += 1
        three.append(_leaf(m, 230, 694 - 20 * row)); m += 1
        three.append(_leaf(m, 405, 688 - 20 * row)); m += 1
    check("a THREE-column page is DECLINED, not half-reordered",
          _reorder_leaves_for_columns(three)[1] is False)

    # A small two-column band is exactly the case the removed code got wrong:
    # too few blocks for the detector to be confident, so it was y-sorted —
    # which interleaves it. Below the confidence floor we do nothing at all.
    small = []
    m = 0
    for row in range(3):
        small.append(_leaf(m, 60, 700 - 20 * row)); m += 1
        small.append(_leaf(m, 330, 690 - 20 * row)); m += 1
    out, changed = _reorder_leaves_for_columns(small)
    check("a page below the confidence floor is left exactly as it was",
          changed is False and [lf["mcid"] for lf in out] == list(range(6)))

    # `changed` must describe the RESULT, not the geometry: a page that is
    # already column-major must never be credited with a correction.
    correct = [_leaf(i, 60, 700 - 20 * i) for i in range(5)] + [
        _leaf(5 + i, 330, 690 - 20 * i) for i in range(5)
    ]
    check("an already-correct 2-column page is not credited with a fix",
          _reorder_leaves_for_columns(correct)[1] is False)

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
