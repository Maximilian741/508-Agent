"""Smoke: HTML accessibility format — detection + round-trip persistence.

Pins the new HTML parser/writer end to end and PROVES the honesty invariant:
for every action in ``_PERSISTED_ACTIONS['html']`` the fix is actually baked
into the output bytes and the original flag clears on re-analysis, while
unrelated markup (scripts, styles, comments, non-ASCII text) is preserved.

  parser   -> tree with xpath locators; analyzers fire on web markup
  executor -> deterministic/AI fixes mutate the tree
  writer   -> output bytes: alt / lang / <title> / heading tag / link text /
              <th scope> all change; scripts/styles/comments untouched
  honesty  -> re-parsing the OUTPUT clears the flags; a label-less control here
              has no confident label so it stays manual (confident HTML
              auto-labeling is covered by smoke_html_form_labels)

Usage:
    python -m app.devtools.smoke_html
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_html_')}/s.db")

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.api.pipeline import _action_persists  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.services.remediation_planner import RemediationPolicy, plan_remediations  # noqa: E402
from app.services.remediators.registry import execute_plans  # noqa: E402
from app.writers.html_writer import write_remediated_html  # noqa: E402

MISSING_ALT = "MISSING_ALT_TEXT"
DEC_ALT = "DECORATIVE_IMAGE_WITH_ALT"
JUMP = "HEADING_LEVEL_JUMP"
LINK = "LINK_TEXT_NON_DESCRIPTIVE"
NO_HEADERS = "TABLE_MISSING_HEADERS"
TITLE = "DOCUMENT_TITLE_MISSING"
LANG = "DOCUMENT_LANGUAGE_MISSING"
FORM = "FORM_FIELD_UNLABELED"
CONTRAST = "LOW_CONTRAST_TEXT"


DIRTY_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>.x{color:#777;background:#fff}</style>
</head>
<body>
<!-- a comment that must survive remediation -->
<h1>Café Résumé</h1>
<h3>Subsection jumped a level</h3>
<p style="color:#888;background:#fff">Inline grey-on-white text (~3.5:1) — a real contrast failure that must be flagged.</p>
<img src="logo.png">
<img src="divider.gif" role="presentation" alt="decorative spacer junk">
<p><a href="/annual-report">click here</a></p>
<table>
  <tr><td>Name</td><td>Email</td><td>Phone</td></tr>
  <tr><td>Ada</td><td>ada@x.com</td><td>555-1</td></tr>
  <tr><td>Bo</td><td>bo@x.com</td><td>555-2</td></tr>
</table>
<table>
  <tr><td>This first row is a full sentence describing the dataset below.</td><td>Another long descriptive sentence cell here.</td></tr>
  <tr><td>10</td><td>20</td></tr>
  <tr><td>30</td><td>40</td></tr>
</table>
<form><input type="text" name="q"></form>
<script>var keep = "this script body must survive";</script>
</body>
</html>
"""


CLEAN_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Quarterly Accessibility Report</title></head>
<body>
<h1>Overview</h1>
<h2>Details</h2>
<p>An ordinary paragraph with no accessibility problems.</p>
<img src="chart.png" alt="Bar chart of quarterly revenue rising each quarter.">
<img src="spacer.gif" alt="">
<p><a href="/full-report">Read the full quarterly report</a></p>
<table>
  <tr><th scope="col">Name</th><th scope="col">Email</th></tr>
  <tr><td>Ada</td><td>ada@x.com</td></tr>
</table>
<form><label>Search <input type="text" name="q"></label></form>
</body>
</html>
"""


# Regression fixture for the adversarial-review CRITICAL: renaming the first
# same-tag sibling shifts getpath() indices for the rest, so a naive writer only
# fixes the FIRST cell/heading of each group. This has MULTIPLE promotable cells
# in row 0 and MULTIPLE heading jumps sharing a tag, plus a synthesize-path table.
MULTI_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Multi</title></head>
<body>
<h1>Report</h1>
<table>
  <tr><td>Name</td><td>Email</td><td>Phone</td></tr>
  <tr><td>Ada</td><td>a@x.com</td><td>1</td></tr>
  <tr><td>Bo</td><td>b@x.com</td><td>2</td></tr>
</table>
<h2>Alpha</h2>
<h4>Beta jumped</h4>
<h2>Gamma</h2>
<h4>Delta jumped</h4>
<table>
  <tr><td>This row is a whole sentence, not a set of column labels at all.</td><td>Another long descriptive sentence cell goes here too.</td></tr>
  <tr><td>10</td><td>20</td></tr>
  <tr><td>30</td><td>40</td></tr>
</table>
</body>
</html>
"""


def _flag_count(tree, code: str) -> int:
    n = 0

    def walk(node):
        nonlocal n
        n += sum(1 for f in node.accessibility_flags if f.code.value == code)
        for ch in node.children:
            walk(ch)

    walk(tree.root)
    return n


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="html_smoke_"))
    policy = RemediationPolicy(allow_ai_actions=True, require_human_review_for_all=False)

    # ------------------------------------------------------------------ honesty
    for code in (
        "SET_DOCUMENT_TITLE",
        "SET_DOCUMENT_LANGUAGE",
        "GENERATE_ALT_TEXT",
        "NORMALIZE_HEADING_LEVEL",
        "IMPROVE_LINK_TEXT",
        "ADD_TABLE_HEADERS",
        "FILL_FORM_FIELD_LABELS",
    ):
        check(f"honesty matrix: {code} persists for html", _action_persists(code, "html"))

    # ------------------------------------------------------------- detection
    src = tmp / "dirty.html"
    src.write_text(DIRTY_HTML, encoding="utf-8")
    res = parse_to_tree(str(src))
    run_analyzers(res.tree)

    check("missing-alt image flagged", _flag_count(res.tree, MISSING_ALT) == 1, f"got {_flag_count(res.tree, MISSING_ALT)}")
    check("role=presentation image treated as decorative (no flag)", _flag_count(res.tree, DEC_ALT) == 0, f"got {_flag_count(res.tree, DEC_ALT)}")
    check("heading level jump (h1->h3) flagged", _flag_count(res.tree, JUMP) == 1, f"got {_flag_count(res.tree, JUMP)}")
    check("non-descriptive link flagged", _flag_count(res.tree, LINK) == 1, f"got {_flag_count(res.tree, LINK)}")
    check("two header-less tables flagged", _flag_count(res.tree, NO_HEADERS) == 2, f"got {_flag_count(res.tree, NO_HEADERS)}")
    check("missing <title> flagged", _flag_count(res.tree, TITLE) == 1, f"got {_flag_count(res.tree, TITLE)}")
    check("missing <html lang> flagged", _flag_count(res.tree, LANG) == 1, f"got {_flag_count(res.tree, LANG)}")
    check("unlabeled form control flagged", _flag_count(res.tree, FORM) == 1, f"got {_flag_count(res.tree, FORM)}")
    check("inline grey-on-white (#888 on #fff, ~3.5:1) flagged for contrast",
          _flag_count(res.tree, CONTRAST) == 1, f"got {_flag_count(res.tree, CONTRAST)}")

    # ------------------------------------------------------------- remediate
    plans = plan_remediations(res.tree, policy)
    execs = execute_plans(res.tree, plans)
    successes = {e.action_code.value for e in execs if e.status.value == "success"}
    check("alt-text generation executed", "GENERATE_ALT_TEXT" in successes, str(successes))
    check("heading normalize executed", "NORMALIZE_HEADING_LEVEL" in successes, str(successes))
    check("table headers executed", "ADD_TABLE_HEADERS" in successes, str(successes))
    # This bare <input> has no nearby label (no orphan <label>, no "Name:" text,
    # no table cell), so it is correctly NOT auto-derivable and stays manual.
    # (Confident HTML auto-labeling is covered by smoke_html_form_labels.)
    check("form-field labeling SKIPS for this label-less control (honest)",
          "FILL_FORM_FIELD_LABELS" not in successes, str(successes))

    out = tmp / "dirty.fixed.html"
    result = write_remediated_html(src, res.tree, out)
    check("writer applied at least the deterministic fixes", len(result["applied"]) >= 4, str(result))
    out_text = out.read_text(encoding="utf-8")

    # --- bytes actually changed ---
    check("output declares a language", 'lang="en"' in out_text or "lang='en'" in out_text, out_text[:200])
    check("output has a non-empty <title>", "<title>" in out_text and "</title>" in out_text
          and out_text.split("<title>")[1].split("</title>")[0].strip() != "")
    check("missing-alt image now has alt baked in", 'src="logo.png"' in out_text
          and 'alt=' in out_text.split('src="logo.png"')[1].split(">")[0])
    check("heading jump fixed (an <h2> now present, no stray <h3>)", "<h2" in out_text and "<h3" not in out_text)
    check("link text rewritten (no longer 'click here')", ">click here<" not in out_text)
    check("table headers promoted/synthesized (<th> present)", "<th" in out_text)

    # --- preservation of unrelated content ---
    check("script body preserved", "this script body must survive" in out_text)
    check("style block preserved", ".x{color:#777" in out_text or "color:#777" in out_text)
    check("HTML comment preserved", "a comment that must survive remediation" in out_text)
    check("non-ASCII text preserved (Café Résumé)", "Café Résumé" in out_text)

    # ------------------------------------------------------------- re-analysis
    res2 = parse_to_tree(str(out))
    run_analyzers(res2.tree)
    check("re-parse: missing-alt flag cleared", _flag_count(res2.tree, MISSING_ALT) == 0, f"got {_flag_count(res2.tree, MISSING_ALT)}")
    check("re-parse: heading jump cleared", _flag_count(res2.tree, JUMP) == 0, f"got {_flag_count(res2.tree, JUMP)}")
    check("re-parse: table-missing-headers cleared", _flag_count(res2.tree, NO_HEADERS) == 0, f"got {_flag_count(res2.tree, NO_HEADERS)}")
    check("re-parse: title flag cleared", _flag_count(res2.tree, TITLE) == 0, f"got {_flag_count(res2.tree, TITLE)}")
    check("re-parse: language flag cleared", _flag_count(res2.tree, LANG) == 0, f"got {_flag_count(res2.tree, LANG)}")

    # ------------------------------------------------------------- idempotency
    plans2 = plan_remediations(res2.tree, policy)
    execs2 = execute_plans(res2.tree, plans2)
    out3 = tmp / "dirty.fixed2.html"
    result2 = write_remediated_html(out, res2.tree, out3)
    persisted_again = [a for a in result2["applied"]
                       if a.get("action") in {"GENERATE_ALT_TEXT", "NORMALIZE_HEADING_LEVEL",
                                              "ADD_TABLE_HEADERS", "SET_DOCUMENT_TITLE",
                                              "SET_DOCUMENT_LANGUAGE", "REMOVE_DECORATIVE_ALT_TEXT"}]
    check("idempotent: second remediation makes no NEW persisted edits",
          len(persisted_again) == 0, str(result2["applied"]))

    # ---------------------------------------- multi-element regression (CRITICAL)
    # Renaming the first <td>/<h4> must NOT cause later same-tag siblings to be
    # silently skipped (getpath index-shift). Every promotable cell and every
    # heading jump must be fixed, not just the first of each group.
    msrc = tmp / "multi.html"
    msrc.write_text(MULTI_HTML, encoding="utf-8")
    mres = parse_to_tree(str(msrc))
    run_analyzers(mres.tree)
    mplans = plan_remediations(mres.tree, policy)
    execute_plans(mres.tree, mplans)
    mout = tmp / "multi.fixed.html"
    write_remediated_html(msrc, mres.tree, mout)
    mtext = mout.read_text(encoding="utf-8")
    # All THREE row-0 cells of the promote-table become <th> (bug → only 1).
    check("multi: all 3 promotable header cells became <th> (not just the first)",
          mtext.count("<th") >= 3, f"<th count={mtext.count('<th')}")
    # Both <h4> jumps normalized — none left (bug → second <h4> survives).
    check("multi: every heading jump normalized, no <h4> remains",
          "<h4" not in mtext, "an <h4> survived (index-shift skip)")
    # Synthesize-path table got a real <thead> header row.
    check("multi: synthesize-path table gained a <thead> header row", "<thead" in mtext)
    mres2 = parse_to_tree(str(mout))
    run_analyzers(mres2.tree)
    check("multi: re-parse clears ALL heading jumps and table-header flags",
          _flag_count(mres2.tree, JUMP) == 0 and _flag_count(mres2.tree, NO_HEADERS) == 0,
          f"jump={_flag_count(mres2.tree, JUMP)} noheaders={_flag_count(mres2.tree, NO_HEADERS)}")

    # ------------------------------------------------------------- FP guard
    clean = tmp / "clean.html"
    clean.write_text(CLEAN_HTML, encoding="utf-8")
    rc = parse_to_tree(str(clean))
    run_analyzers(rc.tree)
    total_flags = sum(_flag_count(rc.tree, c) for c in
                      (MISSING_ALT, DEC_ALT, JUMP, LINK, NO_HEADERS, TITLE, LANG, FORM, CONTRAST))
    check("clean, accessible HTML raises ZERO flags", total_flags == 0, f"got {total_flags}")
    # And the writer leaves a clean doc's bytes functionally intact.
    cout = tmp / "clean.out.html"
    cres = write_remediated_html(clean, rc.tree, cout)
    check("clean doc: writer makes no persisted edits", len(cres["applied"]) == 0, str(cres["applied"]))
    ctext = cout.read_text(encoding="utf-8")
    check("clean doc: title + lang + alt preserved",
          "Quarterly Accessibility Report" in ctext and 'lang="en"' in ctext
          and "Bar chart of quarterly revenue" in ctext)

    # ------------------------------------------------------------- robustness
    broken = tmp / "broken.html"
    broken.write_bytes(b"<html><body><p>unclosed <b>bold <img src=x.png></body>")
    try:
        rb = parse_to_tree(str(broken))
        run_analyzers(rb.tree)
        pb = plan_remediations(rb.tree, policy)
        execute_plans(rb.tree, pb)
        bout = tmp / "broken.out.html"
        write_remediated_html(broken, rb.tree, bout)
        check("malformed HTML: parse+remediate+write never crash", True)
    except Exception as exc:  # pragma: no cover
        check("malformed HTML: parse+remediate+write never crash", False, repr(exc))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
