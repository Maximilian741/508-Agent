"""Smoke: the PDF writer turns an untagged PDF into a tagged one.

Builds a minimal one-page PDF, runs it through the real writer, and asserts the
output carries the PDF/UA essentials (MarkInfo/Marked, a linked StructTreeRoot,
DisplayDocTitle, /Lang, XMP title + pdfuaid) WITHOUT corrupting the content
(the page text must still be extractable).

Usage:
    python -m app.devtools.smoke_pdf_tagging
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_pdfua_')}/s.db"

from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject  # noqa: E402

from app.models.accessibility import DocumentNode  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.writers import write_remediated  # noqa: E402


def _build_pdf(path: Path) -> None:
    w = PdfWriter()
    page = w.add_blank_page(width=300, height=200)
    cs = DecodedStreamObject()
    cs.set_data(b"BT /F1 18 Tf 20 100 Td (Hello Accessible World) Tj ET")
    page[NameObject("/Contents")] = w._add_object(cs)  # noqa: SLF001
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})}  # noqa: SLF001
    )
    with open(path, "wb") as fh:
        w.write(fh)


def _g(d, k):
    v = d.get(k)
    return v.get_object() if hasattr(v, "get_object") else v


def main() -> int:
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp())
    src = tmp / "in.pdf"
    _build_pdf(src)

    res = parse_to_tree(str(src))
    if isinstance(res.tree.root, DocumentNode):
        res.tree.root.metadata.properties = dict(res.tree.root.metadata.properties or {})
        res.tree.root.metadata.properties["title"] = "Quarterly Accessibility Report"
        res.tree.root.metadata.language = "en-US"

    out = tmp / "out.pdf"
    write_remediated(src, res.tree, out, source_format=res.format)

    r = PdfReader(str(out))
    root = r.trailer["/Root"]
    if hasattr(root, "get_object"):
        root = root.get_object()

    mark = _g(root, "/MarkInfo")
    check("MarkInfo/Marked true", bool(mark) and bool(_g(mark, "/Marked")))

    st = _g(root, "/StructTreeRoot")
    check("StructTreeRoot present", st is not None)
    if st is not None:
        kids = _g(st, "/K")
        doc = (kids[0].get_object() if kids and hasattr(kids[0], "get_object") else (kids[0] if kids else None))
        check("Document struct element", doc is not None and str(_g(doc, "/S")) == "/Document")
        check("ParentTree present", _g(st, "/ParentTree") is not None)

    vp = _g(root, "/ViewerPreferences")
    check("DisplayDocTitle true", bool(vp) and bool(_g(vp, "/DisplayDocTitle")))
    check("/Lang set", _g(root, "/Lang") is not None)

    meta = _g(root, "/Metadata")
    xmp = b""
    if meta is not None:
        try:
            xmp = meta.get_data()
        except Exception:
            xmp = b""
    check("XMP carries dc:title", b"Quarterly Accessibility Report" in xmp)
    check("XMP carries pdfuaid:part", b"pdfuaid:part" in xmp)

    # Content integrity: text must still be extractable (we never rewrote bytes).
    txt = r.pages[0].extract_text() or ""
    check("page text still extractable (not corrupted)", "Hello Accessible World" in txt)
    check("page /StructParents set", _g(r.pages[0], "/StructParents") is not None)

    # Re-open through the project's own reader path to confirm validity.
    check("output re-opens cleanly", len(PdfReader(str(out)).pages) == 1)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
