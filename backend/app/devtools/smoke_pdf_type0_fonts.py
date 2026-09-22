"""Smoke: PDFs set in composite (Type0 / Identity-H) fonts are READ, not guessed.

Google Docs, Chrome, Skia and most subsetting exporters write every glyph as a
2-byte CID; the text is only readable through the font's /ToUnicode map. The
tagger and the title step used the raw operand bytes, so on these files:

  * SET_DOCUMENT_TITLE wrote "\\x00:\\x00L\\x00Q..." (the CIDs of "Winter Road
    Maintenance Plan 2026") as the /Title, reported success, and CHARGED;
  * a numeric top row ("2021 | 31.2 | 4.1") was typed /TH (the numeric test
    could not see digits), bullets were not lists, "Page 1 of 2" was body text;
  * tag_reader returned heading text as the Python repr "b'\\x00:...'";
  * when pypdf could not parse the ToUnicode CMap at all (MuPDF writes
    5-hex-digit bfrange destinations), extract_text raised, the parser set the
    page to "" and the document was reported with NO structure finding;
  * a PDF with ``/Title null`` got the literal title "NullObject".

Pinned here, through the parser, the tagger (read back with tag_reader) and the
real /pipeline routes:

  1. Decodable Type0: title candidate is the decoded words; remediation writes
     that readable /Title (charged, and persisted); headings read back as
     words; bullets -> /L; page numbers -> /Artifact; header row -> /TH.
  2. Numeric top row in Type0 -> NO /TH.
  3. Type0 with no ToUnicode: no title candidate, no title written or charged
     for, no heading/list/table claimed from unreadable glyphs, disclosed.
  4. Broken CMap: extract_text raises -> disclosed page list, the document is
     still flagged PDF_UNTAGGED (text exists and needs structure).
  5. /Title null -> title missing (finding raised), never "NullObject".

Usage:
    python -m app.devtools.smoke_pdf_type0_fonts
"""

from __future__ import annotations

import io
import os
import sys

from app.devtools import _pdf_fixture_kit as K

_TMP = K.isolated_env("508_smoke_type0_")

from pypdf import PdfReader, PdfWriter  # noqa: E402

TITLE = "Winter Road Maintenance Plan 2026"
BODY = [
    "Crews begin pre-treating bridges when forecasts show freezing rain.",
    "The department stocks rock salt at four depots across the city.",
    "Residents can report unplowed streets through the service portal.",
]
BULLETS = ["• Salt trucks run 24 hours", "• Plow routes are posted online"]
TABLE = [["Year", "Tons", "Cost"], ["2021", "31.2", "4.1"], ["2022", "28.7", "3.9"], ["2023", "33.0", "4.4"]]
NUMERIC = TABLE[1:]
CHARS = set(TITLE) | set("".join(BODY)) | set("".join(BULLETS)) | set("Page 0123456789 of") | set("".join(sum(TABLE, [])))


def build(*, to_unicode: bool = True, broken: bool = False, numeric: bool = False) -> bytes:
    w = PdfWriter()
    f = K.type0_font(w, CHARS, to_unicode=to_unicode, broken=broken)
    for p in range(2):
        c = b""
        if p == 0:
            c += K.bt("F1", 22, 72, 720, K.type0_hex(TITLE))
        y = 680
        for line in BODY:
            c += K.bt("F1", 11, 72, y, K.type0_hex(line))
            y -= 16
        for line in BULLETS:
            c += K.bt("F1", 11, 90, y, K.type0_hex(line))
            y -= 16
        y -= 20
        for row in (NUMERIC if numeric else TABLE):
            x = 72
            for cell in row:
                c += K.bt("F1", 10, x, y, K.type0_hex(cell))
                x += 90
            y -= 14
        c += K.bt("F1", 9, 300, 30, K.type0_hex(f"Page {p + 1} of 2"))
        K.add_page(w, c, {"F1": f})
    return K.to_bytes(w)


def helvetica_null_title() -> bytes:
    w = PdfWriter()
    f = K.helvetica(w)
    c = K.bt("F1", 22, 72, 720, K.lit("Quarterly Permit Review"))
    for i in range(12):
        c += K.bt("F1", 11, 72, 690 - 14 * i, K.lit("Permit volume grew in every district this quarter, led by the east side."))
    K.add_page(w, c, {"F1": f})
    K.null_title(w)
    return K.to_bytes(w)


def parse(data: bytes):
    from app.parsers.pdf_parser import PDFParser

    p = os.path.join(_TMP, f"f{abs(hash(data)) % 10**8}.pdf")
    with open(p, "wb") as fh:
        fh.write(data)
    return PDFParser().parse(p)


def tag(data: bytes):
    """Tag the parsed document directly and return (report, output reader)."""
    from app.pdf.ua_tagger import tag_pdf

    res = parse(data)
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    rep = tag_pdf(w, res.tree)
    buf = io.BytesIO()
    w.write(buf)
    buf.seek(0)
    return rep, PdfReader(buf)


def main() -> int:
    from app.pdf.tag_reader import read_struct_info

    check = K.Checker()
    good = build()

    # ---- 1. decodable Type0 -------------------------------------------------
    res = parse(good)
    props = res.tree.root.metadata.properties
    check("fixture really is Identity-H: raw bytes are CIDs, not ASCII",
          b"Winter" not in good and b"/Identity-H" in good)
    check("title candidate is the DECODED title", props.get("title_candidate") == TITLE,
          repr(props.get("title_candidate")))

    rep, out = tag(good)
    info = read_struct_info(out)
    heads = [h["text"] for h in info["headings"]]
    check("tag_reader reads the heading back as words, not a bytes repr",
          heads[:1] == [TITLE] and not any(h.startswith("b'") for h in heads), repr(heads[:2]))
    check("decoded bullets become lists (one per page)", rep.get("lists") == 2, str(rep))
    check("'Page N of 2' footers become artifacts", rep.get("artifacts") == 2, str(rep))
    check("label header row over numbers -> /TH row",
          info["tables"] and info["tables"][0]["rows"][0] == ["TH", "TH", "TH"], str(info["tables"][:1]))

    pipe = K.Pipeline("type0@example.com")
    a = pipe.analyze("type0.pdf", good).json()
    rules = {v["ruleId"] for v in a["violations"]}
    check("analyze: title missing + untagged are raised", {"DOCUMENT_TITLE_MISSING", "PDF_UNTAGGED"} <= rules, str(rules))
    b0 = pipe.balance()
    r = pipe.remediate("type0.pdf", good)
    check("remediate succeeds", r.status_code == 200, r.text[:300])
    body = r.json()
    ex = {e["actionCode"]: e for e in body.get("executions", [])}
    check("SET_DOCUMENT_TITLE succeeded", (ex.get("SET_DOCUMENT_TITLE") or {}).get("status") == "success",
          str(ex.get("SET_DOCUMENT_TITLE")))
    outr = PdfReader(io.BytesIO(pipe.download(body)))
    written = str((outr.metadata or {}).get("/Title") or "")
    check("the written /Title is the readable title", written == TITLE, repr(written))
    check("no control bytes anywhere in the written /Title", not any(ord(c) < 32 for c in written))
    xmp = outr.trailer["/Root"]["/Metadata"].get_object().get_data()
    check("XMP dc:title carries the readable title", TITLE.encode() in xmp)
    check("charged for what persisted", body.get("charged") is True and pipe.balance() < b0,
          f"charged={body.get('charged')} {b0}->{pipe.balance()}")

    # ---- 2. numeric top row in a composite font -> no header ---------------
    _rep, out_n = tag(build(numeric=True))
    tn = read_struct_info(out_n)["tables"]
    check("numeric top row (2021 | 31.2 | 4.1) is NOT typed /TH",
          bool(tn) and all("TH" not in row for t in tn for row in t["rows"]), str(tn[:1]))

    # ---- 3. composite font with NO ToUnicode: nothing claimed ---------------
    blind = build(to_unicode=False)
    res_b = parse(blind)
    pb = res_b.tree.root.metadata.properties
    check("no title candidate from glyph ids", not pb.get("title_candidate"), repr(pb.get("title_candidate")))
    check("undecodable pages disclosed", pb.get("text_undecodable_pages") == [1, 2], str(pb.get("text_undecodable_pages")))
    check("...and their text still counts (it exists and needs structure)", int(pb.get("total_text_chars") or 0) >= 200)
    rep_b, out_b = tag(blind)
    info_b = read_struct_info(out_b)
    check("no heading asserted from unreadable text", info_b["headings"] == [], str(info_b["headings"][:2]))
    check("no table / list asserted from unreadable text",
          not info_b["tables"] and rep_b.get("lists") == 0, f"{info_b['tables'][:1]} {rep_b.get('lists')}")
    check("tagger reports the undecodable blocks", int(rep_b.get("undecodableTextBlocks") or 0) > 0, str(rep_b))
    check("...and does not claim PDF/UA", rep_b.get("pdfuaClaimed") is False
          and any("Unicode" in b for b in rep_b.get("pdfuaBlockers") or []), str(rep_b.get("pdfuaBlockers")))
    r = pipe.remediate("blind.pdf", blind)
    check("remediate (no ToUnicode) succeeds", r.status_code == 200, r.text[:300])
    body_b = r.json()
    ex_b = {e["actionCode"]: e for e in body_b.get("executions", [])}
    check("SET_DOCUMENT_TITLE is NOT a success (nothing readable to write)",
          (ex_b.get("SET_DOCUMENT_TITLE") or {}).get("status") != "success", str(ex_b.get("SET_DOCUMENT_TITLE")))
    t_b = str((PdfReader(io.BytesIO(pipe.download(body_b))).metadata or {}).get("/Title") or "")
    check("no garbage /Title written", not t_b or all(ord(c) >= 32 for c in t_b), repr(t_b))
    reasons = " ".join(s.get("reason", "") for s in (body_b.get("writer") or {}).get("skipped", []))
    check("the user is told the text could not be read", "pdfua_text_unreadable" in reasons, reasons[:300])

    # ---- 4. a ToUnicode CMap pypdf cannot parse -----------------------------
    broken = build(broken=True)
    raised = False
    try:
        PdfReader(io.BytesIO(broken)).pages[0].extract_text()
    except Exception:
        raised = True
    check("fixture: pypdf extract_text raises on this CMap", raised)
    pb2 = parse(broken).tree.root.metadata.properties
    check("extraction failure disclosed per page", pb2.get("text_extraction_failed_pages") == [1, 2],
          str(pb2.get("text_extraction_failed_pages")))
    check("...not swallowed as 'no text'", int(pb2.get("total_text_chars") or 0) >= 200, str(pb2.get("total_text_chars")))
    check("...and no title claimed from it", not pb2.get("title_candidate"))
    a2 = pipe.analyze("broken.pdf", broken).json()
    check("the document is still flagged PDF_UNTAGGED",
          "PDF_UNTAGGED" in {v["ruleId"] for v in a2["violations"]}, str([v["ruleId"] for v in a2["violations"]]))

    # ---- 5. /Title null ------------------------------------------------------
    nt = helvetica_null_title()
    check("fixture: raw bytes carry /Title null", b"/Title null" in nt)
    a3 = pipe.analyze("nulltitle.pdf", nt).json()
    check("summary title is not the string 'NullObject'", a3["summary"].get("title") in (None, ""),
          repr(a3["summary"].get("title")))
    check("DOCUMENT_TITLE_MISSING is raised", "DOCUMENT_TITLE_MISSING" in {v["ruleId"] for v in a3["violations"]})
    r = pipe.remediate("nulltitle.pdf", nt)
    t3 = str((PdfReader(io.BytesIO(pipe.download(r.json()))).metadata or {}).get("/Title") or "")
    check("remediation writes the real title, never 'NullObject'", t3 == "Quarterly Permit Review", repr(t3))

    return check.done()


if __name__ == "__main__":
    sys.exit(main())
