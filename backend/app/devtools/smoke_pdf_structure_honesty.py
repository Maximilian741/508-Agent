"""Smoke: the PDF tagger never creates a new defect, never claims what it did
not verify, and says out loud what it left alone.

Pinned (each built as a PDF, tagged, and read back from the output file):

  1. Heading levels by raw font-size rank skipped levels (26pt title -> H1,
     14pt subtitle -> H4 because 18/16pt headings exist elsewhere): re-auditing
     our own output raised HEADING_LEVEL_JUMP — new manual work we created.
     Levels are now contiguous in reading order, and re-analysis is clean.
  2. A cover page (26pt / 14pt / 10pt, one block each) refused its title
     because page-1 block counting called 26pt "body". Body is now the size
     carrying the most characters over the first pages.
  3. A table of contents (entry | page number) was tagged /Table, and
     re-analysis raised a NEW TABLE_MISSING_HEADERS. It now stays paragraphs
     (tocTablesDeclined), and dot-leader lines are /Artifact.
  4. A two-column page the tagger declined to reorder (lines that pair up
     across the gutter) shipped as "tagged" with nothing said. It is now
     counted as readingOrderDeclinedPages and disclosed in writer.skipped —
     never counted as fixed. (The reorder logic itself is unchanged.)
  5. /Table and /L now carry /Pg: a table on page 2 is read back on page 2
     (it used to come back as page 1).
  6. XMP pdfuaid:part is written only when every verifiable check passes
     (fonts embedded, no untagged painting, title, language, no undescribed
     image ...); otherwise omitted with the reasons in writer.skipped. An
     already-tagged document's own claim is carried over, never invented.

Usage:
    python -m app.devtools.smoke_pdf_structure_honesty
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

from app.devtools import _pdf_fixture_kit as K

_TMP = K.isolated_env("508_smoke_structhon_")

from pypdf import PdfReader, PdfWriter  # noqa: E402

BODY = "Stormwater fees fund the drainage system that keeps streets clear during heavy rain events."
BULLETS = ["\x95 Residential rates apply to single homes", "\x95 Commercial rates apply to shops and offices",
           "\x95 Industrial rates apply to plants and yards"]
LEFT = ["Crews begin pre-treating bridges when the forecast", "shows freezing rain, and plows go out once two",
        "inches of snow have fallen on the arterial roads", "that carry buses and emergency vehicles first",
        "before neighborhood streets are cleared later", "in the storm when the main roads are passable"]
RIGHT = ["The department stocks rock salt at four depots", "across the city, and brine is mixed on site at",
         "the central garage so trucks can reload quickly", "during long storms that last through the night",
         "while crews rotate on twelve hour shifts until", "every route on the map has been completed"]


def report_pdf() -> bytes:
    w = PdfWriter()
    f = K.helvetica(w)
    # cover: one block per size
    c = K.bt("F1", 26, 72, 600, K.lit("Stormwater Utility Annual Report"))
    c += K.bt("F1", 14, 72, 560, K.lit("Fiscal Year 2026"))
    c += K.bt("F1", 10, 72, 100, K.lit("City of Springfield | Public Works"))
    K.add_page(w, c, {"F1": f})
    # contents with dot leaders
    c = K.bt("F1", 18, 72, 720, K.lit("Contents"))
    y = 680
    for t, pg in [("1. Introduction", "3"), ("2. Program Overview", "3"), ("3. Rates", "4"), ("4. Outlook", "4")]:
        c += K.bt("F1", 11, 72, y, K.lit(t)) + K.bt("F1", 11, 200, y, K.lit("." * 60)) + K.bt("F1", 11, 520, y, K.lit(pg))
        y -= 20
    K.add_page(w, c, {"F1": f})
    # chapters: 16pt / 13pt headings, 11pt body
    for p in range(2):
        c = K.bt("F1", 16, 72, 720, K.lit("1. Introduction" if p == 0 else "3. Rates"))
        c += K.bt("F1", 13, 72, 690, K.lit("2.1 Rate Structure" if p == 0 else "3.1 Residential"))
        for i in range(8):
            c += K.bt("F1", 11, 72, 660 - 14 * i, K.lit(BODY))
        if p == 0:
            # A SIBLING of 2.1 at the same 13pt size (the clamp once filed it
            # as a child of 2.1: H4 then H5).
            c += K.bt("F1", 13, 72, 530, K.lit("2.2 Capital Projects"))
            for i in range(3):
                c += K.bt("F1", 11, 72, 500 - 14 * i, K.lit(BODY))
        else:
            # A bulleted list (located on re-parse from its tagged text).
            for i, item in enumerate(BULLETS):
                c += K.bt("F1", 11, 90, 520 - 16 * i, K.lit(item))
        K.add_page(w, c, {"F1": f})
    # bare TOC (no leaders): entry | page number
    c = K.bt("F1", 16, 72, 700, K.lit("Table of Contents"))
    y = 660
    for t, pg in [("Introduction", "3"), ("Overview", "3"), ("Rates", "4"), ("Outlook", "5")]:
        c += K.bt("F1", 11, 72, y, K.lit(t)) + K.bt("F1", 11, 520, y, K.lit(pg))
        y -= 20
    K.add_page(w, c, {"F1": f})
    # two-column page whose lines pair up across the gutter
    c = b""
    y = 700
    for a, b in zip(LEFT, RIGHT):
        c += K.bt("F1", 10, 72, y, K.lit(a)) + K.bt("F1", 10, 320, y, K.lit(b))
        y -= 14
    K.add_page(w, c, {"F1": f})
    # a data table on its own page (page 7)
    c = K.bt("F1", 16, 72, 720, K.lit("Rates by Class"))
    y = 680
    for row in [("Class", "Rate", "Units"), ("Residential", "4.10", "ERU"), ("Commercial", "6.25", "ERU"), ("Industrial", "8.00", "ERU")]:
        x = 72
        for cell in row:
            c += K.bt("F1", 10, x, y, K.lit(cell))
            x += 120
        y -= 16
    K.add_page(w, c, {"F1": f})
    return K.to_bytes(w)


def clean_pdf() -> bytes:
    """Qualifies for a PDF/UA claim: embedded font, text only."""
    lines = ["Annual Snow Plan", "Plows run on arterial roads first and then on residential streets."]
    w = PdfWriter()
    f = K.type0_font(w, set("".join(lines)), embedded=True)
    c = K.bt("F1", 20, 72, 720, K.type0_hex(lines[0]))
    for i in range(6):
        c += K.bt("F1", 11, 72, 690 - 14 * i, K.type0_hex(lines[1]))
    K.add_page(w, c, {"F1": f})
    return K.to_bytes(w)


MEMO_BODY = "The winter operations budget was approved by the council on Monday evening."


def memo_pdf(*, headings: int = 3, bold_run: bool = False) -> bytes:
    """Memo style: bold 11pt section heads over 11pt body text."""
    w = PdfWriter()
    f = K.helvetica(w)
    fb = K.helvetica(w, bold=True)
    c = b""
    y = 720
    for h in ["MEMORANDUM", "Purpose", "Background"][:headings]:
        c += K.bt("FB", 11, 72, y, K.lit(h))
        y -= 16
        if bold_run:  # a bold paragraph: emphasis, not a heading
            c += K.bt("FB", 11, 72, y, K.lit("This whole paragraph is set in bold for emphasis."))
            y -= 16
        for _ in range(3):
            c += K.bt("F1", 11, 72, y, K.lit(MEMO_BODY))
            y -= 14
        y -= 10
    K.add_page(w, c, {"F1": f, "FB": fb})
    return K.to_bytes(w)


def grid_pdf() -> bytes:
    """Prose plus two tiny two-row grids with a BOLD header row.

    Too short to be tagged as tables (a table needs >= 3 rows), so each bold
    header cell reaches the bold-heading test. The last bold cell of each
    header row is followed by a plain data cell — which used to make "Owner"
    a heading, twice. A heading stands alone on its line; a cell does not.
    """
    w = PdfWriter()
    f = K.helvetica(w)
    fb = K.helvetica(w, bold=True)
    c = b""
    y = 720
    for _g in range(2):
        for _ in range(3):
            c += K.bt("F1", 11, 72, y, K.lit(MEMO_BODY))
            y -= 14
        y -= 10
        c += K.bt("FB", 11, 72, y, K.lit("Item")) + K.bt("FB", 11, 300, y, K.lit("Owner"))
        y -= 14
        c += K.bt("F1", 11, 72, y, K.lit("Salt delivery")) + K.bt("F1", 11, 300, y, K.lit("Depot crew"))
        y -= 24
    K.add_page(w, c, {"F1": f, "FB": fb})
    return K.to_bytes(w)


def watermark_pdf() -> bytes:
    """A diagonal 72pt "DRAFT" on both pages (the largest text on each)."""
    w = PdfWriter()
    f = K.helvetica(w)
    for p in range(2):
        c = b"BT /F1 72 Tf 0.7071 0.7071 -0.7071 0.7071 150 300 Tm (DRAFT) Tj ET\n"
        if p == 0:
            c += K.bt("F1", 20, 72, 720, K.lit("Winter Operations Plan"))
        c += K.bt("F1", 14, 72, 690, K.lit("Section %d" % (p + 1)))
        for i in range(10):
            c += K.bt("F1", 11, 72, 660 - 14 * i, K.lit(BODY))
        K.add_page(w, c, {"F1": f})
    return K.to_bytes(w)


def contents_first_pdf(big: str = "Contents") -> bytes:
    """Page 1 is a contents page: its largest line is "Contents"."""
    w = PdfWriter()
    f = K.helvetica(w)
    c = K.bt("F1", 18, 72, 740, K.lit(big))
    for i, t in enumerate(["1. Introduction", "2. Salt Supply", "3. Plow Routes"]):
        c += K.bt("F1", 11, 72, 700 - 20 * i, K.lit(t))
    K.add_page(w, c, {"F1": f})
    K.add_page(w, b"".join(K.bt("F1", 11, 72, 700 - 14 * i, K.lit(BODY)) for i in range(12)), {"F1": f})
    return K.to_bytes(w)


def cairo_pdf() -> bytes:
    """Cairo/Skia style: every block is "1 Tf", the size lives in Tm."""
    w = PdfWriter()
    f = K.helvetica(w)
    c = b"BT /F1 1 Tf 20 0 0 20 72 720 Tm (Snow Route Handbook) Tj ET\n"
    c += b"BT /F1 1 Tf 15 0 0 15 72 690 Tm (Getting Started) Tj ET\n"
    for i in range(10):
        c += b"BT /F1 1 Tf 11 0 0 11 72 %d Tm (%s) Tj ET\n" % (660 - 14 * i, BODY.encode())
    K.add_page(w, c, {"F1": f})
    return K.to_bytes(w)


def remediate(data: bytes, name: str, *, title: bool = True):
    from app.parsers.pdf_parser import PDFParser
    from app.writers.pdf_writer import write_remediated_pdf

    src = Path(_TMP) / name
    src.write_bytes(data)
    res = PDFParser().parse(str(src))
    root = res.tree.root
    root.metadata.properties["tag_structure_requested"] = True
    if title:
        root.metadata.properties["title"] = root.metadata.properties.get("title_candidate") or "Report"
    root.metadata.language = "en-US"
    out = Path(_TMP) / ("out_" + name)
    wr = write_remediated_pdf(src, res.tree, out)
    return res, wr, out


def main() -> int:
    from app.parsers.pdf_parser import PDFParser
    from app.pdf.tag_reader import read_struct_info
    from app.services.remediation_engine import RemediationEngine

    check = K.Checker()
    data = report_pdf()
    res, wr, out = remediate(data, "report.pdf")
    ua = wr.get("pdfua") or {}
    props = res.tree.root.metadata.properties

    # 2. cover title
    check("cover title found (26pt among 26/14/10 on page 1)",
          props.get("title_candidate") == "Stormwater Utility Annual Report", repr(props.get("title_candidate")))

    # 1. heading levels
    r = PdfReader(str(out))
    info = read_struct_info(r)
    levels = [h["level"] for h in info["headings"]]
    check("headings found", len(levels) >= 5, str(info["headings"]))
    check("no heading level skips in the output", all(b <= a + 1 for a, b in zip(levels, levels[1:])), str(levels))
    by_text = {h["text"].strip(): h["level"] for h in info["headings"]}
    check("equal-size sibling sections get the SAME level (2.1 and 2.2)",
          by_text.get("2.1 Rate Structure") is not None
          and by_text.get("2.1 Rate Structure") == by_text.get("2.2 Capital Projects"), str(by_text))
    check("the subtitle under the title is H2, not H4", by_text.get("Fiscal Year 2026") == 2, str(by_text))
    check("the first heading is H1", levels[:1] == [1], str(levels))
    check("the tagger reports what it normalised", int(ua.get("headingLevelsNormalized") or 0) >= 1, str(ua))
    res_out = PDFParser().parse(str(out))
    rules = [v.rule_id for v in RemediationEngine().detect_violations(res_out.tree)]
    check("re-analysis: no HEADING_LEVEL_JUMP we created", "HEADING_LEVEL_JUMP" not in rules, str(rules))

    # 3. TOC
    check("bare TOC is not tagged as a table", int(ua.get("tocTablesDeclined") or 0) == 1, str(ua))
    check("only the real data table is a /Table", ua.get("tables") == 1, str(ua))
    check("re-analysis: no NEW TABLE_MISSING_HEADERS", "TABLE_MISSING_HEADERS" not in rules, str(rules))
    toc_tags = K.marked_tags(r, 1)
    check("dot leaders are /Artifact (4 of them)", toc_tags.count("/Artifact") >= 4, str(toc_tags))
    # Each contents row reads as ONE paragraph (entry + page number), not the
    # entry and then a bare "3": 4 rows on the leader page + 4 on the bare TOC.
    from pypdf.generic import ArrayObject as _Arr

    joined = [e for _d, s, e in K.struct_elems(r) if s == "/P" and isinstance(e.get("/K"), _Arr) and len(e["/K"]) == 2]
    check("contents rows are one /P each (entry + page number)",
          len(joined) == 8 and ua.get("tocRowsJoined") == 8, f"{len(joined)} {ua.get('tocRowsJoined')}")

    # 4. reading order disclosure
    check("paired two-column page: declined, counted, NOT fixed",
          ua.get("readingOrderDeclinedPages") == 1 and ua.get("readingOrderFixedPages") == 0, str(ua))
    reasons = " ".join(s.get("reason", "") for s in wr.get("skipped", []))
    check("...and disclosed in writer.skipped", "pdfua_reading_order_declined: 1 page(s)" in reasons, reasons[:400])

    # 5. /Pg on containers
    tables = info["tables"]
    check("the table on page 7 is read back on page 7 (index 6)", [t["page"] for t in tables] == [6], str(tables))
    tnodes = [n for n in _iter(res_out.tree.root) if type(n).__name__ == "TableNode"]
    check("the re-parsed TableNode is on page 7", [n.metadata.page for n in tnodes] == [7], str([n.metadata.page for n in tnodes]))
    tbl = [e for _d, s, e in K.struct_elems(r) if s == "/Table"]
    check("/Table carries /Pg", bool(tbl) and "/Pg" in tbl[0])
    # Locations of TAGGED containers (the re-parse reads them from tags): the
    # table's cells sit at x=72..312+, baselines y=680..632; the list's items
    # at x=90, y=520..488. Each is located from its own text, as one run.
    tb = tnodes[0].metadata.properties.get("bbox") if tnodes else None
    check("the tagged table is located (bbox spans its cells)",
          bool(tb) and 71 <= tb[0] <= 73 and tb[1] <= 632 and tb[3] >= 680 and tb[2] > 312, str(tb))
    lnodes = [n for n in _iter(res_out.tree.root) if type(n).__name__ == "ListNode"]
    lb = lnodes[0].metadata.properties.get("bbox") if lnodes else None
    check("the tagged list is located on its page",
          len(lnodes) == 1 and lnodes[0].metadata.page == 4 and bool(lb)
          and 89 <= lb[0] <= 91 and lb[1] <= 488 and lb[3] >= 520, f"{[n.metadata.page for n in lnodes]} {lb}")

    # 6. PDF/UA claim
    xmp = r.trailer["/Root"]["/Metadata"].get_object().get_data()
    check("not claimed: base-14 font not embedded + declined page", b"pdfuaid:part" not in xmp and ua.get("pdfuaClaimed") is False)
    check("the reasons are given", "pdfua_not_claimed" in reasons and "not embedded" in reasons, reasons[-300:])
    _res2, wr2, out2 = remediate(clean_pdf(), "clean.pdf")
    xmp2 = PdfReader(str(out2)).trailer["/Root"]["/Metadata"].get_object().get_data()
    check("claimed when every check passes", (wr2.get("pdfua") or {}).get("pdfuaClaimed") is True
          and b"<pdfuaid:part>1</pdfuaid:part>" in xmp2, str((wr2.get("pdfua") or {}).get("pdfuaBlockers")))
    _res3, wr3, out3 = remediate(clean_pdf(), "untitled.pdf", title=False)
    xmp3 = PdfReader(str(out3)).trailer["/Root"]["/Metadata"].get_object().get_data()
    check("...but not without a title", b"pdfuaid:part" not in xmp3
          and "the document has no title" in ((wr3.get("pdfua") or {}).get("pdfuaBlockers") or []))

    # 7. bold body-size headings (memo style)
    _rm, wm, outm = remediate(memo_pdf(), "memo.pdf")
    hm = read_struct_info(PdfReader(str(outm)))["headings"]
    check("memo: bold 11pt section heads become headings",
          [h["text"].strip() for h in hm] == ["MEMORANDUM", "Purpose", "Background"]
          and (wm.get("pdfua") or {}).get("boldHeadings") == 3, str(hm))
    _r1, w1, out1 = remediate(memo_pdf(headings=1), "memo1.pdf")
    check("a single bold line proves nothing: no heading",
          read_struct_info(PdfReader(str(out1)))["headings"] == [], str((w1.get("pdfua") or {}).get("boldHeadings")))
    _r2, _w2, out_b = remediate(memo_pdf(bold_run=True), "memo_bold_para.pdf")
    check("bold line followed by more bold (emphasis) is not a heading",
          read_struct_info(PdfReader(str(out_b)))["headings"] == [], str(read_struct_info(PdfReader(str(out_b)))["headings"]))
    # 8. a diagonal watermark is not the title, not H1 on every page, and
    #    (recurring) goes out of the reading order as an artifact
    rw, ww, out_w = remediate(watermark_pdf(), "watermark.pdf")
    check("watermark: the title candidate is the real title, not 'DRAFT'",
          rw.tree.root.metadata.properties.get("title_candidate") == "Winter Operations Plan",
          repr(rw.tree.root.metadata.properties.get("title_candidate")))
    hw = [(h["level"], h["text"].strip()) for h in read_struct_info(PdfReader(str(out_w)))["headings"]]
    check("watermark: never a heading", hw == [(1, "Winter Operations Plan"), (2, "Section 1"), (2, "Section 2")], str(hw))
    check("watermark: recurring stamp is an /Artifact on both pages",
          (ww.get("pdfua") or {}).get("watermarkArtifacts") == 2
          and all("/Artifact" in K.marked_tags(PdfReader(str(out_w)), i) for i in (0, 1)), str(ww.get("pdfua")))
    rc = PDFParser().parse(str(_write(contents_first_pdf(), "contents_first.pdf")))
    check("a contents page's 'Contents' is never offered as the document title",
          not rc.tree.root.metadata.properties.get("title_candidate"),
          repr(rc.tree.root.metadata.properties.get("title_candidate")))
    for big in ("Chapter 1: Programme Area 1", "2.1 Rate Structure"):
        rb = PDFParser().parse(str(_write(contents_first_pdf(big), "first_section.pdf")))
        check(f"the first SECTION's name ({big!r}) is not offered as the document title",
              not rb.tree.root.metadata.properties.get("title_candidate"),
              repr(rb.tree.root.metadata.properties.get("title_candidate")))

    # 9. sizes carried in the text matrix (Cairo/Skia "1 Tf" + "20 0 0 20 Tm")
    rcai, _wc, out_c = remediate(cairo_pdf(), "cairo.pdf")
    check("Tm-scaled text: the title is found",
          rcai.tree.root.metadata.properties.get("title_candidate") == "Snow Route Handbook",
          repr(rcai.tree.root.metadata.properties.get("title_candidate")))
    hc = [(h["level"], h["text"].strip()) for h in read_struct_info(PdfReader(str(out_c)))["headings"]]
    check("Tm-scaled text: headings are ranked by their real size",
          hc == [(1, "Snow Route Handbook"), (2, "Getting Started")], str(hc))

    _rg, wg, out_g = remediate(grid_pdf(), "bold_grid.pdf")
    hg = read_struct_info(PdfReader(str(out_g)))["headings"]
    check("a bold header CELL sharing its line with another cell is not a heading",
          hg == [] and not (wg.get("pdfua") or {}).get("boldHeadings"), str(hg))

    # already-tagged: carry the author's claim over, never add one
    from app.pdf.ua_tagger import _xmp_packet, tag_pdf

    for prior, expect in ((1, True), (None, False)):
        w = PdfWriter(clone_from=PdfReader(io.BytesIO(out2.read_bytes() if prior else out.read_bytes())))
        rep = tag_pdf(w, res.tree)
        buf = io.BytesIO()
        w.write(buf)
        x = PdfReader(buf).trailer["/Root"]["/Metadata"].get_object().get_data()
        check(f"already-tagged doc {'with' if prior else 'without'} a prior claim -> claim {'kept' if expect else 'absent'}",
              rep.get("alreadyTagged") is True and ((b"pdfuaid:part" in x) == expect), str(rep))
    return check.done()


def _write(data: bytes, name: str) -> Path:
    p = Path(_TMP) / name
    p.write_bytes(data)
    return p


def _iter(node):
    yield node
    for c in getattr(node, "children", []) or []:
        yield from _iter(c)


if __name__ == "__main__":
    sys.exit(main())
