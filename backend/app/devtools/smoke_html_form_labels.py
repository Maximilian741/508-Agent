"""Smoke: unlabeled HTML form controls get an accessible name (auto-fix).

An ``<input>``/``<select>``/``<textarea>`` with no associated label has no
accessible name — a screen reader announces only "edit" with no idea what to
type (WCAG 3.3.2 / 4.1.2). HTML used to DETECT these but never fix them. This
pins the new CONSERVATIVE auto-fix, mirroring the DOCX content-control deriver:

  parser   -> counts unlabeled controls AND how many have a confident nearby
              label (form_fields_derivable); orphan <label>, "Name: [input]"
              preceding text, or a table label cell
  analyzer -> FORM_FIELD_UNLABELED fires on the document root
  executor -> FILL_FORM_FIELD_LABELS succeeds only when >=1 is derivable
  writer   -> aria-label written on exactly the derivable controls; ambiguous
              ones (sentences / questions / headings / generic prompts) untouched
  honesty  -> FILL_FORM_FIELD_LABELS persists for html; re-parsing the OUTPUT
              shows the unlabeled count dropped by EXACTLY the derivable count,
              the writer applied exactly that many, and it is idempotent

Usage:
    python -m app.devtools.smoke_html_form_labels
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_hff_')}/s.db")

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.api.pipeline import _action_persists  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.parsers.html_parser import _parse_document  # noqa: E402
from app.services.remediation_planner import RemediationPolicy, plan_remediations  # noqa: E402
from app.services.remediators.registry import execute_plans  # noqa: E402
from app.writers.html_writer import write_remediated_html  # noqa: E402

FLAG = "FORM_FIELD_UNLABELED"

# Fixture A — counts + the three happy paths + the negatives.
FORM_A = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Form A</title></head>
<body>
  <p><label>Full name</label> <input type="text"></p>
  <p>Email: <input type="email"></p>
  <table><tr><td>Phone</td><td><input type="text"></td></tr></table>
  <p>Please complete every field below before submitting. <input type="text"></p>
  <p><label for="city">City</label> <input id="city" type="text"></p>
  <input type="hidden" name="csrf">
  <input type="submit" value="Send">
</body>
</html>
"""

# Fixture B — two controls in one line: #2 must derive "Date", not "Name ...".
FORM_B = """<!DOCTYPE html><html lang="en"><head><title>B</title></head>
<body><p>Name: <input type="text"> Date: <input type="text"></p></body></html>
"""

# Fixture C — non-labels must be REJECTED (left for manual review).
FORM_C = """<!DOCTYPE html><html lang="en"><head><title>C</title></head>
<body>
  <p>What is your name? <input type="text"></p>
  <p>Search <input type="search"></p>
  <p>Section 4 Employment <input type="text"></p>
  <table><tr><td>See the instructions on page 3 for details.</td><td><input type="text"></td></tr></table>
</body></html>
"""

# Fixture D — clean doc: every control properly labeled -> no flag.
FORM_D = """<!DOCTYPE html><html lang="en"><head><title>D</title></head>
<body>
  <p><label for="n">Name</label> <input id="n" type="text"></p>
  <p><label>Country <input type="text"></label></p>
  <input type="text" aria-label="Search the site">
</body></html>
"""


def _root_props(tree) -> dict:
    return tree.root.metadata.properties or {}


def _aria_labels(path) -> list[str]:
    doc = _parse_document(Path(path).read_bytes())
    out = []
    for tag in ("input", "select", "textarea"):
        for ctrl in doc.iter(tag):
            al = (ctrl.get("aria-label") or "").strip()
            if al:
                out.append(al)
    return out


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="html_form_"))
    policy = RemediationPolicy(allow_ai_actions=True, require_human_review_for_all=False)

    check("honesty matrix: FILL_FORM_FIELD_LABELS persists for html",
          _action_persists("FILL_FORM_FIELD_LABELS", "html"))

    # ============================================================ Fixture A
    src = tmp / "a.html"
    src.write_text(FORM_A, encoding="utf-8")
    res = parse_to_tree(str(src))
    run_analyzers(res.tree)
    props = _root_props(res.tree)
    check("A: FORM_FIELD_UNLABELED fires",
          any(f.code.value == FLAG for f in res.tree.root.accessibility_flags))
    check("A: 5 total, 4 unlabeled, 3 derivable (hidden+submit excluded)",
          props.get("form_fields_total") == 5
          and props.get("form_fields_unlabeled") == 4
          and props.get("form_fields_derivable") == 3,
          f"total={props.get('form_fields_total')} unlabeled={props.get('form_fields_unlabeled')} derivable={props.get('form_fields_derivable')}")

    plans = [p for p in plan_remediations(res.tree, policy) if p.flag.code.value == FLAG]
    fill_plans = [p for p in plans if any(a.action_code.value == "FILL_FORM_FIELD_LABELS" for a in p.actions)]
    check("A: a FILL_FORM_FIELD_LABELS plan exists", len(fill_plans) >= 1)
    execs = execute_plans(res.tree, plans)
    ok = [e for e in execs if e.status.value == "success" and e.action_code.value == "FILL_FORM_FIELD_LABELS"]
    check("A: fill executes successfully", len(ok) == 1,
          str([(e.action_code.value, e.status.value, e.notes) for e in execs]))

    out = tmp / "a.fixed.html"
    result = write_remediated_html(src, res.tree, out)
    applied = [a for a in result["applied"] if a.get("action") == "FILL_FORM_FIELD_LABELS"]
    check("A: writer applied exactly 3 labels (== derivable)", len(applied) == 3, str(result["applied"]))
    labels = _aria_labels(out)
    check("A: derived the right names",
          set(labels) == {"Full name", "Email", "Phone"}, str(labels))

    res2 = parse_to_tree(str(out))
    run_analyzers(res2.tree)
    p2 = _root_props(res2.tree)
    check("A: re-parse unlabeled 4 -> 1 (only the prose control remains)",
          p2.get("form_fields_unlabeled") == 1, f"got {p2.get('form_fields_unlabeled')}")
    check("A: re-parse 0 derivable left", p2.get("form_fields_derivable") == 0,
          f"got {p2.get('form_fields_derivable')}")

    # Idempotent: running again labels nothing more.
    execs_again = execute_plans(res2.tree, [p for p in plan_remediations(res2.tree, policy) if p.flag.code.value == FLAG])
    check("A: idempotent (no further successful fill)",
          not any(e.status.value == "success" and e.action_code.value == "FILL_FORM_FIELD_LABELS" for e in execs_again))

    # ============================================================ Fixture B
    srcb = tmp / "b.html"
    srcb.write_text(FORM_B, encoding="utf-8")
    rb = parse_to_tree(str(srcb))
    run_analyzers(rb.tree)
    pb = _root_props(rb.tree)
    check("B: 2 unlabeled, 2 derivable",
          pb.get("form_fields_unlabeled") == 2 and pb.get("form_fields_derivable") == 2,
          f"unlabeled={pb.get('form_fields_unlabeled')} derivable={pb.get('form_fields_derivable')}")
    execute_plans(rb.tree, [p for p in plan_remediations(rb.tree, policy) if p.flag.code.value == FLAG])
    outb = tmp / "b.fixed.html"
    write_remediated_html(srcb, rb.tree, outb)
    lb = _aria_labels(outb)
    check("B: control #1='Name', control #2='Date' (not the concatenation)",
          lb == ["Name", "Date"], str(lb))

    # ============================================================ Fixture C
    srcc = tmp / "c.html"
    srcc.write_text(FORM_C, encoding="utf-8")
    rc = parse_to_tree(str(srcc))
    run_analyzers(rc.tree)
    pc = _root_props(rc.tree)
    check("C: non-labels rejected (4 unlabeled, 0 derivable)",
          pc.get("form_fields_unlabeled") == 4 and pc.get("form_fields_derivable") == 0,
          f"unlabeled={pc.get('form_fields_unlabeled')} derivable={pc.get('form_fields_derivable')}")
    # And the writer writes nothing even if asked.
    execute_plans(rc.tree, [p for p in plan_remediations(rc.tree, policy) if p.flag.code.value == FLAG])
    outc = tmp / "c.fixed.html"
    resc = write_remediated_html(srcc, rc.tree, outc)
    check("C: writer applied 0 labels (all ambiguous)",
          len([a for a in resc["applied"] if a.get("action") == "FILL_FORM_FIELD_LABELS"]) == 0,
          str(resc["applied"]))

    # ============================================================ Fixture D
    srcd = tmp / "d.html"
    srcd.write_text(FORM_D, encoding="utf-8")
    rd = parse_to_tree(str(srcd))
    run_analyzers(rd.tree)
    check("D: clean doc has no form-field flag",
          not any(f.code.value == FLAG for f in rd.tree.root.accessibility_flags))

    # ====================================== negative: no approval -> no writes
    src_na = tmp / "noapprove.html"
    src_na.write_text(FORM_A, encoding="utf-8")
    rna = parse_to_tree(str(src_na))
    run_analyzers(rna.tree)
    out_na = tmp / "noapprove.out.html"
    res_na = write_remediated_html(src_na, rna.tree, out_na)  # never executed the plan
    check("no approval -> writer adds no aria-labels",
          len([a for a in res_na["applied"] if a.get("action") == "FILL_FORM_FIELD_LABELS"]) == 0
          and _aria_labels(out_na) == [],
          str(res_na["applied"]))

    # =========================================================================
    # Fixture E — wrong-label regressions (adversarial review). Every one of
    # these MUST stay manual: deriving a name here would be misleading, which is
    # worse than no name. Combined into one doc; expect 0 derivable, 0 written.
    # =========================================================================
    def _derive_and_write(name: str, payload) -> tuple[int, list[str]]:
        sp = tmp / name
        if isinstance(payload, bytes):
            sp.write_bytes(payload)
        else:
            sp.write_text(payload, encoding="utf-8")
        r = parse_to_tree(str(sp))
        run_analyzers(r.tree)
        derivable = int(_root_props(r.tree).get("form_fields_derivable") or 0)
        execute_plans(r.tree, [p for p in plan_remediations(r.tree, policy) if p.flag.code.value == FLAG])
        op = tmp / (name + ".out.html")
        write_remediated_html(sp, r.tree, op)
        return derivable, _aria_labels(op)

    WRONG = """<!DOCTYPE html><html lang="en"><head><title>E</title></head><body>
      <form><fieldset><legend>Payment details</legend><input type="text" name="card"></fieldset></form>
      <form><div><h2>Contact Information</h2><input type="text" name="x"></div></form>
      <form><p><label for="cb">I agree to the Terms</label> <input type="text" name="coupon"> <input id="cb" type="checkbox"></p></form>
      <form><div><label>Legal name</label><input type="text" name="first"><input type="text" name="last"></div></form>
      <form><p>Gender <input type="radio" name="g" value="m"> Male <input type="radio" name="g" value="f"> Female</p></form>
      <form><p>All fields required <input type="text" name="y"></p></form>
    </body></html>"""
    e_deriv, e_labels = _derive_and_write("e.html", WRONG)
    check("E: NONE of the misleading cases are auto-derived (0 derivable)",
          e_deriv == 0, f"derivable={e_deriv}")
    check("E: no aria-labels written for any misleading case",
          e_labels == [], str(e_labels))
    # Specifically: the coupon box must never inherit the consent checkbox's name.
    check("E: coupon box not mislabeled 'I agree to the Terms'",
          "I agree to the Terms" not in e_labels, str(e_labels))

    # legend/heading/group each in isolation (clearer failure messages).
    for nm, payload, why in [
        ("legend", '<!DOCTYPE html><html lang=en><head><title>l</title></head><body><form><fieldset><legend>Payment details</legend><input type="text" name="card"></fieldset></form></body></html>', "<legend> is a group caption"),
        ("heading", '<!DOCTYPE html><html lang=en><head><title>h</title></head><body><form><div><h2>Contact Information</h2><input type="text" name="x"></div></form></body></html>', "<h2> is a section title"),
        ("group", '<!DOCTYPE html><html lang=en><head><title>g</title></head><body><form><div><label>Legal name</label><input type="text" name="first"><input type="text" name="last"></div></form></body></html>', "shared group label"),
    ]:
        d, l = _derive_and_write(nm + ".html", payload)
        check(f"E: {why} -> 0 derivable, nothing written", d == 0 and l == [], f"derivable={d} labels={l}")

    # =========================================================================
    # Fixture F — text-normalization (these SHOULD derive, but cleaned).
    # =========================================================================
    # &nbsp; + newline collapse to a single space.
    d, l = _derive_and_write("nbsp.html",
        '<!DOCTYPE html><html lang=en><head><title>n</title></head><body><form><p>Full&nbsp;Name\n   <input type="text" name="fn"></p></form></body></html>')
    check("F: &nbsp;/newline normalized to 'Full Name'", l == ["Full Name"], f"derivable={d} labels={l}")
    # Trailing currency symbol stripped (single control, so not a group label).
    d, l = _derive_and_write("amt.html",
        '<!DOCTYPE html><html lang=en><head><title>a</title></head><body><form><p>Amount $ <input type="text" name="amount"></p></form></body></html>')
    check("F: trailing '$' stripped -> 'Amount'", l == ["Amount"], f"derivable={d} labels={l}")
    # UTF-8 with NO <meta charset>: the accented label must NOT be mojibake.
    utf8_no_charset = '<!DOCTYPE html><html lang="fr"><head><title>u</title></head><body><form><p>Prénom: <input type="text" name="p"></p></form></body></html>'.encode("utf-8")
    d, l = _derive_and_write("utf8.html", utf8_no_charset)
    check("F: charset-less UTF-8 derives correct accented label 'Prénom'",
          l == ["Prénom"], f"derivable={d} labels={l!r}")

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
