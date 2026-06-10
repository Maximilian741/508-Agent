"""Smoke: TAGGED-but-badly-tagged PDFs are now genuinely analyzed.

Untagged PDFs get the deep reconstruction treatment, but tagged PDFs used to
coast: headings came from a text heuristic, tagged tables were never
itemised, and a Figure whose /Alt lives on the StructElem (the correct place
per the standards) was FALSELY flagged as missing alt. Pins:

  - headings read from H1..H6 tags (RoleMap-resolved), text recovered from
    MCID spans; the text heuristic is suppressed when tags exist
  - a tag-level H1 -> H3 jump raises HEADING_LEVEL_JUMP
  - a tagged Table with no TH raises TABLE_MISSING_HEADERS
  - Figure /Alt on the StructElem -> NO MISSING_ALT_TEXT (FP fixed)
  - Figure with no alt anywhere -> MISSING_ALT_TEXT still fires (no FN)
  - dogfood: a PDF tagged by OUR ua_tagger re-parses through the tag reader

Usage:
    python -m app.devtools.smoke_tagged_pdf_analysis
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_tpa_')}/s.db")

from PIL import Image  # noqa: E402
from pypdf import PdfWriter  # noqa: E402
from pypdf.generic import (  # noqa: E402
    ArrayObject,
    BooleanObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    StreamObject,
    TextStringObject,
)

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.models.accessibility import (  # noqa: E402
    AccessibilityTree,
    ContentKind,
    DocumentNode,
    HeadingNode,
    ImageNode,
    NodeContent,
    NodeMetadata,
    TableNode,
    iter_reading_order,
)
from app.parsers import parse_to_tree  # noqa: E402
from app.pdf.ua_tagger import tag_pdf  # noqa: E402
from app.services.remediation_engine import RemediationEngine  # noqa: E402


def _jpeg_xobject(w: PdfWriter, px=(60, 40), color=(180, 40, 40)):
    img = Image.new("RGB", px, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    xobj = StreamObject()
    xobj._data = buf.getvalue()  # noqa: SLF001
    xobj.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(px[0]),
            NameObject("/Height"): NumberObject(px[1]),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
            NameObject("/Filter"): NameObject("/DCTDecode"),
        }
    )
    return w._add_object(xobj)  # noqa: SLF001


def _struct_elem(w, s, parent_ref, page_ref, kids, alt=None):
    el = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/StructElem"),
            NameObject("/S"): NameObject(s),
            NameObject("/P"): parent_ref,
            NameObject("/Pg"): page_ref,
        }
    )
    if alt is not None:
        el[NameObject("/Alt")] = TextStringObject(alt)
    ref = w._add_object(el)  # noqa: SLF001
    resolved_kids = []
    for k in kids:
        if isinstance(k, int):
            resolved_kids.append(NumberObject(k))
        else:
            resolved_kids.append(k)
    if len(resolved_kids) == 1:
        el[NameObject("/K")] = resolved_kids[0]
    elif resolved_kids:
        el[NameObject("/K")] = ArrayObject(resolved_kids)
    return ref


def _build_bad_tagged_pdf(path: Path) -> None:
    """Tagged PDF with: H1 -> custom /Head3 (RoleMap -> H3) jump; a 3-row
    tagged table with NO TH; Figure A with /Alt on the StructElem only;
    Figure B with no alt anywhere."""

    w = PdfWriter()
    page = w.add_blank_page(width=612, height=792)
    im_a = _jpeg_xobject(w, color=(180, 40, 40))
    im_b = _jpeg_xobject(w, color=(40, 40, 180))

    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)}),
            NameObject("/XObject"): DictionaryObject(
                {NameObject("/ImA"): im_a, NameObject("/ImB"): im_b}
            ),
        }
    )
    content = (
        b"/H1 <</MCID 0>> BDC BT /F1 20 Tf 40 700 Td (Service Review Title) Tj ET EMC\n"
        b"/Head3 <</MCID 1>> BDC BT /F1 14 Tf 40 660 Td (Jumped Subsection) Tj ET EMC\n"
        b"/P <</MCID 2>> BDC BT /F1 11 Tf 40 620 Td (Ordinary body paragraph for content.) Tj ET EMC\n"
        b"/Figure <</MCID 3>> BDC q 100 0 0 80 40 480 cm /ImA Do Q EMC\n"
        b"/Figure <</MCID 4>> BDC q 100 0 0 80 200 480 cm /ImB Do Q EMC\n"
    )
    cs = DecodedStreamObject()
    cs.set_data(content)
    page[NameObject("/Contents")] = w._add_object(cs)  # noqa: SLF001
    page_ref = page.indirect_reference

    st = DictionaryObject()
    st_ref = w._add_object(st)  # noqa: SLF001
    doc_el = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/StructElem"),
            NameObject("/S"): NameObject("/Document"),
            NameObject("/P"): st_ref,
        }
    )
    doc_ref = w._add_object(doc_el)  # noqa: SLF001

    h1 = _struct_elem(w, "/H1", doc_ref, page_ref, [0])
    h3 = _struct_elem(w, "/Head3", doc_ref, page_ref, [1])  # RoleMap -> H3
    para = _struct_elem(w, "/P", doc_ref, page_ref, [2])
    fig_a = _struct_elem(w, "/Figure", doc_ref, page_ref, [3], alt="Bar chart of programme results")
    fig_b = _struct_elem(w, "/Figure", doc_ref, page_ref, [4])  # no alt anywhere

    # 3-row tagged table with NO TH (cells carry no content kids — allowed).
    trs = []
    for _r in range(3):
        cells = [_struct_elem(w, "/TD", doc_ref, page_ref, []) for _c in range(2)]
        trs.append(_struct_elem(w, "/TR", doc_ref, page_ref, cells))
    table = _struct_elem(w, "/Table", doc_ref, page_ref, trs)

    doc_el[NameObject("/K")] = ArrayObject([h1, h3, para, fig_a, fig_b, table])
    st.update(
        {
            NameObject("/Type"): NameObject("/StructTreeRoot"),
            NameObject("/K"): ArrayObject([doc_ref]),
            NameObject("/RoleMap"): DictionaryObject(
                {NameObject("/Head3"): NameObject("/H3")}
            ),
        }
    )
    catalog = w._root_object  # noqa: SLF001
    catalog[NameObject("/StructTreeRoot")] = st_ref
    catalog[NameObject("/MarkInfo")] = DictionaryObject(
        {NameObject("/Marked"): BooleanObject(True)}
    )
    with open(path, "wb") as fh:
        w.write(fh)


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="tpa_"))

    # ===== Hand-built badly tagged PDF =======================================
    bad = tmp / "bad_tagged.pdf"
    _build_bad_tagged_pdf(bad)
    res = parse_to_tree(str(bad))
    run_analyzers(res.tree)
    nodes = list(iter_reading_order(res.tree.root))

    headings = [n for n in nodes if isinstance(n, HeadingNode)]
    check("2 headings read from tags", len(headings) == 2, str([(h.level, h.content.text) for h in headings]))
    check(
        "RoleMap-resolved levels (1 then 3) with MCID text",
        [h.level for h in headings] == [1, 3]
        and "Service Review Title" in (headings[0].content.text or "")
        and "Jumped Subsection" in (headings[1].content.text or ""),
        str([(h.level, h.content.text) for h in headings]),
    )

    viols = RemediationEngine().detect_violations(res.tree)
    rule_ids = [v.rule_id for v in viols]
    check("tag-level H1->H3 jump raises HEADING_LEVEL_JUMP", "HEADING_LEVEL_JUMP" in rule_ids, str(rule_ids))

    tables = [n for n in nodes if isinstance(n, TableNode)]
    check("tagged table emitted", len(tables) == 1)
    check("tagged TH-less table raises TABLE_MISSING_HEADERS", "TABLE_MISSING_HEADERS" in rule_ids)

    images = [n for n in nodes if isinstance(n, ImageNode)]
    with_alt = [n for n in images if n.alt_text]
    without_alt = [n for n in images if not n.alt_text and not n.is_decorative]
    check(
        "Figure /Alt on StructElem honoured (FP fixed)",
        any("Bar chart" in (n.alt_text or "") for n in with_alt),
        str([(n.id, n.alt_text) for n in images]),
    )
    check("Figure with no alt anywhere still flagged", len(without_alt) == 1 and "MISSING_ALT_TEXT" in rule_ids)

    # ===== Dogfood: OUR tagger's output re-parses through the tag reader =====
    w = PdfWriter()
    page = w.add_blank_page(width=460, height=560)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})}
    )
    cs = DecodedStreamObject()
    cs.set_data(
        b"BT /F1 20 Tf 40 520 Td (Dogfood Title) Tj ET\n"
        b"BT /F1 11 Tf 40 480 Td (Body paragraph one with enough words to be prose.) Tj ET\n"
        b"BT /F1 11 Tf 40 462 Td (Body paragraph two continues the document nicely.) Tj ET"
    )
    page[NameObject("/Contents")] = w._add_object(cs)  # noqa: SLF001
    tree = AccessibilityTree(
        root=DocumentNode(
            id="d",
            content=NodeContent(kind=ContentKind.NONE),
            metadata=NodeMetadata(source_format="pdf", language="en", properties={"title": "T"}),
        )
    )
    tag_pdf(w, tree)
    dog = tmp / "dogfood.pdf"
    with open(dog, "wb") as fh:
        w.write(fh)

    res2 = parse_to_tree(str(dog))
    run_analyzers(res2.tree)
    headings2 = [n for n in iter_reading_order(res2.tree.root) if isinstance(n, HeadingNode)]
    check(
        "dogfood: tagger's H1 read back via tags with text",
        len(headings2) == 1 and headings2[0].level == 1 and "Dogfood Title" in (headings2[0].content.text or ""),
        str([(h.level, h.content.text) for h in headings2]),
    )
    check(
        "dogfood: no heading-jump false positives",
        "HEADING_LEVEL_JUMP" not in [v.rule_id for v in RemediationEngine().detect_violations(res2.tree)],
    )

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
