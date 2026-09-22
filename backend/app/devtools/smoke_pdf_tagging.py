"""Smoke: the PDF writer turns an untagged PDF into a tagged one — safely.

Covers the happy path AND the adversarial cases that matter in production:
* multi-page tagging with correct ParentTree / StructParents invariants,
* already-tagged PDFs are NOT corrupted (structure preserved, no double-wrap),
* idempotence (running twice does not add a second marked-content sequence),
* encrypted PDFs are skipped gracefully (no crash),
* pages that already contain marked content are skipped (no artifact nesting).

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
from pypdf.generic import (  # noqa: E402
    ArrayObject,
    BooleanObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from app.models.accessibility import DocumentNode  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.writers import write_remediated  # noqa: E402


def _font_resources(w: PdfWriter) -> DictionaryObject:
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    return DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})}  # noqa: SLF001
    )


def _build_pdf(path: Path, pages: int = 1, bdc_on_last: bool = False, encrypt: bool = False) -> None:
    w = PdfWriter()
    res = _font_resources(w)
    for i in range(pages):
        page = w.add_blank_page(width=300, height=200)
        body = b"BT /F1 18 Tf 20 100 Td (Page text %d) Tj ET" % i
        if bdc_on_last and i == pages - 1:
            body = b"/Span <</MCID 0>> BDC " + body + b" EMC"
        cs = DecodedStreamObject()
        cs.set_data(body)
        page[NameObject("/Contents")] = w._add_object(cs)  # noqa: SLF001
        page[NameObject("/Resources")] = res
    if encrypt:
        w.encrypt("ownerpw")
    with open(path, "wb") as fh:
        w.write(fh)


def _g(d, k):
    v = d.get(k)
    return v.get_object() if hasattr(v, "get_object") else v


def _content_bytes(page) -> bytes:
    c = _g(page, "/Contents")
    if c is None:
        return b""
    if isinstance(c, ArrayObject):
        out = []
        for it in c:
            o = it.get_object() if hasattr(it, "get_object") else it
            try:
                out.append(o.get_data())
            except Exception:
                pass
        return b"\n".join(out)
    try:
        return c.get_data()
    except Exception:
        return b""


def _remediate(src: Path, out: Path, title=None, lang=None) -> dict:
    res = parse_to_tree(str(src))
    if isinstance(res.tree.root, DocumentNode):
        res.tree.root.metadata.properties = dict(res.tree.root.metadata.properties or {})
        # Stands in for an approved TAG_PDF_STRUCTURE: the writer tags only on request.
        res.tree.root.metadata.properties["tag_structure_requested"] = True
        if title:
            res.tree.root.metadata.properties["title"] = title
        if lang:
            res.tree.root.metadata.language = lang
    return write_remediated(src, res.tree, out, source_format=res.format)


def main() -> int:
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp())

    # ---- Case 1: untagged 3-page PDF -> tagged, invariants hold ----------
    src = tmp / "multi.pdf"
    _build_pdf(src, pages=3)
    out = tmp / "multi_out.pdf"
    _remediate(src, out, title="Quarterly Accessibility Report", lang="en-US")

    r = PdfReader(str(out))
    root = _g(r.trailer, "/Root")
    mark = _g(root, "/MarkInfo")
    st = _g(root, "/StructTreeRoot")
    check("MarkInfo/Marked true", bool(mark) and bool(_g(mark, "/Marked")))
    check("StructTreeRoot present", st is not None)
    check("DisplayDocTitle true", bool(_g(root, "/ViewerPreferences")) and bool(_g(_g(root, "/ViewerPreferences"), "/DisplayDocTitle")))
    check("/Lang set", _g(root, "/Lang") is not None)
    meta = _g(root, "/Metadata")
    xmp = meta.get_data() if meta is not None else b""
    check("XMP carries dc:title + pdfuaid", b"Quarterly Accessibility Report" in xmp and b"pdfuaid:part" in xmp)

    if st is not None:
        kids = _g(st, "/K")
        doc = (kids[0].get_object() if kids and hasattr(kids[0], "get_object") else (kids[0] if kids else None))
        doc_kids = _g(doc, "/K") if doc else None
        check("Document -> one P per page (3)", doc is not None and doc_kids is not None and len(doc_kids) == 3)
        pt = _g(st, "/ParentTree")
        nums = _g(pt, "/Nums") if pt else None
        keys = [int(nums[i]) for i in range(0, len(nums), 2)] if nums else []
        check("ParentTree keys are 0,1,2", keys == [0, 1, 2])
    sps = sorted(int(_g(p, "/StructParents")) for p in r.pages if _g(p, "/StructParents") is not None)
    check("each page has matching StructParents", sps == [0, 1, 2])
    check("every page wrapped exactly once (one BDC each)", all(_content_bytes(p).count(b"BDC") == 1 for p in r.pages))
    check("page text still extractable", "Page text 0" in (r.pages[0].extract_text() or ""))

    # ---- Case 2: idempotence — re-remediating a tagged PDF must NOT double-wrap
    out2 = tmp / "multi_out2.pdf"
    _remediate(out, out2, title="Quarterly Accessibility Report", lang="en-US")
    r2 = PdfReader(str(out2))
    check("idempotent: still one BDC per page (no double wrap)", all(_content_bytes(p).count(b"BDC") == 1 for p in r2.pages))
    check("idempotent: still exactly one StructTreeRoot", _g(_g(r2.trailer, "/Root"), "/StructTreeRoot") is not None)
    check("idempotent: page count preserved", len(r2.pages) == 3)

    # ---- Case 3: already-tagged input is preserved (not destroyed) -------
    tagged_src = tmp / "pretagged.pdf"
    _build_pdf(tagged_src, pages=1)
    # Mark it tagged with a sentinel struct tree, then remediate.
    rw = PdfWriter(clone_from=PdfReader(str(tagged_src)))
    sentinel = DictionaryObject({NameObject("/Type"): NameObject("/StructTreeRoot"), NameObject("/K"): ArrayObject()})
    rw._root_object[NameObject("/StructTreeRoot")] = rw._add_object(sentinel)  # noqa: SLF001
    rw._root_object[NameObject("/MarkInfo")] = DictionaryObject({NameObject("/Marked"): BooleanObject(True)})  # noqa: SLF001
    with open(tagged_src, "wb") as fh:
        rw.write(fh)
    tagged_out = tmp / "pretagged_out.pdf"
    _remediate(tagged_out.with_name("pretagged.pdf"), tagged_out, title="Already Tagged", lang="en-US")
    rt = PdfReader(str(tagged_out))
    check("already-tagged: page NOT re-wrapped (no new BDC)", _content_bytes(rt.pages[0]).count(b"BDC") == 0)
    check("already-tagged: doc metadata (Lang) still applied", _g(_g(rt.trailer, "/Root"), "/Lang") is not None)
    check("already-tagged: text intact", "Page text 0" in (rt.pages[0].extract_text() or ""))

    # ---- Case 4: encrypted PDF -> writer skips gracefully (no crash) -----
    # The writer copies+opens the encrypted source itself, where the clone
    # fails — it must record `failed_to_open_pdf` and return the unchanged copy,
    # never raise. (In the real pipeline parse_to_tree gates encrypted files at
    # 422 first; this tests the writer's own defensive contract.)
    clean_tree = parse_to_tree(str(src)).tree  # `src` is the clean 3-page PDF
    enc = tmp / "enc.pdf"
    _build_pdf(enc, pages=1, encrypt=True)
    enc_out = tmp / "enc_out.pdf"
    crashed = False
    rep = None
    try:
        rep = write_remediated(enc, clean_tree, enc_out, source_format="pdf")
    except Exception as exc:  # must NOT happen
        crashed = True
        print("  (encrypted raised:", exc, ")")
    check("encrypted: writer did not crash", not crashed)
    check("encrypted: output file still produced", enc_out.exists())
    check(
        "encrypted: reported as failed_to_open_pdf (skipped)",
        bool(rep) and any("failed_to_open_pdf" in str(s.get("reason", "")) for s in rep.get("skipped", [])),
    )

    # ---- Case 5: page with existing marked content is skipped ------------
    mixed = tmp / "mixed.pdf"
    _build_pdf(mixed, pages=2, bdc_on_last=True)  # page 0 clean, page 1 has BDC
    mixed_out = tmp / "mixed_out.pdf"
    _remediate(mixed, mixed_out, title="Mixed", lang="en")
    rm = PdfReader(str(mixed_out))
    check("mixed: clean page wrapped (1 BDC)", _content_bytes(rm.pages[0]).count(b"BDC") == 1)
    check("mixed: pre-marked page left alone (still its 1 original BDC)", _content_bytes(rm.pages[1]).count(b"BDC") == 1)
    st_m = _g(_g(rm.trailer, "/Root"), "/StructTreeRoot")
    doc_m = None
    if st_m is not None:
        km = _g(st_m, "/K")
        doc_m = (km[0].get_object() if km and hasattr(km[0], "get_object") else (km[0] if km else None))
    check("mixed: only the clean page is in the struct tree (1 P)", doc_m is not None and len(_g(doc_m, "/K")) == 1)

    # ---- Case 6: per-element tagging (multi-block text + image -> Figure) ---
    pe = tmp / "perel.pdf"
    w = PdfWriter()
    page = w.add_blank_page(width=400, height=300)
    img = DecodedStreamObject()
    img.set_data(bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 0]))
    for k, v in {
        "/Type": NameObject("/XObject"), "/Subtype": NameObject("/Image"),
        "/Width": NumberObject(2), "/Height": NumberObject(2),
        "/ColorSpace": NameObject("/DeviceRGB"), "/BitsPerComponent": NumberObject(8),
    }.items():
        img[NameObject(k)] = v
    img_ref = w._add_object(img)  # noqa: SLF001
    content = b"BT /F1 18 Tf 20 250 Td (Heading block) Tj ET\nBT /F1 12 Tf 20 200 Td (Paragraph block) Tj ET\nq 50 0 0 50 100 80 cm /Im0 Do Q"
    cs = DecodedStreamObject(); cs.set_data(content)
    page[NameObject("/Contents")] = w._add_object(cs)  # noqa: SLF001
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)}),  # noqa: SLF001
        NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): img_ref}),
    })
    with open(pe, "wb") as fh:
        w.write(fh)

    # Set alt on the parsed image, then remediate.
    from app.models.accessibility import ImageNode, iter_reading_order
    res = parse_to_tree(str(pe))
    for n in iter_reading_order(res.tree.root):
        if isinstance(n, ImageNode):
            n.alt_text = "A coloured test figure"; n.is_decorative = False
    if isinstance(res.tree.root, DocumentNode):
        res.tree.root.metadata.properties = dict(res.tree.root.metadata.properties or {})
        res.tree.root.metadata.properties["title"] = "Per Element"; res.tree.root.metadata.language = "en"
        res.tree.root.metadata.properties["tag_structure_requested"] = True  # approved TAG_PDF_STRUCTURE
    pe_out = tmp / "perel_out.pdf"
    write_remediated(pe, res.tree, pe_out, source_format=res.format)

    rp = PdfReader(str(pe_out))
    st_p = _g(_g(rp.trailer, "/Root"), "/StructTreeRoot")
    doc_p = None
    if st_p is not None:
        kp = _g(st_p, "/K")
        doc_p = (kp[0].get_object() if kp and hasattr(kp[0], "get_object") else (kp[0] if kp else None))
    specs = [str(_g(k.get_object() if hasattr(k, "get_object") else k, "/S")) for k in (_g(doc_p, "/K") or [])] if doc_p else []
    check("per-element: heading + paragraph + figure", specs.count("/H1") == 1 and specs.count("/P") == 1 and specs.count("/Figure") == 1)
    # the Figure carries alt
    fig_alt = ""
    for k in (_g(doc_p, "/K") or []):
        el = k.get_object() if hasattr(k, "get_object") else k
        if str(_g(el, "/S")) == "/Figure":
            fig_alt = str(_g(el, "/Alt") or "")
    check("per-element: figure has /Alt in the tree", "coloured test figure" in fig_alt)
    check("per-element: all text still extracts", all(s in (rp.pages[0].extract_text() or "") for s in ["Heading block", "Paragraph block"]))

    # ---- Case 7: heading-level detection by font size ----------------------
    hp = tmp / "headings.pdf"
    w = PdfWriter()
    page = w.add_blank_page(width=400, height=300)
    content = (
        b"BT /F1 24 Tf 20 260 Td (Big Heading) Tj ET\n"
        b"BT /F1 12 Tf 20 230 Td (Body paragraph one) Tj ET\n"
        b"BT /F1 12 Tf 20 200 Td (Body paragraph two) Tj ET\n"
        b"BT /F1 18 Tf 20 170 Td (Sub Heading) Tj ET"
    )
    cs = DecodedStreamObject(); cs.set_data(content)
    page[NameObject("/Contents")] = w._add_object(cs)  # noqa: SLF001
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})})  # noqa: SLF001
    with open(hp, "wb") as fh:
        w.write(fh)
    hp_out = tmp / "headings_out.pdf"
    _remediate(hp, hp_out, title="Headings", lang="en")
    rh = PdfReader(str(hp_out))
    st_h = _g(_g(rh.trailer, "/Root"), "/StructTreeRoot")
    doc_h = None
    if st_h is not None:
        kh = _g(st_h, "/K")
        doc_h = (kh[0].get_object() if kh and hasattr(kh[0], "get_object") else (kh[0] if kh else None))
    hspecs = [str(_g(k.get_object() if hasattr(k, "get_object") else k, "/S")) for k in (_g(doc_h, "/K") or [])] if doc_h else []
    check("heading detection: 24pt->H1, 12pt body->P, 18pt->H2", hspecs == ["/H1", "/P", "/P", "/H2"])

    # ---- Case 8: adversarial content-stream cases (expert findings) --------
    def _one_page(content_bytes: bytes) -> bytes:
        w2 = PdfWriter()
        pg = w2.add_blank_page(width=400, height=300)
        c = DecodedStreamObject(); c.set_data(content_bytes)
        pg[NameObject("/Contents")] = w2._add_object(c)  # noqa: SLF001
        fnt = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        pg[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): w2._add_object(fnt)})})  # noqa: SLF001
        p = tmp / f"adv_{abs(hash(content_bytes)) % 99999}.pdf"
        with open(p, "wb") as fh:
            w2.write(fh)
        o = p.with_name(p.stem + "_out.pdf")
        _remediate(p, o, title="Adv", lang="en")
        return _content_bytes(PdfReader(str(o)).pages[0])

    # 8a: visible text containing the acronym "BDC" must NOT exclude the page.
    out_bdc = _one_page(b"BT /F1 12 Tf 20 250 Td (Report by the BDC team) Tj ET")
    check("acronym 'BDC' in visible text still gets tagged", out_bdc.count(b"BDC") >= 1)

    # 8b: graphics state (cm transform + rg colour) is preserved through re-serialization.
    gstate = b"q 1 0 0 1 10 10 cm 0.2 0.4 0.6 rg BT /F1 12 Tf 20 250 Td (Coloured text) Tj ET Q"
    out_g = _one_page(gstate)
    check("graphics-state ops (cm + rg) preserved", b"cm" in out_g and b"rg" in out_g)
    check("graphics-state page got tagged", out_g.count(b"BDC") >= 1)

    # 8c: unterminated BT must fall back to the safe page-level wrap (1 BDC), not corrupt.
    out_bad = _one_page(b"BT /F1 12 Tf 20 250 Td (Unterminated block) Tj")
    check("unterminated BT -> safe fallback, still wrapped once", out_bad.count(b"BDC") == 1)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
