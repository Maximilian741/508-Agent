"""Smoke: unlabeled PDF AcroForm fields get an accessible name (auto-fix).

An AcroForm field with no ``/TU`` has no accessible label — a screen reader
announces only the field type. PDF previously DETECTED these but never fixed
them. This pins the new CONSERVATIVE auto-fix:

  parser   -> counts unlabeled fields AND how many have a confident label we
              can derive from a descriptive ``/T`` (form_fields_derivable);
              shared derive_pdf_field_label
  analyzer -> FORM_FIELD_UNLABELED fires on the document root
  executor -> FILL_FORM_FIELD_LABELS succeeds only when >=1 is derivable
  writer   -> /TU written on exactly the derivable fields; auto-generated
              names ("Text1", "Check Box 3", "untitled") and pushbuttons stay
              manual; an existing /TU is never clobbered
  honesty  -> FILL_FORM_FIELD_LABELS persists for pdf; re-reading the OUTPUT
              shows /TU set on the derivable fields and the unlabeled count
              dropped by exactly the derivable count (idempotent)

Usage:
    python -m app.devtools.smoke_pdf_form_labels
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_pff_')}/s.db")

from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import (  # noqa: E402
    ArrayObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.api.pipeline import _action_persists  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.parsers.pdf_parser import _clean_pdf_field_name, derive_pdf_field_label  # noqa: E402
from app.services.remediation_planner import RemediationPolicy, plan_remediations  # noqa: E402
from app.services.remediators.registry import execute_plans  # noqa: E402
from app.writers.pdf_writer import write_remediated_pdf  # noqa: E402

FLAG = "FORM_FIELD_UNLABELED"
PUSHBUTTON = 1 << 16


def _field(t: str, ft: str = "/Tx", tu: str | None = None, ff: int | None = None) -> DictionaryObject:
    d = DictionaryObject({NameObject("/FT"): NameObject(ft), NameObject("/T"): TextStringObject(t)})
    if tu is not None:
        d[NameObject("/TU")] = TextStringObject(tu)
    if ff is not None:
        d[NameObject("/Ff")] = NumberObject(ff)
    return d


def _build_pdf(path: Path, fields: list[DictionaryObject]) -> None:
    w = PdfWriter()
    w.add_blank_page(width=300, height=200)
    refs = [w._add_object(f) for f in fields]  # noqa: SLF001
    w._root_object[NameObject("/AcroForm")] = w._add_object(  # noqa: SLF001
        DictionaryObject({NameObject("/Fields"): ArrayObject(refs)})
    )
    with open(path, "wb") as fh:
        w.write(fh)


def _tu_by_t(path: Path) -> dict[str, str]:
    """Map each AcroForm field's /T -> its /TU (or '') after a round-trip."""
    reader = PdfReader(str(path))
    root = reader.trailer["/Root"]
    acro = root["/AcroForm"].get_object()
    out: dict[str, str] = {}
    for ref in acro["/Fields"]:
        fo = ref.get_object()
        out[str(fo.get("/T"))] = str(fo.get("/TU") or "")
    return out


def _root_props(tree) -> dict:
    return tree.root.metadata.properties or {}


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="pdf_form_"))
    policy = RemediationPolicy(allow_ai_actions=True, require_human_review_for_all=False)

    check("honesty matrix: FILL_FORM_FIELD_LABELS persists for pdf",
          _action_persists("FILL_FORM_FIELD_LABELS", "pdf"))

    # Mixed form: 3 derivable, 4 non-derivable (autoname/junk/pushbutton), 1 labeled.
    src = tmp / "form.pdf"
    _build_pdf(src, [
        _field("First Name"),                       # derivable -> "First Name"
        _field("Email_Address"),                    # derivable -> "Email Address"
        _field("Phone"),                            # derivable -> "Phone"
        _field("Address 2"),                        # derivable -> "Address 2" (real: address line 2)
        _field("Text1"),                            # autoname -> manual
        _field("Check Box 3", ft="/Btn"),           # autoname -> manual
        _field("Text Field 2"),                     # autoname (widget words + number) -> manual
        _field("untitled"),                         # junk -> manual
        _field("Country", tu="Select your country"),  # already labeled
        _field("Submit", ft="/Btn", ff=PUSHBUTTON),  # pushbutton -> manual
    ])

    res = parse_to_tree(str(src))
    run_analyzers(res.tree)
    props = _root_props(res.tree)
    check("FORM_FIELD_UNLABELED fires",
          any(f.code.value == FLAG for f in res.tree.root.accessibility_flags))
    check("10 total, 9 unlabeled, 4 derivable",
          props.get("form_fields_total") == 10
          and props.get("form_fields_unlabeled") == 9
          and props.get("form_fields_derivable") == 4,
          f"total={props.get('form_fields_total')} unlabeled={props.get('form_fields_unlabeled')} derivable={props.get('form_fields_derivable')}")

    plans = [p for p in plan_remediations(res.tree, policy) if p.flag.code.value == FLAG]
    execs = execute_plans(res.tree, plans)
    ok = [e for e in execs if e.status.value == "success" and e.action_code.value == "FILL_FORM_FIELD_LABELS"]
    check("fill executes successfully", len(ok) == 1,
          str([(e.action_code.value, e.status.value, e.notes) for e in execs]))

    out = tmp / "form_fixed.pdf"
    result = write_remediated_pdf(src, res.tree, out)
    labels = [a for a in result["applied"] if a.get("kind") == "form_field_label"]
    check("writer applied exactly 4 /TU labels (== derivable)", len(labels) == 4, str(result["applied"]))

    tu = _tu_by_t(out)
    check("derived /TU correct on the confident fields",
          tu.get("First Name") == "First Name"
          and tu.get("Email_Address") == "Email Address"
          and tu.get("Phone") == "Phone"
          and tu.get("Address 2") == "Address 2",
          str(tu))
    check("auto-named/junk fields left unlabeled (no /TU)",
          tu.get("Text1") == "" and tu.get("Check Box 3") == ""
          and tu.get("Text Field 2") == "" and tu.get("untitled") == "",
          str(tu))
    check("pushbutton left unlabeled (no /TU)", tu.get("Submit") == "", str(tu))
    check("pre-existing /TU preserved", tu.get("Country") == "Select your country", str(tu))

    # Re-parse OUTPUT: the 3 derivable are labeled now; only the manual ones remain.
    res2 = parse_to_tree(str(out))
    run_analyzers(res2.tree)
    p2 = _root_props(res2.tree)
    check("output: unlabeled 9 -> 5 (manual ones remain)",
          p2.get("form_fields_unlabeled") == 5, f"got {p2.get('form_fields_unlabeled')}")
    check("output: 0 derivable left", p2.get("form_fields_derivable") == 0,
          f"got {p2.get('form_fields_derivable')}")

    # Idempotent: re-running labels nothing more.
    execs2 = execute_plans(res2.tree, [p for p in plan_remediations(res2.tree, policy) if p.flag.code.value == FLAG])
    check("idempotent: no further successful fill",
          not any(e.status.value == "success" and e.action_code.value == "FILL_FORM_FIELD_LABELS" for e in execs2))

    # Negative: without the executor's approval, the writer writes NO /TU.
    res3 = parse_to_tree(str(src))
    run_analyzers(res3.tree)  # no plan/execute -> apply_form_field_labels never set
    out3 = tmp / "noapprove.pdf"
    r3 = write_remediated_pdf(src, res3.tree, out3)
    check("no approval -> writer writes no form-field labels",
          not any(a.get("kind") == "form_field_label" for a in r3["applied"]))
    tu3 = _tu_by_t(out3)
    check("no approval -> the 3 confident fields still have no /TU",
          tu3.get("First Name") == "" and tu3.get("Phone") == "", str(tu3))

    # Clean form: a properly labeled field -> no flag.
    csrc = tmp / "clean.pdf"
    _build_pdf(csrc, [_field("City", tu="City")])
    rc = parse_to_tree(str(csrc))
    run_analyzers(rc.tree)
    check("clean form: no FORM_FIELD_UNLABELED flag",
          not any(f.code.value == FLAG for f in rc.tree.root.accessibility_flags))

    # =========================================================================
    # Adversarial-review regressions (8 confirmed wrong-name cases). Each /T
    # below must NOT become an accessible name — it is a widget type, an opaque
    # id, a code, an XFA path, or a checkbox value, all worse than no /TU.
    # =========================================================================
    REJECT = [
        "Field_TextBox", "fillText", "DateField", "EditField 3", "Datefield 1",
        "Textbox 2", "Numberfield2", "editBox", "ListBox",          # compound widget words
        "a8f3c2d1", "550e8400e29b", "b3f9",                          # hex / guid
        "Q1a", "Item3b", "line_5a",                                  # codes
        "topmostSubform[0].Page1[0].f1_01[0]",                       # XFA path
        "form1[0].#subform[1].TextField1[0]",                       # XFA path
        "X",                                                         # single letter
        "Champ 2", "Feld 3",                                         # localized auto-names
    ]
    for name in REJECT:
        check(f"reject auto/code/id name: {name!r}", _clean_pdf_field_name(name) is None,
              f"got {_clean_pdf_field_name(name)!r}")

    # Real labels must still pass (guard against over-rejection).
    ACCEPT = {
        "First Name": "First Name", "Date of Birth": "Date of Birth",
        "Address 2": "Address 2", "Email": "Email", "Phone Number": "Phone Number",
        "SSN": "SSN", "DOB": "DOB", "City": "City",
    }
    for name, want in ACCEPT.items():
        check(f"accept real label: {name!r}", _clean_pdf_field_name(name) == want,
              f"got {_clean_pdf_field_name(name)!r}")

    # Checkbox/radio (/Btn) /T is a value, not a label -> never derived.
    check("checkbox /T='Yes' not derived (value, not label)",
          derive_pdf_field_label(_field("Yes", ft="/Btn")) is None)
    check("radio /T='Male' not derived (value, not label)",
          derive_pdf_field_label(_field("Male", ft="/Btn")) is None)
    check("text /T='First Name' still derived",
          derive_pdf_field_label(_field("First Name")) == "First Name")

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
