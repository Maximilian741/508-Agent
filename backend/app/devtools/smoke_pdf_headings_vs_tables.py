"""Smoke: a data table cannot decide what "body text" is.

The tagger's heading detector took body text to be the most COMMON block
font size. A report page with an 8x10 data table at 8pt is eighty tiny
blocks against ten 12pt body sentences, so 8pt "won", every 12pt paragraph
was tagged /H3, and the output had zero /P — a fabricated outline for the
whole document, credited as a fix.

Body text is now the size carrying the most CHARACTERS: eighty three-digit
cells are ~240 characters against ~800 of prose. Pinned on exactly that page:
1 /H1 (18pt title), 2 /H2 (14pt sections), 10 /P (12pt body), and the
table tagged as a table — where before it was /H3 x10 and no /P at all.

Usage:
    python -m app.devtools.smoke_pdf_headings_vs_tables
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
from collections import Counter

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_hvt_')}/s.db")

from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject  # noqa: E402

from app.models.accessibility import AccessibilityTree, ContentKind, DocumentNode, NodeContent, NodeMetadata  # noqa: E402
from app.pdf.ua_tagger import _heading_levels, tag_pdf  # noqa: E402


def _bt(sz, x, y, t):
    return b"BT /F1 %d Tf %d %d Td (%s) Tj ET" % (sz, x, y, t)


def _report_with_table() -> PdfWriter:
    ops = [_bt(18, 72, 740, b"Annual Programme Report"), _bt(14, 72, 700, b"Section One"), _bt(14, 72, 420, b"Section Two")]
    body = [
        b"The board presents the consolidated results for the year in this section of the report.",
        b"Revenue grew in every region and operating costs were held flat across the period.",
        b"Further detail on each programme is set out in the appendices that follow this summary.",
        b"The committee reviewed the outcomes and endorsed the priorities for the coming year.",
        b"Staffing levels remained stable and the training programme was completed on schedule.",
    ]
    for i, t in enumerate(body):
        ops.append(_bt(12, 72, 670 - 18 * i, t))
    for i, t in enumerate(body):
        ops.append(_bt(12, 72, 390 - 18 * i, t))
    xs = [72 + 45 * c for c in range(11)]
    ys = [300 - 14 * r for r in range(9)]
    for y in ys:
        ops.append(b"%d %d m %d %d l S" % (xs[0], y, xs[-1], y))
    for x in xs:
        ops.append(b"%d %d m %d %d l S" % (x, ys[-1], x, ys[0]))
    for r in range(8):
        for c in range(10):
            ops.append(_bt(8, xs[c] + 3, ys[r] - 10, (b"Col%d" % c) if r == 0 else (b"%d" % (100 + r * 10 + c))))
    w = PdfWriter()
    f = DictionaryObject()
    f.update({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    fs = DictionaryObject(); fs[NameObject("/F1")] = w._add_object(f)  # noqa: SLF001
    r = DictionaryObject(); r[NameObject("/Font")] = fs
    p = w.add_blank_page(width=612, height=792)
    cs = DecodedStreamObject(); cs.set_data(b"\n".join(ops))
    p[NameObject("/Contents")] = w._add_object(cs)  # noqa: SLF001
    p[NameObject("/Resources")] = r
    return w


def _tag_counts(w: PdfWriter) -> Counter:
    buf = io.BytesIO(); w.write(buf); buf.seek(0)
    st = PdfReader(buf).trailer["/Root"]["/StructTreeRoot"].get_object()
    tags: Counter = Counter()

    def walk(e):
        e = e.get_object(); tags[str(e.get("/S") or "")] += 1
        k = e.get("/K"); k = k.get_object() if hasattr(k, "get_object") else k
        for kid in (k if isinstance(k, list) else []):
            if hasattr(kid, "get_object") and isinstance(kid.get_object(), DictionaryObject):
                walk(kid)

    for kid in st["/K"]:
        walk(kid)
    return tags


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    # Unit: 80 short 8pt blocks vs 10 long 12pt blocks -> body is 12pt.
    sizes = [8.0] * 80 + [12.0] * 10 + [18.0]
    weights = [3] * 80 + [85] * 10 + [23]
    lv = _heading_levels(sizes, weights)
    check("unit: body is the size with the most CHARACTERS (12pt), not the most blocks (8pt)",
          12.0 not in lv and 8.0 not in lv and lv.get(18.0) == 1, str(lv))
    check("unit: without weights the old block-count behaviour is unchanged (8pt body)",
          12.0 in _heading_levels(sizes) and 8.0 not in _heading_levels(sizes), str(_heading_levels(sizes)))

    # End to end on the report page.
    w = _report_with_table()
    tree = AccessibilityTree(root=DocumentNode(id="d", content=NodeContent(kind=ContentKind.NONE),
                                               metadata=NodeMetadata(source_format="pdf", language="en", properties={"title": "T"})))
    rep = tag_pdf(w, tree)
    tags = _tag_counts(w)
    check("report page: exactly one /H1 (the 18pt title)", tags.get("/H1") == 1, str(dict(tags)))
    check("report page: exactly two /H2 (the 14pt sections)", tags.get("/H2") == 2, str(dict(tags)))
    check("report page: the ten body sentences are /P (were /H3 x10, zero /P)", tags.get("/P") == 10 and not tags.get("/H3"), str(dict(tags)))
    check("report page: the data table is tagged as a table", rep.get("tables") == 1 and tags.get("/Table") == 1, str(dict(tags)))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
