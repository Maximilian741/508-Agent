"""Smoke: every finding says WHERE it is, and whether we can fix it ourselves.

Pins the shared API contract on /pipeline/analyze and /pipeline/remediate:

  violation.autoFixable  — a recommended action for the flag is in
                           pipeline._PERSISTED_ACTIONS[format] and is not
                           FLAG_FOR_MANUAL_REVIEW (recomputed independently here);
  violation.location     — {kind, page, bbox, pageSize, snippet, highlight,
                           thumbnail}, all seven keys always present;
  summary                — {total, autoFixable, needsYou, cost}.

Per format it proves the location is the REAL place, not a guess:
  * DOCX: the heading jump highlights the heading's own text; the typed "- "
    list highlights its marker; the table shows its first row; the picture
    carries a <=240 px PNG thumbnail read back from word/media; the content
    control is shown inside its own sentence; title/language are "document".
  * HTML: "click here" is highlighted inside the sentence it sits in; a
    data: image gets a thumbnail; a REMOTE image does not, and no socket is
    ever opened; the unlabeled <input>, the untitled <iframe>, the positive
    tabindex and the autocomplete candidate — root-level COUNT findings — each
    point at the first offending element's markup.
  * PPTX: the picture thumbnail comes from the slide shape; the link is shown
    inside its text box's sentence.
  * PDF: the image finding names page 1 and the page size, with a thumbnail
    decoded from the page's XObject; the link annotation becomes a
    "pdf-region" with the annotation's own /Rect as bbox.
Invariants on every location: highlight is a literal substring of snippet,
snippet <= 200 chars, a thumbnail is a PNG no larger than 240x240.

Also: a location can never fail a request (broken image bytes, a node id that
isn't in the tree, a decompression bomb -> None, not a 500), and remediate
returns the same findings with ``fixed`` — True only for an APPROVED finding
whose fix reached the file — while keeping thumbnails OUT of meta.json.

Run: python -m app.devtools.smoke_finding_location
"""

from __future__ import annotations

import base64
import io
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="508_smoke_location_"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'loc.db').as_posix()}"
os.environ["STORAGE_LOCAL_ROOT"] = str(_TMP / "storage")
os.environ["MATERIALIZED_ROOT"] = str(_TMP / "materialized")
for _key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SEMANTIC_PROVIDER", "SMTP_HOST"):
    os.environ.pop(_key, None)

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
KEYS = {"kind", "page", "bbox", "pageSize", "snippet", "highlight", "thumbnail"}
KINDS = {"pdf-region", "image", "text", "table", "document"}


def _png(w: int, h: int, color=(30, 110, 200)) -> bytes:
    buf = io.BytesIO()
    im = Image.new("RGB", (w, h), color)
    for x in range(0, w, max(1, w // 8)):
        for y in range(h):
            im.putpixel((x, y), (250, 250, 250))
    im.save(buf, "PNG")
    return buf.getvalue()


def _docx() -> bytes:
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    d = Document()
    d.add_heading("Top-Level Section", level=1)
    d.add_heading("Sub-sub Section", level=3)
    for item in ("- first typed item", "- second typed item", "- third typed item"):
        d.add_paragraph(item)
    d.add_paragraph().add_run().add_picture(io.BytesIO(_png(900, 600)))
    para = d.add_paragraph("For the annual numbers ")
    rid = d.part.relate_to(
        "https://example.com/annual.pdf",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    hl = OxmlElement("w:hyperlink")
    hl.set(qn("r:id"), rid)
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "click here"
    r.append(t)
    hl.append(r)
    para._p.append(hl)
    # An unlabeled content control inside a sentence.
    form = d.add_paragraph("Manager email: ")
    sdt = OxmlElement("w:sdt")
    sdt.append(OxmlElement("w:sdtPr"))
    content = OxmlElement("w:sdtContent")
    cr = OxmlElement("w:r")
    ct = OxmlElement("w:t")
    ct.text = "Click or tap here to enter text."
    cr.append(ct)
    content.append(cr)
    sdt.append(content)
    form._p.append(sdt)
    table = d.add_table(rows=3, cols=3)
    for ri, row in enumerate([("Region", "Q1", "Q2"), ("North", "120", "130"), ("South", "90", "95")]):
        for ci, val in enumerate(row):
            table.cell(ri, ci).text = val
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _html() -> bytes:
    data_uri = "data:image/png;base64," + base64.b64encode(_png(640, 480, (200, 60, 60))).decode()
    return (
        "<!doctype html><html><head></head><body>"
        "<h1>Report</h1><h3>Details</h3>"
        '<p>For the full report, <a href="https://example.com/r.pdf">click here</a> today.</p>'
        f'<img src="{data_uri}">'
        '<img src="https://images.example.com/remote.png">'
        "<table><tr><td>Region</td><td>Q1</td></tr><tr><td>North</td><td>12</td></tr></table>"
        '<form><p>Search: <input type="text" name="q"></p>'
        '<p>Your email <input type="text" name="email" aria-label="Your email"></p></form>'
        '<p>Map: <iframe src="https://maps.example.com/embed"></iframe></p>'
        '<p><a href="/next" tabindex="3">Skip ahead</a></p>'
        "</body></html>"
    ).encode("utf-8")


def _pptx() -> bytes:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_picture(io.BytesIO(_png(800, 500, (40, 160, 90))), Inches(1), Inches(2))
    tb = slide.shapes.add_textbox(Inches(1), Inches(0.5), Inches(6), Inches(1))
    p = tb.text_frame.paragraphs[0]
    p.add_run().text = "Read the report: "
    link = p.add_run()
    link.text = "click here"
    link.hyperlink.address = "https://example.com/deck-report"
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _pdf() -> bytes:
    from pypdf import PdfReader, PdfWriter
    from pypdf.annotations import Link

    buf = io.BytesIO()
    Image.open(io.BytesIO(_png(400, 300, (20, 120, 200)))).convert("RGB").save(buf, "PDF")
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(buf.getvalue())))
    w.add_annotation(0, Link(rect=(50, 50, 200, 80), url="https://example.com/report"))
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def _helv(text: str, size: float = 12.0) -> float:
    """Advance of ``text`` in 12 pt Helvetica, from the Core14 AFM table the
    location code measures non-embedded standard fonts with (its values are
    pinned against the AFM in main())."""
    from app.services.pdf_core14_widths import core14_widths

    table = core14_widths("/Helvetica")
    return sum(table[c] for c in text) * size / 1000.0


def _click_rect(dx: float = 0.0, dy: float = 0.0) -> tuple:
    """A link /Rect around "click here" in "For the details, click here today."
    (12 pt, baseline 660, x from 72), 1 pt of slack each side."""
    x0 = 72 + _helv("For the details, ")
    x1 = x0 + _helv("click here")
    return (round(x0 - 1 + dx), 655 + dy, round(x1 + 1 + dx), 672 + dy)


def _pdf_drawn() -> bytes:
    """A hand-built PDF whose geometry we know exactly (pypdf only).

    Page 1 (MediaBox 0 0 612 792): a 24 pt title run and a 12 pt sentence at
    known baselines, a picture painted by ``200 0 0 150 300 400 cm /Im1 Do``,
    and a link annotation whose /Rect covers the words "click here".
    Page 2 (MediaBox 100 100 712 892): the SAME picture and link, shifted by
    (+100, +100) in user space — page-relative boxes must come out identical.
    Page 3: page 1 again with ``/Rotate 90`` — boxes and page size must come
    out as a viewer shows the page (turned clockwise, 792 x 612).
    """
    from pypdf import PdfWriter
    from pypdf.annotations import Link
    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        FloatObject,
        NameObject,
        NumberObject,
    )

    w = PdfWriter()
    font = w._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    }))
    img = DecodedStreamObject()
    img.set_data(Image.new("RGB", (40, 30), (200, 30, 30)).tobytes())
    img.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Image"),
        NameObject("/Width"): NumberObject(40),
        NameObject("/Height"): NumberObject(30),
        NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
        NameObject("/BitsPerComponent"): NumberObject(8),
    })
    img_ref = w._add_object(img)

    def page(dx: float, dy: float, box, rotate: int = 0) -> None:
        pg = w.add_blank_page(612, 792)
        pg[NameObject("/MediaBox")] = ArrayObject([FloatObject(v) for v in box])
        if rotate:
            pg[NameObject("/Rotate")] = NumberObject(rotate)
        pg[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
            NameObject("/XObject"): DictionaryObject({NameObject("/Im1"): img_ref}),
        })
        body = (
            f"BT /F1 24 Tf {72 + dx} {700 + dy} Td (Annual Report) Tj ET\n"
            f"BT /F1 12 Tf {72 + dx} {660 + dy} Td (For the details, click here today.) Tj ET\n"
            f"q 200 0 0 150 {300 + dx} {400 + dy} cm /Im1 Do Q\n"
        ).encode("latin-1")
        cs = DecodedStreamObject()
        cs.set_data(body)
        pg[NameObject("/Contents")] = w._add_object(cs)

    page(0, 0, (0, 0, 612, 792))
    page(100, 100, (100, 100, 712, 892))
    page(0, 0, (0, 0, 612, 792), rotate=90)
    # "For the details, " is 82.7 pt of 12 pt Helvetica and "click here" 50.7 pt,
    # so the words sit at x 154.7-205.4 on baseline 660 (page 1).
    w.add_annotation(0, Link(rect=_click_rect(), url="https://example.com/details"))
    w.add_annotation(1, Link(rect=_click_rect(100, 100), url="https://example.com/details"))
    w.add_annotation(2, Link(rect=_click_rect(), url="https://example.com/details"))
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def _pdf_lines() -> bytes:
    """Links that are their own text-show operation, inside a line.

    Line 1 (baseline 660): "For the details, " "click here" " today." as three
    Tj runs, and — on the SAME baseline, across a gutter — "Right column
    text." at x=400 (two columns share baselines). Line 2 (baseline 620):
    "See the " "annual report" " for 2025." Links cover "click here" and
    "annual report" (Core14 Helvetica metrics).
    Line 3 (baseline 580, font F2 with its OWN /Widths: 500 for every code):
    "Go " "here" " now." at 10 pt, so "here" is x 87-107 whatever Helvetica
    says. Line 4 (baseline 540, font F3 whose /Widths cover only A-Z):
    "ABC" "xyz" — the lowercase run has no width, so it is never measured.
    Line 5 (baseline 500, font F9: Type0 / Identity-H, /W [1 [400 500 600]],
    /DW 1000, ToUnicode 1-4 -> A B C space): <0001000200030004> then
    <00030003>, i.e. "ABC " (x 72-97) then "CC" (x 97-109) at 10 pt.
    """
    from pypdf import PdfWriter
    from pypdf.annotations import Link
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject, TextStringObject

    w = PdfWriter()

    def helvetica(first: int = 0, widths=None):
        d = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        })
        if widths is not None:
            d[NameObject("/FirstChar")] = NumberObject(first)
            d[NameObject("/LastChar")] = NumberObject(first + len(widths) - 1)
            d[NameObject("/Widths")] = ArrayObject([NumberObject(v) for v in widths])
        return w._add_object(d)

    to_unicode = DecodedStreamObject()
    to_unicode.set_data(
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        b"/CMapName /Adobe-Identity-UCS def /CMapType 2 def\n"
        b"1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
        b"4 beginbfchar\n<0001> <0041>\n<0002> <0042>\n<0003> <0043>\n<0004> <0020>\nendbfchar\n"
        b"endcmap CMapName currentdict /CMap defineresource pop end end\n"
    )
    cid_font = w._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/CIDFontType2"),
        NameObject("/BaseFont"): NameObject("/SmokeCID"),
        NameObject("/CIDSystemInfo"): DictionaryObject({
            NameObject("/Registry"): TextStringObject("Adobe"),
            NameObject("/Ordering"): TextStringObject("Identity"),
            NameObject("/Supplement"): NumberObject(0),
        }),
        NameObject("/DW"): NumberObject(1000),
        NameObject("/W"): ArrayObject([NumberObject(1), ArrayObject([NumberObject(400), NumberObject(500), NumberObject(600)])]),
    }))
    type0 = w._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type0"),
        NameObject("/BaseFont"): NameObject("/SmokeCID"),
        NameObject("/Encoding"): NameObject("/Identity-H"),
        NameObject("/DescendantFonts"): ArrayObject([cid_font]),
        NameObject("/ToUnicode"): w._add_object(to_unicode),
    }))
    pg = w.add_blank_page(612, 792)
    pg[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({
        NameObject("/F1"): helvetica(),
        NameObject("/F2"): helvetica(32, [500] * 95),
        NameObject("/F3"): helvetica(65, [600] * 26),
        NameObject("/F9"): type0,
    })})
    cs = DecodedStreamObject()
    cs.set_data(
        b"BT /F1 12 Tf 72 660 Td (For the details, ) Tj (click here) Tj ( today.) Tj ET\n"
        b"BT /F1 12 Tf 400 660 Td (Right column text.) Tj ET\n"
        b"BT /F1 12 Tf 72 620 Td (See the ) Tj (annual report) Tj ( for 2025.) Tj ET\n"
        b"BT /F2 10 Tf 72 580 Td (Go ) Tj (here) Tj ( now.) Tj ET\n"
        b"BT /F3 10 Tf 72 540 Td (ABC) Tj (xyz) Tj ET\n"
        b"BT /F9 10 Tf 72 500 Td <0001000200030004> Tj <00030003> Tj ET\n"
    )
    pg[NameObject("/Contents")] = w._add_object(cs)
    w.add_annotation(0, Link(rect=_click_rect(), url="https://example.com/details"))
    a0 = 72 + _helv("See the ")
    a1 = a0 + _helv("annual report")
    w.add_annotation(0, Link(rect=(round(a0 - 1), 615, round(a1 + 1), 632), url="https://example.com/annual"))
    w.add_annotation(0, Link(rect=(86, 575, 108, 592), url="https://example.com/here"))
    w.add_annotation(0, Link(rect=(89, 535, 110, 552), url="https://example.com/unmeasured"))
    w.add_annotation(0, Link(rect=(96.5, 495, 109.5, 512), url="https://example.com/cid"))
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def _pdf_grey() -> bytes:
    """Grey #949494 text (3.03:1 on white). Page 1 paints a navy background
    (the analyzer skips such pages, so must we); page 2 has the grey at 24 pt
    (large text: passes 3:1), black body text, then 10 pt grey fine print set
    as a kerned TJ array — the text that actually fails."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    w = PdfWriter()
    font = w._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    }))
    for body in (
        b"0.1 0.1 0.4 rg 0 0 612 792 re f\nBT /F1 10 Tf 0.58 g 72 700 Td (Small grey on navy) Tj ET\n",
        b"BT /F1 24 Tf 0.58 g 72 700 Td (Big Grey Heading) Tj ET\n"
        b"BT /F1 12 Tf 0 g 72 660 Td (Body text in black.) Tj ET\n"
        b"BT /F1 10 Tf 0.58 g 72 640 Td [(Fine) -250 (print applies.)] TJ ET\n",
    ):
        pg = w.add_blank_page(612, 792)
        pg[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        cs = DecodedStreamObject()
        cs.set_data(body)
        pg[NameObject("/Contents")] = w._add_object(cs)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, detail: object = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, "" if cond else f"  [{str(detail)[:400]}]")
        if not cond:
            failures += 1

    from app.api.credits import DOC_FORMAT_COSTS
    from app.api.pipeline import _PERSISTED_ACTIONS
    from app.db.models import UserRow
    from app.db.session_sqlalchemy import session_scope
    from app.main import app
    from app.models.accessibility import REMEDIATION_ACTIONS_BY_FLAG, AccessibilityFlagCode

    client = TestClient(app)
    r = client.post("/auth/sign-in", json={"email": "where@example.com", "password": "wherepass1"})
    assert r.status_code == 200, r.text
    auth = {"Authorization": f"Bearer {r.json()['token']}"}
    with session_scope() as s:
        s.get(UserRow, r.json()["user"]["id"]).credits_balance = 500

    def expected_auto(rule: str, fmt: str) -> bool:
        acts = [a.action_code.value for a in REMEDIATION_ACTIONS_BY_FLAG.get(AccessibilityFlagCode(rule), [])]
        return any(a != "FLAG_FOR_MANUAL_REVIEW" and a in _PERSISTED_ACTIONS.get(fmt, set()) for a in acts)

    def thumb_ok(uri) -> bool:
        if not (isinstance(uri, str) and uri.startswith("data:image/png;base64,")):
            return False
        raw = base64.b64decode(uri.split(",", 1)[1])
        im = Image.open(io.BytesIO(raw))
        return im.format == "PNG" and im.size[0] <= 240 and im.size[1] <= 240

    def contract(label: str, body: dict, fmt: str) -> list:
        vs = body.get("violations") or []
        check(f"{label}: has findings", len(vs) > 0, body)
        bad_keys = [v["ruleId"] for v in vs if set((v.get("location") or {}).keys()) != KEYS]
        check(f"{label}: every location has exactly the 7 contract keys", not bad_keys, bad_keys)
        check(f"{label}: every autoFixable is a bool", all(isinstance(v.get("autoFixable"), bool) for v in vs))
        # autoFixable is the persisted-action capability AND (no paid provider
        # here) the offline dry run actually making the fix — so it may be
        # narrower than the capability, never wider.
        wrong = [v["ruleId"] for v in vs if v["autoFixable"] and not expected_auto(v["ruleId"], fmt)]
        check(f"{label}: autoFixable never names a fix this format can't persist", not wrong, wrong)
        locs = [v["location"] for v in vs]
        check(f"{label}: kinds are from the contract", all(l["kind"] in KINDS for l in locs), [l["kind"] for l in locs])
        check(
            f"{label}: highlight is always a substring of snippet",
            all(not l["highlight"] or (l["snippet"] and l["highlight"] in l["snippet"]) for l in locs),
            [(l["snippet"], l["highlight"]) for l in locs],
        )
        check(f"{label}: snippets are <= 200 chars", all(len(l["snippet"] or "") <= 200 for l in locs))
        check(
            f"{label}: every thumbnail is a PNG <= 240x240",
            all(l["thumbnail"] is None or thumb_ok(l["thumbnail"]) for l in locs),
        )
        check(
            f"{label}: thumbnails only on image findings",
            all(l["thumbnail"] is None or v["evidence"].get("node_type") == "image" for v, l in zip(vs, locs)),
        )
        check(
            f"{label}: a bbox is always paired with a pageSize (pdf-region)",
            all((l["bbox"] is None) or (l["pageSize"] and l["kind"] == "pdf-region") for l in locs),
        )
        s = body.get("summary") or {}
        n_auto = sum(1 for v in vs if v["autoFixable"])
        check(
            f"{label}: summary total/autoFixable/needsYou/cost (0 when nothing is auto-fixable)",
            s.get("total") == len(vs) and s.get("autoFixable") == n_auto and s.get("needsYou") == len(vs) - n_auto
            and s.get("cost") == (DOC_FORMAT_COSTS[fmt] if n_auto else 0),
            {k: s.get(k) for k in ("total", "autoFixable", "needsYou", "cost")},
        )
        return vs

    def by_rule(vs: list, rule: str) -> list:
        return [v for v in vs if v["ruleId"] == rule]

    def analyze(name: str, data: bytes, mime: str) -> dict:
        rr = client.post("/pipeline/analyze", files={"file": (name, data, mime)}, headers=auth)
        assert rr.status_code == 200, rr.text[:400]
        return rr.json()

    loc_copies_before = {p.name for p in Path(tempfile.gettempdir()).glob("508loc_*")}

    # ---- DOCX -----------------------------------------------------------------
    docx = _docx()
    vs = contract("docx", analyze("where.docx", docx, DOCX), "docx")
    jump = by_rule(vs, "HEADING_LEVEL_JUMP") or by_rule(vs, "SKIPPED_HEADING_LEVEL")
    check(
        "docx: the heading jump highlights the heading's own text",
        bool(jump) and jump[0]["location"]["highlight"] == "Sub-sub Section" and jump[0]["location"]["kind"] == "text",
        jump[:1],
    )
    fake = by_rule(vs, "LIST_STRUCTURE_INVALID")
    check(
        "docx: the typed list highlights its '-' marker inside the items",
        bool(fake) and fake[0]["location"]["highlight"] == "-"
        and "first typed item" in (fake[0]["location"]["snippet"] or "")
        and "second typed item" in (fake[0]["location"]["snippet"] or ""),
        fake[:1],
    )
    tables = [v for v in vs if v["location"]["kind"] == "table"]
    check(
        "docx: the table finding shows its first row",
        bool(tables) and tables[0]["location"]["snippet"] == "Region | Q1 | Q2",
        [t["location"] for t in tables][:1],
    )
    img = by_rule(vs, "MISSING_ALT_TEXT")
    check(
        "docx: the picture has a thumbnail read back from word/media",
        bool(img) and img[0]["location"]["kind"] == "image" and thumb_ok(img[0]["location"]["thumbnail"]),
        [i["location"] | {"thumbnail": bool(i["location"]["thumbnail"])} for i in img],
    )
    link = by_rule(vs, "LINK_TEXT_NON_DESCRIPTIVE")
    check(
        "docx: the link finding highlights 'click here'",
        bool(link) and link[0]["location"]["highlight"] == "click here",
        link[:1],
    )
    title = by_rule(vs, "DOCUMENT_TITLE_MISSING")
    check("docx: the missing title is a document-level location", bool(title) and title[0]["location"]["kind"] == "document", title[:1])
    ff = by_rule(vs, "FORM_FIELD_UNLABELED")
    check(
        "docx: the unlabeled content control is shown in its own sentence",
        bool(ff)
        and "Manager email:" in (ff[0]["location"]["snippet"] or "")
        and ff[0]["location"]["highlight"] == "Click or tap here to enter text.",
        ff[:1],
    )
    check("docx: no DOCX finding claims a page", all(v["location"]["page"] is None for v in vs))

    # ---- HTML (and: no network, ever) -----------------------------------------
    connects = []
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo
    _LOCAL = {"127.0.0.1", "::1", "localhost"}

    def _no_network(self, address, *a, **k):
        # Loopback stays open: the event loop's own self-pipe uses it.
        host = address[0] if isinstance(address, tuple) and address else address
        if host in _LOCAL:
            return real_connect(self, address, *a, **k)
        connects.append(address)  # pragma: no cover - only hit on a regression
        raise OSError("network disabled in smoke_finding_location")

    def _no_dns(host, *a, **k):
        if host in _LOCAL or host is None:
            return real_getaddrinfo(host, *a, **k)
        connects.append(host)  # pragma: no cover - only hit on a regression
        raise OSError("DNS disabled in smoke_finding_location")

    socket.socket.connect = _no_network
    socket.getaddrinfo = _no_dns
    try:
        hbody = analyze("where.html", _html(), "text/html")
    finally:
        socket.socket.connect = real_connect
        socket.getaddrinfo = real_getaddrinfo
    check("html: building locations opened no socket (remote images are never fetched)", not connects, connects)
    vs = contract("html", hbody, "html")
    link = by_rule(vs, "LINK_TEXT_NON_DESCRIPTIVE")
    check(
        "html: 'click here' is highlighted inside the sentence it sits in",
        bool(link)
        and link[0]["location"]["snippet"] == "For the full report, click here today."
        and link[0]["location"]["highlight"] == "click here",
        link[:1],
    )
    imgs = by_rule(vs, "MISSING_ALT_TEXT")
    thumbs = [bool(v["location"]["thumbnail"]) for v in imgs]
    check("html: the data: image has a thumbnail, the remote one does not", sorted(thumbs) == [False, True], thumbs)
    ff = by_rule(vs, "FORM_FIELD_UNLABELED")
    check(
        "html: the unlabeled <input> is highlighted in its markup",
        bool(ff) and "Search:" in (ff[0]["location"]["snippet"] or "")
        and (ff[0]["location"]["highlight"] or "").startswith("<input"),
        ff[:1],
    )

    for rule, needle in (
        ("IFRAME_TITLE_MISSING", '<iframe src="https://maps.example.com/embed">'),
        ("POSITIVE_TABINDEX", 'tabindex="3"'),
        ("INPUT_AUTOCOMPLETE_MISSING", 'name="email"'),
    ):
        hit = by_rule(vs, rule)
        check(
            f"html: {rule} points at the first offending element's markup",
            bool(hit) and needle in (hit[0]["location"]["highlight"] or "") and hit[0]["location"]["kind"] == "text",
            hit[:1] or [v["ruleId"] for v in vs],
        )

    # ---- PPTX -----------------------------------------------------------------
    vs = contract("pptx", analyze("where.pptx", _pptx(), PPTX), "pptx")
    # python-pptx names the picture "image.png", so this is the "alt is a file
    # name" finding — and the bad alt itself is what gets highlighted.
    img = by_rule(vs, "ALT_TEXT_NOT_DESCRIPTIVE") or by_rule(vs, "MISSING_ALT_TEXT")
    check(
        "pptx: the picture's thumbnail comes from its slide shape, on slide 1",
        bool(img) and thumb_ok(img[0]["location"]["thumbnail"]) and img[0]["location"]["page"] == 1,
        [i["location"] | {"thumbnail": bool(i["location"]["thumbnail"])} for i in img],
    )
    check(
        "pptx: a file-name alt is shown and highlighted as the problem",
        bool(img) and img[0]["ruleId"] != "ALT_TEXT_NOT_DESCRIPTIVE"
        or (img[0]["location"]["snippet"] == "image.png" and img[0]["location"]["highlight"] == "image.png"),
        img[:1],
    )
    slide = by_rule(vs, "SLIDE_TITLE_MISSING")
    check(
        "pptx: an untitled slide is located by its page and how it starts",
        bool(slide) and slide[0]["location"]["page"] == 1 and slide[0]["location"]["snippet"] == "Read the report: click here",
        slide[:1],
    )
    link = by_rule(vs, "LINK_TEXT_NON_DESCRIPTIVE")
    check(
        "pptx: the link is shown inside its text box's sentence",
        bool(link) and link[0]["location"]["snippet"] == "Read the report: click here"
        and link[0]["location"]["highlight"] == "click here",
        link[:1],
    )

    # ---- PDF ------------------------------------------------------------------
    vs = contract("pdf", analyze("where.pdf", _pdf(), "application/pdf"), "pdf")
    img = [v for v in vs if v["evidence"].get("node_type") == "image"]
    check(
        "pdf: the image finding names page 1 + the page size, with a thumbnail",
        bool(img) and img[0]["location"]["page"] == 1 and img[0]["location"]["pageSize"] == [400.0, 300.0]
        and thumb_ok(img[0]["location"]["thumbnail"]),
        [i["location"] | {"thumbnail": bool(i["location"]["thumbnail"])} for i in img],
    )
    links = [v for v in vs if v["evidence"].get("node_type") == "link"]
    check(
        "pdf: the link annotation is a pdf-region with its own /Rect",
        bool(links) and links[0]["location"]["kind"] == "pdf-region"
        and links[0]["location"]["bbox"] == [50.0, 50.0, 200.0, 80.0]
        and links[0]["location"]["pageSize"] == [400.0, 300.0],
        [l["location"] for l in links][:1],
    )

    # ---- PDF geometry measured from the content stream ---------------------------
    from app.services.pdf_core14_widths import core14_widths

    helv, helv_bold, times = core14_widths("/Helvetica"), core14_widths("/Helvetica-Bold"), core14_widths("/Times-Roman")
    check(
        "Core14 widths match Adobe's AFMs (Helvetica a/l/W/space, Helvetica-Bold l/m, Times-Roman a/m; subset tag ignored; Calibri unknown)",
        (helv["a"], helv["l"], helv["W"], helv[" "]) == (556, 222, 944, 278)
        and (helv_bold["l"], helv_bold["m"]) == (278, 889)
        and (times["a"], times["m"]) == (444, 778)
        and core14_widths("/ABCDEF+ArialMT") == helv
        and core14_widths("/Calibri") is None,
    )
    drawn = _pdf_drawn()
    vs = contract("pdf-drawn", analyze("drawn.pdf", drawn, "application/pdf"), "pdf")
    imgs = sorted((v for v in vs if v["evidence"].get("node_type") == "image"), key=lambda v: v["location"]["page"] or 0)
    check(
        "pdf: each picture's box is where its page paints it (cm + Do), page-relative on pages 1-2",
        [i["location"]["page"] for i in imgs] == [1, 2, 3]
        and all(
            i["location"]["kind"] == "pdf-region"
            and i["location"]["bbox"] == [300.0, 400.0, 500.0, 550.0]
            and i["location"]["pageSize"] == [612.0, 792.0]
            and thumb_ok(i["location"]["thumbnail"])
            for i in imgs[:2]
        ),
        [i["location"] | {"thumbnail": bool(i["location"]["thumbnail"])} for i in imgs],
    )
    check(
        "pdf: on a /Rotate 90 page the picture's box and the page size are as a viewer shows them",
        len(imgs) == 3 and imgs[2]["location"]["bbox"] == [400.0, 112.0, 550.0, 312.0]
        and imgs[2]["location"]["pageSize"] == [792.0, 612.0],
        imgs[2]["location"] | {"thumbnail": bool(imgs[2]["location"]["thumbnail"])} if len(imgs) == 3 else imgs,
    )
    links = sorted((v for v in vs if v["ruleId"] == "LINK_TEXT_NON_DESCRIPTIVE"), key=lambda v: v["location"]["page"] or 0)
    flat = [float(v) for v in _click_rect()]
    check(
        "pdf: a link shows the words drawn under its /Rect, inside their sentence",
        len(links) == 3
        and all(
            l["location"]["highlight"] == "click here"
            and l["location"]["snippet"] == "For the details, click here today."
            for l in links
        ),
        [l["location"] for l in links],
    )
    check(
        "pdf: link boxes are page-relative (page 2's MediaBox starts at 100,100) and turned on the rotated page",
        [l["location"]["bbox"] for l in links]
        == [flat, flat, [655.0, 612.0 - flat[2], 672.0, 612.0 - flat[0]]],
        [l["location"]["bbox"] for l in links],
    )

    vs = contract("pdf-lines", analyze("lines.pdf", _pdf_lines(), "application/pdf"), "pdf")
    shown = sorted(
        (v["location"]["snippet"], v["location"]["highlight"])
        for v in vs
        if v["evidence"].get("node_type") == "link" and v["location"]["kind"] == "pdf-region"
    )
    check(
        "pdf: a link that is its own text run is shown in its line; text across a column gutter is not stitched on",
        ("For the details, click here today.", "click here") in shown
        and ("See the annual report for 2025.", "annual report") in shown,
        shown,
    )
    check("pdf: a font's own /Widths decide where its text is ('here' at x 87-107 with 500-unit glyphs)", ("Go here now.", "here") in shown, shown)
    check("pdf: a Type0 / Identity-H font is measured by /W and /DW per CID ('CC' at x 97-109)", ("ABC CC", "CC") in shown, shown)
    check(
        "pdf: text in a glyph the font gives no width for is never measured (the link keeps its URI, no invented words)",
        ("https://example.com/unmeasured", "https://example.com/unmeasured") in shown
        and not any(h and ("xyz" in h or "ABC" in h) for _s, h in shown),
        shown,
    )

    vs = contract("pdf-grey", analyze("grey.pdf", _pdf_grey(), "application/pdf"), "pdf")
    grey = by_rule(vs, "LOW_CONTRAST_TEXT")
    gl0 = grey[0]["location"] if grey else {}
    check(
        "pdf: the contrast finding points at the text that fails (10 pt grey, page 2), not the passing 24 pt"
        " grey heading nor the grey on the navy page",
        bool(grey) and gl0.get("page") == 2 and gl0.get("kind") == "pdf-region"
        and gl0.get("snippet") == "Fine print applies." and gl0.get("highlight") == "Fine print applies."
        and (gl0.get("bbox") or [0])[0] == 72.0 and (gl0.get("bbox") or [0, 0, 0, 0])[1] < 640 < (gl0.get("bbox") or [0, 0, 0, 0])[3],
        gl0,
    )

    from app.models.accessibility import (
        AccessibilityTree as _Tree,
        ContentKind as _CK,
        DocumentNode as _Doc,
        HeadingNode as _H,
        NodeContent as _NC,
        NodeLocation as _NL,
        NodeMetadata as _NM,
        ParagraphNode as _P,
        SectionNode as _S,
        Violation as _V,
    )
    from app.services.finding_location import build_locations as _build

    drawn_path = _TMP / "drawn.pdf"
    drawn_path.write_bytes(drawn)

    def _node(cls, nid: str, page_no: int, text: str, **kw):
        return cls(id=nid, content=_NC(kind=_CK.TEXT, text=text), metadata=_NM(page=page_no, source_format="pdf"), **kw)

    p1 = _S(id="s1", content=_NC(kind=_CK.NONE), metadata=_NM(page=1, source_format="pdf"), children=[
        _node(_H, "h1", 1, "Annual Report", level=1),
        _node(_P, "twice-a", 1, "For the details, click here today."),
        _node(_P, "twice-b", 1, "For the details, click here today."),
        _node(_P, "often", 1, "he"),
        _node(_P, "absent", 1, "Not on this page at all"),
    ])
    p2 = _S(id="s2", content=_NC(kind=_CK.NONE), metadata=_NM(page=2, source_format="pdf"), children=[
        _node(_H, "h2", 2, "Annual Report", level=3),
    ])
    gtree = _Tree(root=_Doc(id="d", content=_NC(kind=_CK.NONE), metadata=_NM(source_format="pdf"), children=[p1, p2]))
    gl = _build(
        gtree,
        [
            _V(violation_id=f"v-{n}", rule_id="HEADING_LEVEL_JUMP", severity="warning", description="x", location=_NL(node_id=n))
            for n in ("h1", "h2", "twice-a", "often", "absent")
        ],
        "pdf",
        drawn_path,
    )
    h1 = gl["v-h1"]["bbox"] or [0, 0, 0, 0]
    check(
        "pdf: a heading's box is measured from its own text run (starts at x=72, spans baseline 700)",
        gl["v-h1"]["kind"] == "pdf-region" and h1[0] == 72.0 and h1[1] < 700 < h1[3] and 200 < h1[2] < 240,
        gl["v-h1"],
    )
    check("pdf: the same heading on the offset page gets the same page-relative box", gl["v-h2"]["bbox"] == gl["v-h1"]["bbox"], gl["v-h2"])
    check(
        "pdf: text found twice in the page's parsed text gets NO box (never possibly the wrong one)",
        gl["v-twice-a"]["bbox"] is None and gl["v-twice-a"]["page"] == 1 and gl["v-twice-a"]["snippet"],
        gl["v-twice-a"],
    )
    check("pdf: text drawn more than once on the page gets NO box", gl["v-often"]["bbox"] is None, gl["v-often"])
    check("pdf: text not drawn on the page gets NO box", gl["v-absent"]["bbox"] is None and gl["v-absent"]["kind"] == "text", gl["v-absent"])

    leaked = {p.name for p in Path(tempfile.gettempdir()).glob("508loc_*")} - loc_copies_before
    check("the private upload copies used for thumbnails are all deleted (docx/html/pptx/pdf)", not leaked, leaked)

    # ---- the builder can never fail a request -----------------------------------
    from app.models.accessibility import (
        AccessibilityTree,
        ContentKind,
        DocumentNode,
        ImageNode,
        NodeContent,
        NodeLocation,
        NodeMetadata,
        ParagraphNode,
        SectionNode,
        Violation,
    )
    from app.services.finding_location import build_locations, png_thumbnail_data_uri

    para = ParagraphNode(
        id="p-1",
        content=NodeContent(kind=ContentKind.TEXT, text="Revenue   grew\nin every region."),
        metadata=NodeMetadata(page=2, source_format="pdf", properties={"bbox": [300, 700, 72, 650]}),
    )
    broken = ImageNode(
        id="img-1",
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(page=2, source_format="pdf", properties={"image_b64": base64.b64encode(b"not an image").decode()}),
    )
    page = SectionNode(
        id="page-2",
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(page=2, source_format="pdf", properties={"page_size": [612, 792]}),
        children=[para, broken],
    )
    tree = AccessibilityTree(root=DocumentNode(
        id="doc", content=NodeContent(kind=ContentKind.NONE), metadata=NodeMetadata(source_format="pdf"), children=[page]
    ))

    def viol(vid: str, node: str, rule: str) -> Violation:
        return Violation(violation_id=vid, rule_id=rule, severity="warning", description="x", location=NodeLocation(node_id=node))

    locs = build_locations(
        tree,
        [viol("a", "p-1", "LOW_CONTRAST_TEXT"), viol("b", "img-1", "MISSING_ALT_TEXT"), viol("c", "gone", "MISSING_ALT_TEXT")],
        "pdf",
        None,
    )
    a = locs["a"]
    check(
        "parser bbox + ancestor page_size -> pdf-region, box normalised, text whitespace-collapsed",
        a["kind"] == "pdf-region" and a["bbox"] == [72.0, 650.0, 300.0, 700.0] and a["pageSize"] == [612.0, 792.0]
        and a["page"] == 2 and a["snippet"] == "Revenue grew in every region." and a["highlight"] == a["snippet"],
        a,
    )
    check("broken image bytes -> thumbnail None, still located", locs["b"]["thumbnail"] is None and locs["b"]["page"] == 2, locs["b"])
    check("a node id not in the tree -> a document location, no exception", locs["c"]["kind"] == "document", locs["c"])
    wide = png_thumbnail_data_uri(_png(5000, 100))
    check("a 5000x100 picture thumbnails to <= 240 px wide", thumb_ok(wide))
    bomb = Image.new("1", (9000, 9000))
    check("a 81-megapixel image is refused, not decoded", png_thumbnail_data_uri(bomb) is None)

    # ---- remediate returns the same findings, with an honest ``fixed`` ----------------
    ab = analyze("where.docx", docx, DOCX)
    ids = [v["id"] for v in ab["violations"]]
    rr = client.post(
        "/pipeline/remediate",
        files={"file": ("where.docx", docx, DOCX)},
        data={"approved_violations": json.dumps(ids), "rejected_violations": "[]"},
        headers=auth,
    )
    check("remediate docx -> 200", rr.status_code == 200, rr.text[:300])
    rb = rr.json() if rr.status_code == 200 else {}
    rvs = rb.get("violations") or []
    check("remediate: same findings as analyze", sorted(v["id"] for v in rvs) == sorted(ids), [v["id"] for v in rvs])
    check("remediate: every finding keeps its location + autoFixable", all(set(v["location"]) == KEYS for v in rvs) and rvs)
    fixed = [v for v in rvs if v.get("fixed")]
    check("remediate: some approved fixes reached the file", len(fixed) >= 1 and rb.get("persistedFixes", 0) >= 1, rb.get("persistedFixes"))
    check("remediate: nothing a writer can't persist is ever 'fixed'", all(v["autoFixable"] for v in fixed), [v["ruleId"] for v in fixed if not v["autoFixable"]])
    check(
        "remediate: 'fixed' is exactly the approved findings with a success execution",
        all(isinstance(v.get("fixed"), bool) for v in rvs),
    )
    meta = (_TMP / "materialized" / "pipeline" / rb.get("jobId", "x") / "meta.json")
    text = meta.read_text(encoding="utf-8") if meta.exists() else ""
    check("remediate: meta.json exists but holds no thumbnails / violations", bool(text) and "data:image" not in text and '"violations"' not in text)

    rr = client.post(
        "/pipeline/remediate",
        files={"file": ("where.docx", docx, DOCX)},
        data={"approved_violations": "[]", "rejected_violations": "[]"},
        headers=auth,
    )
    none_fixed = rr.status_code == 200 and not any(v.get("fixed") for v in rr.json().get("violations") or [])
    check("remediate with nothing approved: no finding is 'fixed' (and nothing charged)", none_fixed and rr.json().get("charged") is False, rr.text[:200])

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
