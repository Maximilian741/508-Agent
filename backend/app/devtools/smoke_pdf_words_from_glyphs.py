"""Smoke: link names and form labels read off a PDF page are whole words, in
the text shapes real exporters write.

Before: words were cut per pypdf layout fragment, and a kerned TJ array or a
one-glyph-per-Tj stream is one fragment per element. Measured on real files:

* Microsoft Word 365 (Save as PDF): a link named "s t r ee t u se p er mit
  ru les" and one named "h e r e"; contents links "In t r od u c ti on 1";
* Chrome/Edge (Skia): "h e r e", "s n o w r o u t e p r i o r i t y ...";
* kerned labels (pdfTeX, InDesign): field /TU "A pplicant n ame" — charged.

The broken names were written into the output as /Link /Contents, and
"here" hidden in "h e r e" escaped LINK_TEXT_NON_DESCRIPTIVE, so the one
finding that mattered disappeared. pypdf's walker also never advanced the pen
past a shown string, so "(Click ) Tj (here) Tj" put "here" on top of "Click".

Each page below reproduces one producer's operator shape (from the real
exports; the real files carry the author's name, so they are not committed):

  1. Word 365: one BT + absolute Tm per run, TJ alternating 1-3 glyph CID
     strings with kerning numbers, each space its own element, full /W;
  2. Chrome/Skia: y-flipped cm, one Tj per glyph followed by Td, space glyph
     printed, /W omitting every glyph whose width equals /DW;
  3. a bold word mid-sentence: consecutive Tj with no repositioning;
  4. adjacent strings inside one TJ array, kerned inside words;
  5. kerned form labels.

Asserted through the real /pipeline routes: the words, the link names written
to the output, the "here" findings (3), and the field labels.

Usage:
    python -m app.devtools.smoke_pdf_words_from_glyphs
"""

from __future__ import annotations

import io
import sys

from app.devtools import _pdf_fixture_kit as K

_TMP = K.isolated_env("508_smoke_glyphwords_")

from pypdf import PdfReader, PdfWriter  # noqa: E402

from app.pdf.text_geometry import _AFM, _ASCII  # noqa: E402

W = dict(zip(_ASCII, _AFM["Helvetica"]))
WB = dict(zip(_ASCII, _AFM["Helvetica-Bold"]))
BODY = "Crews treat primary routes first, then secondary routes and school zones."
ALL_CHARS = _ASCII


def _body(font: str, y: float, n: int = 6, cid: bool = False) -> bytes:
    out = b""
    for i in range(n):
        op = K.type0_hex(BODY) if cid else K.lit(BODY)
        out += K.bt(font, 10, 72, y - 14 * i, op)
    return out


# ---- 1. Word 365 -------------------------------------------------------------

_KERNS = (13, -2, 5, -4, 27, 6, -3, 11)


def word_runs(runs, *, x: float, y: float, size: float):
    """Word's shape. Returns (content, [(x_start, x_end) per run])."""
    out = b""
    spans = []
    k = 0
    for text in runs:
        chunks, buf, want = [], "", 1
        for ch in text:
            if ch == " ":
                if buf:
                    chunks.append(buf)
                    buf = ""
                chunks.append(" ")
                continue
            buf += ch
            if len(buf) == want:
                chunks.append(buf)
                buf, want = "", (want % 3) + 1
        if buf:
            chunks.append(buf)
        elems = []
        pen = start = x
        end = x
        for i, chunk in enumerate(chunks):
            elems.append(K.type0_hex(chunk))
            pen += sum(W[c] for c in chunk) * size / 1000.0
            if chunk != " ":
                end = pen
            if i < len(chunks) - 1:
                kv = _KERNS[k % len(_KERNS)]
                k += 1
                elems.append(b"%d" % kv)
                pen -= kv / 1000.0 * size
        out += b"BT /F1 %g Tf 1 0 0 1 %.3f %.3f Tm [%s] TJ ET\n" % (size, start, y, b" ".join(elems))
        spans.append((start, end))
        x = pen
    return out, spans


def page_word(w: PdfWriter):
    f = K.type0_font(w, ALL_CHARS, base="/BCDGEE+Calibri", dw=1000, widths=W)
    runs = ["Read the ", "street use permit rules", " before you apply, or click ", "here", " for answers."]
    c, spans = word_runs(runs, x=72.0, y=658.3, size=12)
    c = K.bt("F1", 20, 72, 700, K.type0_hex("Winter Parking Permits")) + c + _body("F1", 600, cid=True)
    page = K.add_page(w, c, {"F1": f})
    links = [
        K.link_annot(w, (spans[1][0], 652.8, spans[1][1], 669.7), uri="https://example.gov/permits/rules"),
        K.link_annot(w, (spans[3][0], 652.8, spans[3][1], 669.7), uri="https://example.gov/permits/faq"),
    ]
    page[K.NameObject("/Annots")] = K.ArrayObject(links)
    return " ".join("".join(runs).split())


# ---- 2. Chrome / Skia --------------------------------------------------------


def skia_line(text: str, *, x: float, y: float, size: float) -> tuple:
    """Skia's shape: device space 1/0.24 pt, y down; one Tj per glyph, each
    followed by a Td of its advance. Returns (content, [x of each char])."""
    s = 0.24
    sd = size / s
    xd, yd = x / s, (792 - y) / s
    out = b"q 0.24 0 0 -0.24 0 792 cm BT /F1 %.4f Tf 1 0 0 -1 %.4f %.4f Tm\n" % (sd, xd, yd)
    xs, pen = [], x
    for i, ch in enumerate(text):
        xs.append(pen)
        out += K.type0_hex(ch) + b" Tj\n"
        adv = W[ch] / 1000.0 * sd
        pen += adv * s
        if i < len(text) - 1:
            out += b"%.4f 0 Td\n" % adv
    xs.append(pen)
    return out + b"ET Q\n", xs


def page_skia(w: PdfWriter):
    omit = {ch: v for ch, v in W.items() if v != 500}  # Skia leaves DW-width glyphs out of /W
    f = K.type0_font(w, ALL_CHARS, base="/AAAAAA+ArialMT", dw=500, widths=omit)
    line1 = "The full list is on the snow route priority map, updated each season."
    line2 = "Secondary routes are treated later. Click here for the list."
    c1, xs1 = skia_line(line1, x=85.35, y=571.5, size=11)
    c2, xs2 = skia_line(line2, x=85.35, y=381.0, size=11)
    c = K.bt("F1", 20, 72, 700, K.type0_hex("Winter Road Maintenance")) + c1 + c2 + _body("F1", 540, 8, cid=True)
    page = K.add_page(w, c, {"F1": f})
    a = line1.index("snow route priority map")
    b = line2.index("here")
    links = [
        K.link_annot(w, (xs1[a], 569.3, xs1[a + len("snow route priority map")], 581.3), uri="https://example.gov/snow/priorities"),
        K.link_annot(w, (xs2[b], 378.8, xs2[b + 4], 390.8), uri="https://example.gov/snow/secondary"),
    ]
    page[K.NameObject("/Annots")] = K.ArrayObject(links)
    return line1


# ---- 3 + 4. pen carried across Tj / TJ strings --------------------------------


def page_pen(w: PdfWriter):
    f, fb = K.helvetica(w), K.helvetica(w, bold=True)
    c = K.bt("F1", 20, 72, 720, K.lit("Depot Contacts"))
    c += b"BT /F1 11 Tf 72 660 Td (Questions? Click ) Tj /F2 11 Tf (here) Tj /F1 11 Tf ( to email the depot.) Tj ET\n"
    c += b"BT /F1 11 Tf 72 620 Td [(See the )(S)15(no)10(w R)20(oute M)15(ap)( for details.)] TJ ET\n"
    c += _body("F1", 560)
    page = K.add_page(w, c, {"F1": f, "F2": fb})
    x0 = 72 + sum(W[ch] for ch in "Questions? Click ") * 11 / 1000.0
    x1 = x0 + sum(WB[ch] for ch in "here") * 11 / 1000.0
    m0 = 72 + sum(W[ch] for ch in "See the ") * 11 / 1000.0
    m1 = m0 + (sum(W[ch] for ch in "Snow Route Map") - (15 + 10 + 20 + 15)) * 11 / 1000.0
    links = [
        K.link_annot(w, (x0, 657, x1, 670), uri="mailto:depot@example.gov"),
        K.link_annot(w, (m0, 617, m1, 630), uri="https://example.gov/map"),
    ]
    page[K.NameObject("/Annots")] = K.ArrayObject(links)


# ---- 5. kerned form labels ---------------------------------------------------


def page_form(w: PdfWriter):
    f = K.helvetica(w)
    c = K.bt("F1", 20, 72, 720, K.lit("Permit Application"))
    c += b"BT /F1 11 Tf 72 680 Td [(A)15(pplicant n)10(ame:)] TJ ET\n"
    c += b"BT /F1 11 Tf 72 650 Td [(E)20(mail addr)10(ess:)] TJ ET\n"
    c += _body("F1", 600)
    page = K.add_page(w, c, {"F1": f})
    wid = [K.widget(w, (170, 676, 400, 690), name="Text1"), K.widget(w, (170, 646, 400, 660), name="Text2")]
    for ref in wid:
        ref.get_object()[K.NameObject("/P")] = page.indirect_reference
    page[K.NameObject("/Annots")] = K.ArrayObject(wid)
    K.set_acroform(w, wid)


def build():
    w = PdfWriter()
    word_line = page_word(w)
    skia_line1 = page_skia(w)
    page_pen(w)
    page_form(w)
    return K.to_bytes(w), word_line, skia_line1


def _line_words(reader, page_index: int, y: float):
    from app.pdf.text_geometry import all_words, page_spans

    ws = [x for x in all_words(page_spans(reader.pages[page_index], reader)) if abs(x.y - y) < 1.0]
    return [x.text for x in sorted(ws, key=lambda x: x.x0)]


def main() -> int:
    from app.pdf.text_geometry import Word, looks_fragmented, text_in_rect

    check = K.Checker()
    data, word_line, skia_line1 = build()
    rd = PdfReader(io.BytesIO(data))

    got = _line_words(rd, 0, 658.3)
    check("Word 365 TJ runs: whole words", got == word_line.split(), str(got))
    got = _line_words(rd, 1, 571.5)
    check("Chrome/Skia one-Tj-per-glyph: whole words", got == skia_line1.split(), str(got))
    got = _line_words(rd, 2, 660)
    check("consecutive Tj: the pen moves on ('here' is not drawn over 'Click')",
          got == "Questions? Click here to email the depot.".split(), str(got))
    got = _line_words(rd, 2, 620)
    check("adjacent strings in one TJ: the pen moves on", got == "See the Snow Route Map for details.".split(), str(got))

    # Fail closed on letter-by-letter text from a shape we do not rebuild.
    check("'h e r e' is fragmented", looks_fragmented("h e r e"))
    check("'s t r ee t u se p er mit ru les' is fragmented", looks_fragmented("s t r ee t u se p er mit ru les"))
    check("'go to p. 2 of the map' is not", not looks_fragmented("go to p. 2 of the map"))
    check("'Plan B' is not", not looks_fragmented("Plan B"))
    letters = [Word(ch, 100 + 6 * i, 105 + 6 * i, 500, 11) for i, ch in enumerate("here")]
    check("a link over letter fragments is unnamed (URI fallback), not 'h e r e'",
          text_in_rect(letters, (99, 495, 130, 510)) is None)

    pipe = K.Pipeline("glyphwords@example.com")
    a = pipe.analyze("words.pdf", data)
    check("analyze succeeds", a.status_code == 200, a.text[:200])
    viol = a.json().get("violations", [])
    nondesc = [v for v in viol if v.get("ruleId") == "LINK_TEXT_NON_DESCRIPTIVE"]
    check("the three 'here' links are flagged non-descriptive (Word, Chrome, bold Tj) — none of the others",
          len(nondesc) == 3, str([(v.get("ruleId"), (v.get("location") or {}).get("snippet")) for v in nondesc]))
    r = pipe.remediate("words.pdf", data)
    check("remediate succeeds", r.status_code == 200, r.text[:300])
    out = PdfReader(io.BytesIO(pipe.download(r.json())))
    names = []
    tu = {}
    for pg in out.pages:
        for ref in pg.get("/Annots") or []:
            o = ref.get_object()
            if o.get("/Subtype") == "/Link":
                names.append(str(o.get("/Contents")) if o.get("/Contents") is not None else None)
            elif o.get("/Subtype") == "/Widget":
                tu[str(o.get("/T"))] = str(o.get("/TU")) if o.get("/TU") is not None else None
    check("link names written to the output are the printed words",
          names == ["street use permit rules", "here", "snow route priority map", "here", "here", "Snow Route Map"],
          str(names))
    check("kerned labels: /TU is the whole words", tu == {"Text1": "Applicant name", "Text2": "Email address"}, str(tu))
    return check.done()


if __name__ == "__main__":
    sys.exit(main())
