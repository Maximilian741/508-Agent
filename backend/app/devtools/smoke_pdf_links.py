"""Smoke: PDF/UA link-annotation tagging (Matterhorn 28-011 / 28-012).

When the tagger reconstructs structure on an untagged PDF, every /Link
annotation must end up nested in a /Link structure element via an OBJR
reference, with:
  - /StructParent on the annotation,
  - an ascending ParentTree entry mapping that key -> the /Link StructElem,
  - ParentTreeNextKey above all keys,
  - a /Contents accessible description (fallback = the URI action's URI),
  - /Tabs /S on the page.
Pages without annotations and non-link annotations are untouched; the text
byte-identity guard still holds (annotations live outside content streams).

Usage:
    python -m app.devtools.smoke_pdf_links
"""

from __future__ import annotations

import io
import os
import sys
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_pl_')}/s.db")

from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import (  # noqa: E402
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

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


def _link_annot(w, uri: str, rect, contents: str | None = None):
    action = DictionaryObject(
        {NameObject("/S"): NameObject("/URI"), NameObject("/URI"): TextStringObject(uri)}
    )
    annot = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Link"),
            NameObject("/Rect"): ArrayObject([FloatObject(v) for v in rect]),
            NameObject("/Border"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(0)]),
            NameObject("/A"): action,
        }
    )
    if contents:
        annot[NameObject("/Contents")] = TextStringObject(contents)
    return w._add_object(annot)


def _build(with_links: bool):
    w = PdfWriter()
    res = _font_res(w)
    page = w.add_blank_page(width=460, height=560)
    cs = DecodedStreamObject()
    cs.set_data(
        b"BT /F1 18 Tf 40 520 Td (Linked Report) Tj ET\n"
        b"BT /F1 11 Tf 40 480 Td (See the published data portal for details.) Tj ET"
    )
    page[NameObject("/Contents")] = w._add_object(cs)
    page[NameObject("/Resources")] = res
    if with_links:
        # Over blank space: no printed words to name it, so the URI fallback
        # applies. (A rect over text is named by that text — pinned in
        # smoke_pdf_links_named_by_page.)
        a1 = _link_annot(w, "https://data.example.gov/portal", (40, 300, 200, 322))
        a2 = _link_annot(w, "https://example.gov/method", (40, 440, 200, 462), contents="Methodology notes")
        page[NameObject("/Annots")] = ArrayObject([a1, a2])
    tree = AccessibilityTree(
        root=DocumentNode(
            id="d",
            content=NodeContent(kind=ContentKind.NONE),
            metadata=NodeMetadata(source_format="pdf", language="en", properties={"title": "T"}),
        )
    )
    report = tag_pdf(w, tree)
    buf = io.BytesIO()
    w.write(buf)
    buf.seek(0)
    return report, PdfReader(buf)


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    report, r = _build(with_links=True)
    check("tagger reports 2 links", report.get("links") == 2, str(report))

    root = r.trailer["/Root"]
    st = root["/StructTreeRoot"].get_object()
    doc_elem = st["/K"][0].get_object()
    kids = [k.get_object() for k in doc_elem["/K"]]
    link_elems = [k for k in kids if str(k.get("/S")) == "/Link"]
    check("2 /Link StructElems under Document", len(link_elems) == 2, str([str(k.get('/S')) for k in kids]))

    page = r.pages[0]
    annots = [a.get_object() for a in page["/Annots"]]
    check("annots carry /StructParent", all(a.get("/StructParent") is not None for a in annots))

    # OBJR points back at the annotation object.
    objr_ok = 0
    for le in link_elems:
        k = le.get("/K")
        k = k.get_object() if hasattr(k, "get_object") else k
        if str(k.get("/Type")) == "/OBJR" and k.get("/Obj") is not None:
            target = k["/Obj"].get_object()
            if str(target.get("/Subtype")) == "/Link":
                objr_ok += 1
    check("each /Link kid is an OBJR to the annot", objr_ok == 2)

    # ParentTree: ascending keys; annot keys map DIRECTLY to the Link elems.
    nums = st["/ParentTree"].get_object()["/Nums"]
    keys = [int(nums[i]) for i in range(0, len(nums), 2)]
    check("ParentTree keys ascending", keys == sorted(keys), str(keys))
    by_key = {int(nums[i]): nums[i + 1] for i in range(0, len(nums), 2)}
    sp_ok = 0
    for a in annots:
        entry = by_key.get(int(a["/StructParent"]))
        entry = entry.get_object() if hasattr(entry, "get_object") else entry
        if isinstance(entry, DictionaryObject) and str(entry.get("/S")) == "/Link":
            sp_ok += 1
    check("annot StructParent keys resolve to the /Link elems", sp_ok == 2)
    check(
        "ParentTreeNextKey above all keys",
        int(st["/ParentTreeNextKey"]) > max(keys),
        str(st.get("/ParentTreeNextKey")),
    )

    # Accessible description: URI fallback applied; explicit Contents kept.
    contents = sorted(str(a.get("/Contents") or "") for a in annots)
    check(
        "URI fallback + explicit Contents preserved",
        contents == ["Methodology notes", "https://data.example.gov/portal"],
        str(contents),
    )
    check("/Tabs /S on page", str(page.get("/Tabs")) == "/S")

    # Text content untouched (annotations live outside the content stream).
    text = r.pages[0].extract_text()
    check("text byte-content intact", "Linked Report" in text and "published data portal" in text)

    # No annots -> no /Link elems, ParentTreeNextKey == page count.
    report2, r2 = _build(with_links=False)
    check("no-annot doc reports 0 links", report2.get("links") == 0, str(report2))
    st2 = r2.trailer["/Root"]["/StructTreeRoot"].get_object()
    doc2 = st2["/K"][0].get_object()
    kinds2 = [str(k.get_object().get("/S")) for k in doc2["/K"]]
    check("no-annot doc has no /Link elems", "/Link" not in kinds2, str(kinds2))
    check("no-annot ParentTreeNextKey == pages", int(st2["/ParentTreeNextKey"]) == 1)

    # --- Widget annotation -> /Form StructElem with /TU Contents fallback ----
    w = PdfWriter()
    res = _font_res(w)
    page_w = w.add_blank_page(width=460, height=560)
    cs = DecodedStreamObject()
    cs.set_data(b"BT /F1 12 Tf 40 520 Td (Application form) Tj ET")
    page_w[NameObject("/Contents")] = w._add_object(cs)
    page_w[NameObject("/Resources")] = res
    widget = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/FT"): NameObject("/Tx"),
            NameObject("/T"): TextStringObject("full_name"),
            NameObject("/TU"): TextStringObject("Full name"),
            NameObject("/Rect"): ArrayObject([FloatObject(v) for v in (40, 460, 220, 482)]),
        }
    )
    w_ref = w._add_object(widget)
    page_w[NameObject("/Annots")] = ArrayObject([w_ref])
    tree_w = AccessibilityTree(
        root=DocumentNode(
            id="d",
            content=NodeContent(kind=ContentKind.NONE),
            metadata=NodeMetadata(source_format="pdf", language="en", properties={"title": "T"}),
        )
    )
    report_w = tag_pdf(w, tree_w)
    buf = io.BytesIO(); w.write(buf); buf.seek(0)
    rw = PdfReader(buf)
    check("widget doc reports 1 form widget", report_w.get("formWidgets") == 1, str(report_w))
    stw = rw.trailer["/Root"]["/StructTreeRoot"].get_object()
    docw = stw["/K"][0].get_object()
    kindsw = [str(k.get_object().get("/S")) for k in docw["/K"]]
    check("/Form StructElem present", "/Form" in kindsw, str(kindsw))
    annot_w = rw.pages[0]["/Annots"][0].get_object()
    check("widget got /StructParent", annot_w.get("/StructParent") is not None)
    check("widget /Contents falls back to /TU", str(annot_w.get("/Contents")) == "Full name")

    # --- TH /Scope + numbered-list /ListNumbering ----------------------------
    w2 = PdfWriter()
    res2 = _font_res(w2)
    page2 = w2.add_blank_page(width=460, height=620)
    rows = []
    # 3x3 aligned data grid (short cells) -> /Table with TH row 0.
    ys = (560, 540, 520)
    for r_i, y in enumerate(ys):
        for c_i, x in enumerate((40, 160, 280)):
            label = ("Name", "Unit", "Qty")[c_i] if r_i == 0 else f"v{r_i}{c_i}"
            rows.append(f"BT /F1 10 Tf {x} {y} Td ({label}) Tj ET".encode())
    # Numbered list (sequential from 1).
    rows.append(b"BT /F1 11 Tf 40 470 Td (1. prepare the draft) Tj ET")
    rows.append(b"BT /F1 11 Tf 40 452 Td (2. circulate for review) Tj ET")
    rows.append(b"BT /F1 11 Tf 40 434 Td (3. publish the final) Tj ET")
    cs2 = DecodedStreamObject()
    cs2.set_data(b"\n".join(rows))
    page2[NameObject("/Contents")] = w2._add_object(cs2)
    page2[NameObject("/Resources")] = res2
    tree2 = AccessibilityTree(
        root=DocumentNode(
            id="d",
            content=NodeContent(kind=ContentKind.NONE),
            metadata=NodeMetadata(source_format="pdf", language="en", properties={"title": "T"}),
        )
    )
    report2b = tag_pdf(w2, tree2)
    buf2 = io.BytesIO(); w2.write(buf2); buf2.seek(0)
    r2b = PdfReader(buf2)
    st2b = r2b.trailer["/Root"]["/StructTreeRoot"].get_object()

    def _walk(elem, acc):
        acc.append(elem)
        k = elem.get("/K")
        k = k.get_object() if hasattr(k, "get_object") else k
        if isinstance(k, ArrayObject):
            for kid in k:
                ko = kid.get_object() if hasattr(kid, "get_object") else kid
                if isinstance(ko, DictionaryObject) and ko.get("/Type") == "/StructElem":
                    _walk(ko, acc)
        return acc

    all_elems = _walk(st2b["/K"][0].get_object(), [])
    ths = [e for e in all_elems if str(e.get("/S")) == "/TH"]
    check("tagged table has TH cells", len(ths) >= 2, str(report2b))
    scope_ok = all(
        str((e.get("/A") or {}).get("/Scope")) == "/Column" for e in ths
    )
    check("every TH carries /A /Scope /Column", scope_ok)
    lists = [e for e in all_elems if str(e.get("/S")) == "/L"]
    check("numbered list tagged as /L", len(lists) >= 1, str(report2b))
    ln_ok = any(
        str((e.get("/A") or {}).get("/ListNumbering")) == "/Decimal" for e in lists
    )
    check("numbered /L carries /ListNumbering /Decimal", ln_ok)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
