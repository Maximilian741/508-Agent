"""Smoke: a PDF form field takes the label PRINTED next to it, and its /Form
element sits next to that label in the reading order.

Before: labels came only from a clean /T name. "Text3" (printed label "Date of
birth:"), an XFA path (label "Email address:") and a checkbox named "Yes"
(label "I certify the information above is accurate.") all stayed unlabeled —
announced as "Text3, edit", "f1_04, edit", "Yes, check box" — although the
label was right there on the page. And every /Form element was appended after
ALL the page text, so a screen reader in tagged order heard every label, then
every field.

Now:
  1. text fields take the words immediately LEFT of them on the same line
     (or directly ABOVE, aligned with the field's left edge); checkboxes take
     the words immediately RIGHT of them;
  2. refusals: a field whose only nearby words belong to another field's
     label, radio buttons, anything ambiguous — stay manual;
  3. the parser's derivable count equals exactly what the writer writes, via
     the real /pipeline/remediate route (charged == written);
  4. each /Form element follows its label in the structure tree.

Usage:
    python -m app.devtools.smoke_pdf_form_labels_geometry
"""

from __future__ import annotations

import io
import sys

from app.devtools import _pdf_fixture_kit as K

_TMP = K.isolated_env("508_smoke_formgeo_")

from pypdf import PdfReader, PdfWriter  # noqa: E402

EXPECTED = {
    "First Name": "First Name",  # clean /T (unchanged behaviour)
    "Text3": "Date of birth",
    "topmostSubform[0].Page1[0].f1_04[0]": "Email address",
    "Yes": "I certify the information above is accurate.",
    "Text7": "Phone",  # label printed above the field
    "Text8": "Name",
}
MANUAL = {"Text9", "Choice1"}


def build() -> bytes:
    w = PdfWriter()
    f = K.helvetica(w)
    c = K.bt("F1", 18, 72, 740, K.lit("Permit Application"))
    c += K.bt("F1", 11, 72, 700, K.lit("First Name:"))
    c += K.bt("F1", 11, 72, 670, K.lit("Date of birth:"))
    c += K.bt("F1", 11, 72, 640, K.lit("Email address:"))
    c += K.bt("F1", 11, 92, 600, K.lit("I certify the information above is accurate."))
    c += K.bt("F1", 11, 72, 560, K.lit("Phone"))
    c += K.bt("F1", 11, 72, 500, K.lit("Name:"))
    # Enough instructions for the document to count as untagged prose.
    for i in range(4):
        c += K.bt("F1", 10, 72, 420 - 14 * i, K.lit("Return this application to the permit office with a copy of your photo ID."))
    p = K.add_page(w, c, {"F1": f})
    wid = [
        K.widget(w, (160, 696, 360, 714), name="First Name"),
        K.widget(w, (160, 666, 360, 684), name="Text3"),
        K.widget(w, (160, 636, 360, 654), name="topmostSubform[0].Page1[0].f1_04[0]"),
        K.widget(w, (72, 598, 84, 610), name="Yes", ft="/Btn"),
        K.widget(w, (72, 532, 272, 550), name="Text7"),
        K.widget(w, (120, 496, 220, 514), name="Text8"),
        # right of Text8: its only neighbour is Text8's label -> manual
        K.widget(w, (230, 496, 330, 514), name="Text9"),
        # a radio button beside words: radios are never labelled from geometry
        K.widget(w, (400, 598, 412, 610), name="Choice1", ft="/Btn", ff=1 << 15),
    ]
    for ref in wid:
        ref.get_object()[K.NameObject("/P")] = p.indirect_reference
    p[K.NameObject("/Annots")] = K.ArrayObject(wid)
    K.set_acroform(w, wid)
    return K.to_bytes(w)


def main() -> int:
    check = K.Checker()
    data = build()
    pipe = K.Pipeline("formgeo@example.com")
    a = pipe.analyze("form.pdf", data)
    check("analyze succeeds", a.status_code == 200, a.text[:200])
    rules = {v["ruleId"] for v in a.json()["violations"]}
    check("FORM_FIELD_UNLABELED raised", "FORM_FIELD_UNLABELED" in rules, str(rules))

    from app.parsers.pdf_parser import PDFParser
    import os

    p = os.path.join(_TMP, "form.pdf")
    with open(p, "wb") as fh:
        fh.write(data)
    props = PDFParser().parse(p).tree.root.metadata.properties
    check("parser: 8 fields, 8 unlabeled, 6 derivable",
          (props.get("form_fields_total"), props.get("form_fields_unlabeled"), props.get("form_fields_derivable")) == (8, 8, 6),
          str((props.get("form_fields_total"), props.get("form_fields_unlabeled"), props.get("form_fields_derivable"))))
    locs = props.get("form_fields_unlabeled_locations") or []
    check("each unlabeled field is located (page + bbox)", len(locs) == 8 and all(l.get("page") == 1 and len(l.get("bbox") or []) == 4 for l in locs), str(locs[:2]))

    r = pipe.remediate("form.pdf", data)
    check("remediate succeeds", r.status_code == 200, r.text[:300])
    body = r.json()
    written = [x for x in (body.get("writer") or {}).get("applied", []) if x.get("kind") == "form_field_label"]
    check("writer wrote exactly the derivable count (charged == written)", len(written) == 6, str(len(written)))
    out = PdfReader(io.BytesIO(pipe.download(body)))
    tu = {}
    for ref in out.pages[0]["/Annots"]:
        o = ref.get_object()
        tu[str(o.get("/T"))] = str(o.get("/TU")) if o.get("/TU") is not None else None
    for name, label in EXPECTED.items():
        check(f"{name!r} -> /TU {label!r}", tu.get(name) == label, repr(tu.get(name)))
    for name in MANUAL:
        check(f"{name!r} stays manual (no /TU)", tu.get(name) is None, repr(tu.get(name)))

    order = [s for d, s, _e in K.struct_elems(out) if d == 1]
    # H1, then label, field for the first three rows; checkbox BEFORE its
    # right-hand label; the above-label "Phone" followed by its field.
    check("each /Form element sits next to its label",
          order[:9] == ["/H1", "/P", "/Form", "/P", "/Form", "/P", "/Form", "/Form", "/P"], str(order))
    i_phone = [i for i, s in enumerate(order) if s == "/P"][5] if order.count("/P") > 5 else -1
    check("the field under 'Phone' follows it", i_phone >= 0 and order[i_phone + 1] == "/Form", str(order))

    # ---- layouts that used to get the WRONG label written (and charged) ----
    got = _labels(yes_no_before())
    check("'Yes [ ]  No [ ]': each box takes the word right BEFORE it (the Yes box was labelled 'No')",
          got == {"cb_yes": "Yes", "cb_no": "No"}, str(got))
    got = _labels(yes_no_after())
    check("'[ ] Yes  [ ] No': each box takes the word right AFTER it", got == {"cb_yes": "Yes", "cb_no": "No"}, str(got))
    got = _labels(captions_below())
    check("captions printed UNDER fields: no field takes the heading above it or the previous field's caption",
          got == {"Text1": None, "Text2": None}, str(got))
    return check.done()


def _labels(data: bytes):
    """{/T: label the parser would write} — the same helper the writer uses."""
    from app.parsers.pdf_parser import FieldLabeler, derive_pdf_field_label, iter_acroform_fields

    r = PdfReader(io.BytesIO(data))
    lab = FieldLabeler(r)
    acro = r.trailer["/Root"]["/AcroForm"].get_object()
    return {str(fo.get("/T")): derive_pdf_field_label(fo, lab) for fo in iter_acroform_fields(acro)}


def _form(content: bytes, widgets) -> bytes:
    w = PdfWriter()
    f = K.helvetica(w)
    for i in range(6):
        content += K.bt("F1", 10, 72, 600 - 14 * i, K.lit("Return this application to the permit office with a copy of your ID."))
    p = K.add_page(w, content, {"F1": f})
    refs = [K.widget(w, rect, name=name, ft=ft) for rect, name, ft in widgets]
    for ref in refs:
        ref.get_object()[K.NameObject("/P")] = p.indirect_reference
    p[K.NameObject("/Annots")] = K.ArrayObject(refs)
    K.set_acroform(w, refs)
    return K.to_bytes(w)


def yes_no_before() -> bytes:
    c = K.bt("F1", 11, 72, 700, K.lit("Do you own a vehicle?"))
    c += K.bt("F1", 11, 250, 700, K.lit("Yes")) + K.bt("F1", 11, 300, 700, K.lit("No"))
    return _form(c, [((272, 698, 282, 708), "cb_yes", "/Btn"), ((316, 698, 326, 708), "cb_no", "/Btn")])


def yes_no_after() -> bytes:
    c = K.bt("F1", 11, 72, 700, K.lit("Do you own a vehicle?"))
    c += K.bt("F1", 11, 264, 700, K.lit("Yes")) + K.bt("F1", 11, 314, 700, K.lit("No"))
    return _form(c, [((250, 698, 260, 708), "cb_yes", "/Btn"), ((300, 698, 310, 708), "cb_no", "/Btn")])


def captions_below() -> bytes:
    c = K.bt("F1", 16, 72, 740, K.lit("Signature Page"))
    c += K.bt("F1", 8, 72, 690, K.lit("Signature of applicant"))
    c += K.bt("F1", 8, 72, 664, K.lit("Printed name"))
    return _form(c, [((72, 698, 300, 712), "Text1", "/Tx"), ((72, 672, 300, 686), "Text2", "/Tx")])


if __name__ == "__main__":
    sys.exit(main())
