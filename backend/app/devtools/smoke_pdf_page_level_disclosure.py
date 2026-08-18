"""Smoke: pages the tagger could only wrap at page level are DISCLOSED in the note.

The TAG_PDF_STRUCTURE executor runs before the writer and can only promise
("headings, lists, tables ... will be reconstructed"). The tagger then bails
to a single page-level /P on any page whose content stream it cannot safely
segment (nested BT, unreconcilable op count). Those pages are tagged and
valid but carry NO headings/lists/tables. The count lived only in
writer.skipped / the pdfua summary, while the execution note the user reads
still promised the full structure. The pipeline now reconciles the two and
appends the disclosure to the note itself.

Pinned here through the real app: a 4-page PDF where 3 pages have nested BT
-> pdfua says pagesPageLevelOnly=3, and the TAG_PDF_STRUCTURE execution note
says "3 of 4 page(s) could not be broken into elements".

Usage:
    python -m app.devtools.smoke_pdf_page_level_disclosure
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_plo_')}/s.db"
os.environ.setdefault("APP_SECRET", "smoke-plo-secret")

from fastapi.testclient import TestClient  # noqa: E402
from pypdf import PdfWriter  # noqa: E402
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject  # noqa: E402

from app.main import app  # noqa: E402


def _pdf() -> bytes:
    w = PdfWriter()
    f = DictionaryObject()
    f.update({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    fs = DictionaryObject(); fs[NameObject("/F1")] = w._add_object(f)  # noqa: SLF001
    res = DictionaryObject(); res[NameObject("/Font")] = fs
    long = b"a genuine sentence of running body text that keeps going for a while so the page has plenty of characters"
    for p in range(4):
        page = w.add_blank_page(width=612, height=792)
        if p < 3:  # nested BT..BT..ET..ET: the tagger's documented bail condition
            body = b"BT /F1 12 Tf 72 700 Td (" + long + b") Tj BT /F1 12 Tf 72 680 Td (" + long + b") Tj ET ET"
        else:
            body = b"BT /F1 18 Tf 72 720 Td (Clean Heading) Tj ET BT /F1 12 Tf 72 690 Td (" + long + b") Tj ET"
        cs = DecodedStreamObject(); cs.set_data(body)
        page[NameObject("/Contents")] = w._add_object(cs)  # noqa: SLF001
        page[NameObject("/Resources")] = res
    buf = io.BytesIO(); w.write(buf)
    return buf.getvalue()


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    c = TestClient(app)
    r = c.post("/auth/sign-in", json={"email": "plo@example.com", "displayName": "P", "password": "starterpass1"})
    hdr = {"Authorization": "Bearer " + r.json()["token"]}
    c.post("/auth/grant-starter", headers=hdr)
    data = _pdf()
    a = c.post("/pipeline/analyze", headers=hdr, files={"file": ("plo.pdf", data, "application/pdf")}).json()
    check("fixture is detected as untagged", "PDF_UNTAGGED" in {v["ruleId"] for v in a["violations"]})
    ids = [v["id"] for v in a["violations"]]
    r = c.post("/pipeline/remediate", headers=hdr, files={"file": ("plo.pdf", data, "application/pdf")},
               data={"approved_violations": json.dumps(ids), "rejected_violations": "[]"})
    check("remediate succeeds", r.status_code == 200, r.text[:200])
    j = r.json()
    pdfua = (j.get("writer") or {}).get("pdfua") or {}
    check("pdfua summary: 3 of 4 pages fell back to page level",
          pdfua.get("pages") == 4 and pdfua.get("pagesPageLevelOnly") == 3, str(pdfua))
    tag = [e for e in j.get("executions", []) if e["actionCode"] == "TAG_PDF_STRUCTURE"]
    check("a TAG_PDF_STRUCTURE execution exists", bool(tag))
    note = tag[0]["notes"] if tag else ""
    check("the execution NOTE discloses the page-level fallback (not just writer.skipped)",
          "3 of 4 page(s) could not be broken into elements" in note, note[-220:])
    check("...and says those pages' structure was NOT identified", "NOT identified" in note, note[-220:])

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
