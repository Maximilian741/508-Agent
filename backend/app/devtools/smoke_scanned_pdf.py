"""Smoke: detect scanned PDFs (image pages with no extractable text).

This is the single most-embarrassing failure mode for an accessibility tool:
a user uploads a scanned-PDF government form, the engine sees zero text →
reports zero violations → claims accessibility. The document is 100%
unreadable to screen readers. SCANNED_DOCUMENT_NO_TEXT now fires as an ERROR
with a clear "OCR first" message.

Usage:
    python -m app.devtools.smoke_scanned_pdf
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_scan_')}/s.db"

from pathlib import Path  # noqa: E402

from pypdf import PdfWriter  # noqa: E402
from pypdf.generic import (  # noqa: E402
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.models.accessibility import AccessibilityFlagCode as F  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402


def _flag_codes(tree) -> set:
    found = set()

    def walk(n):
        for f in n.accessibility_flags:
            found.add(f.code.value)
        for ch in n.children:
            walk(ch)

    walk(tree.root)
    return found


def _image_xobject(w, width=2, height=2):
    """Tiny stub image XObject (2x2 gray DeviceGray) so the page has image content."""
    data = bytes([128, 128, 128, 128])
    xo = DecodedStreamObject()
    xo.set_data(data)
    xo[NameObject("/Type")] = NameObject("/XObject")
    xo[NameObject("/Subtype")] = NameObject("/Image")
    xo[NameObject("/Width")] = NumberObject(width)
    xo[NameObject("/Height")] = NumberObject(height)
    xo[NameObject("/BitsPerComponent")] = NumberObject(8)
    xo[NameObject("/ColorSpace")] = NameObject("/DeviceGray")
    return w._add_object(xo)


def _font_dict(w):
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    return DictionaryObject(
        {NameObject("/F1"): w._add_object(font)}
    )


def _build_scanned_pdf(path: Path, n_pages: int = 3) -> None:
    """A scanned-style PDF: every page is just a stretched image, no text ops."""
    w = PdfWriter()
    for _ in range(n_pages):
        img_ref = _image_xobject(w)
        page = w.add_blank_page(width=300, height=400)
        cs = DecodedStreamObject()
        # Draw the image filling the page; no BT/ET text ops at all.
        cs.set_data(b"q 300 0 0 400 0 0 cm /Im0 Do Q")
        page[NameObject("/Contents")] = w._add_object(cs)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): img_ref}),
        })
    with open(path, "wb") as fh:
        w.write(fh)


def _build_normal_pdf(path: Path, n_pages: int = 3) -> None:
    """A real text PDF — no images, lots of extractable text."""
    w = PdfWriter()
    for i in range(n_pages):
        page = w.add_blank_page(width=400, height=500)
        body = (
            b"BT /F1 12 Tf 40 460 Td (This is page %d of a real text-based document.) Tj ET\n"
            b"BT /F1 12 Tf 40 440 Td (It has plenty of extractable text on every page.) Tj ET\n"
            b"BT /F1 12 Tf 40 420 Td (No scan, no OCR needed.) Tj ET" % (i + 1)
        )
        cs = DecodedStreamObject()
        cs.set_data(body)
        page[NameObject("/Contents")] = w._add_object(cs)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): _font_dict(w),
        })
    with open(path, "wb") as fh:
        w.write(fh)


def _build_mixed_pdf(path: Path) -> None:
    """A PDF with a few image-only pages but mostly text pages — should NOT flag."""
    w = PdfWriter()
    # 4 text pages
    for i in range(4):
        page = w.add_blank_page(width=400, height=500)
        body = b"BT /F1 12 Tf 40 460 Td (Text content page %d here with lots of words.) Tj ET" % (i + 1)
        cs = DecodedStreamObject()
        cs.set_data(body)
        page[NameObject("/Contents")] = w._add_object(cs)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): _font_dict(w),
        })
    # 1 image-only page
    img_ref = _image_xobject(w)
    page = w.add_blank_page(width=300, height=400)
    cs = DecodedStreamObject()
    cs.set_data(b"q 300 0 0 400 0 0 cm /Im0 Do Q")
    page[NameObject("/Contents")] = w._add_object(cs)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): img_ref}),
    })
    with open(path, "wb") as fh:
        w.write(fh)


def main() -> int:
    failures = 0

    def check(name, cond):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp())

    SCAN = F.SCANNED_DOCUMENT_NO_TEXT.value

    # --- 1. Pure scanned PDF: all pages are image-only -> MUST flag ---
    p1 = tmp / "scanned.pdf"
    _build_scanned_pdf(p1, n_pages=3)
    res1 = parse_to_tree(str(p1))
    run_analyzers(res1.tree)
    props1 = res1.tree.root.metadata.properties
    check("parser counts: 3 image-only pages", props1.get("image_only_pages") == 3)
    check("parser counts: ~0 text chars", props1.get("total_text_chars") == 0)
    check("scanned PDF -> SCANNED_DOCUMENT_NO_TEXT fires", SCAN in _flag_codes(res1.tree))

    # --- 2. Real text PDF: NEVER fires ---
    p2 = tmp / "text.pdf"
    _build_normal_pdf(p2, n_pages=3)
    res2 = parse_to_tree(str(p2))
    run_analyzers(res2.tree)
    check("text PDF: 0 image-only pages", (res2.tree.root.metadata.properties.get("image_only_pages") or 0) == 0)
    check("text PDF -> NO SCANNED_DOCUMENT_NO_TEXT flag", SCAN not in _flag_codes(res2.tree))

    # --- 3. Mixed PDF (mostly text + one image page): NOT flagged (4/5 are text) ---
    p3 = tmp / "mixed.pdf"
    _build_mixed_pdf(p3)
    res3 = parse_to_tree(str(p3))
    run_analyzers(res3.tree)
    check("mixed PDF (1 of 5 pages image-only) -> NO flag (below 80% threshold)",
          SCAN not in _flag_codes(res3.tree))

    # --- 4. Severity is ERROR (this is high-severity) ---
    if SCAN in _flag_codes(res1.tree):
        sev = next(
            f.severity.value for f in res1.tree.root.accessibility_flags if f.code.value == SCAN
        )
        check("scanned flag severity == 'error'", sev == "error")

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
