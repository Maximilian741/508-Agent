"""Smoke: running headers/footers/page numbers are tagged /Artifact, not content.

PDF/UA (Matterhorn 01-005) requires pagination artifacts — running heads, footers
and page numbers — to be marked as artifacts and kept OUT of the structure tree.
The tagger detects blocks that repeat at the same header/footer-band position
across >=2 pages and wraps them ``/Artifact BMC..EMC`` instead of tagging them as
content.

Usage:
    python -m app.devtools.smoke_pdf_artifacts
"""

from __future__ import annotations

import io
import os
import sys
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_pdfart_')}/s.db"

from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject  # noqa: E402

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


def _tag(page_bodies, height=800, width=400):
    w = PdfWriter()
    res = _font_res(w)
    for body in page_bodies:
        page = w.add_blank_page(width=width, height=height)
        cs = DecodedStreamObject()
        cs.set_data(body)
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


def _top_tags(r):
    st = r.trailer["/Root"]["/StructTreeRoot"].get_object()
    doc = st["/K"][0].get_object()
    return [str(c.get_object().get("/S")) for c in doc["/K"]]


def main() -> int:
    failures = 0

    def check(name, cond):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    # 3 pages: running header (same), body (unique), footer page number (changing text, same position).
    pages = []
    for pno in (1, 2, 3):
        pages.append(b"\n".join([
            b"BT /F1 11 Tf 60 770 Td (Confidential Report) Tj ET",     # header, top band
            b"BT /F1 12 Tf 60 400 Td (Unique body text on a page.) Tj ET",  # body
            b"BT /F1 10 Tf 200 30 Td (%d) Tj ET" % pno,                # page number, bottom band
        ]))
    rep, r = _tag(pages)

    check("6 pagination artifacts detected (header+footer x3)", rep.get("artifacts") == 6)
    check("struct tree holds only body content (3 elements)", rep.get("elements") == 3)
    check("no header/footer in the struct tree (all /P body)", _top_tags(r) == ["/P", "/P", "/P"])

    c0 = r.pages[0]["/Contents"].get_object().get_data()
    check("page content carries /Artifact BMC", b"/Artifact" in c0 and b"BMC" in c0)
    check("text preserved (header still renders)", "Confidential Report" in (r.pages[0].extract_text() or ""))

    # Single page: nothing repeats, so NOTHING is an artifact (need >=2 pages).
    rep1, _ = _tag([b"BT /F1 11 Tf 60 770 Td (Header) Tj ET\nBT /F1 12 Tf 60 400 Td (Body.) Tj ET"])
    check("single-page doc -> 0 artifacts (no cross-page repetition)", rep1.get("artifacts") == 0)

    # Body text that happens to repeat but is NOT in the band -> not an artifact.
    rep2, r2 = _tag([
        b"BT /F1 12 Tf 60 400 Td (Same body line.) Tj ET",
        b"BT /F1 12 Tf 60 400 Td (Same body line.) Tj ET",
    ])
    check("repeated mid-page body line -> NOT an artifact", rep2.get("artifacts") == 0)

    # EXPERT FP GUARD: DIFFERENT body text at the same top-margin position across
    # pages must NOT be stripped (the position-only bug). Margin band ~y>712.
    rep3, _ = _tag([
        b"BT /F1 12 Tf 60 730 Td (Introduction to one topic here.) Tj ET\nBT /F1 12 Tf 60 690 Td (a) Tj ET",
        b"BT /F1 12 Tf 60 730 Td (Conclusion on a different topic.) Tj ET\nBT /F1 12 Tf 60 690 Td (b) Tj ET",
    ])
    check("different text at same margin position -> NOT an artifact (text-aware)", rep3.get("artifacts") == 0)

    # EXPERT FP GUARD: per-page DIFFERENT headings at the top stay as content.
    rep4, _ = _tag([
        b"BT /F1 20 Tf 60 740 Td (Chapter One) Tj ET\nBT /F1 12 Tf 60 690 Td (a) Tj ET",
        b"BT /F1 20 Tf 60 740 Td (Chapter Two) Tj ET\nBT /F1 12 Tf 60 690 Td (b) Tj ET",
    ])
    check("per-page distinct headings -> NOT artifacts (kept in tree)", rep4.get("artifacts") == 0)

    # Page numbers (changing digits, stable position) ARE caught via carve-out.
    rep5, _ = _tag([
        b"BT /F1 12 Tf 60 400 Td (body a) Tj ET\nBT /F1 10 Tf 300 40 Td (1) Tj ET",
        b"BT /F1 12 Tf 60 400 Td (body b) Tj ET\nBT /F1 10 Tf 300 40 Td (2) Tj ET",
    ])
    check("page numbers (changing digits) -> artifacts via page-number carve-out", rep5.get("artifacts") == 2)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
