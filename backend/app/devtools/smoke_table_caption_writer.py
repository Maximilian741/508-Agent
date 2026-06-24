"""Smoke: AI-generated table <caption> auto-fix for HTML (GENERATE_TABLE_CAPTION).

A data table with no caption and no nearby label gives a screen-reader user no
context before the row-by-row read-out (WCAG 1.3.1). This pins the new auto-fix
end to end AND the honesty invariant:

  detect   -> captionless data table flagged TABLE_CAPTION_MISSING
  execute  -> GenerateTableCaptionExecutor derives a caption from the table's
              own headers/rows (heuristic provider here, deterministic/offline)
              and stores it in metadata.properties['caption']
  write    -> html_writer inserts <caption> as the table's first child
  honesty  -> GENERATE_TABLE_CAPTION persists for html; re-parsing the OUTPUT
              reads the <caption> back and TABLE_CAPTION_MISSING clears; tables
              that already have a caption or a preceding label are untouched

Usage:
    python -m app.devtools.smoke_table_caption_writer
"""

from __future__ import annotations

import os
import sys
import tempfile

# Deterministic, offline AI: derive the caption from headers, no network.
os.environ["SEMANTIC_PROVIDER"] = "heuristic"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_cap_')}/s.db")

from pathlib import Path  # noqa: E402

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.api.pipeline import _action_persists  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.services.remediation_planner import RemediationPolicy, plan_remediations  # noqa: E402
from app.services.remediators.registry import execute_plans  # noqa: E402
from app.writers.html_writer import write_remediated_html  # noqa: E402

FLAG = "TABLE_CAPTION_MISSING"

# A real data table: header row + 2 data rows, NO <caption>, NO preceding label.
DIRTY = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Report</title></head>
<body>
  <table>
    <tr><th>Region</th><th>Q1</th><th>Q2</th></tr>
    <tr><td>North</td><td>120</td><td>140</td></tr>
    <tr><td>South</td><td>90</td><td>110</td></tr>
  </table>
</body></html>
"""

# Already-captioned table (control) — must never get a second caption.
CAPTIONED = """<!DOCTYPE html><html lang="en"><head><title>c</title></head><body>
  <table><caption>Existing caption</caption>
    <tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr><tr><td>3</td><td>4</td></tr>
  </table></body></html>"""

# Table with a preceding <p> label (control) — analyzer should not flag it.
LABELED = """<!DOCTYPE html><html lang="en"><head><title>l</title></head><body>
  <p>Quarterly sales by region</p>
  <table>
    <tr><th>Region</th><th>Q1</th></tr><tr><td>North</td><td>120</td></tr><tr><td>South</td><td>90</td></tr>
  </table></body></html>"""


def _flag_count(tree, code):
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

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="caption_smoke_"))
    policy = RemediationPolicy(allow_ai_actions=True, require_human_review_for_all=False)

    check("honesty matrix: GENERATE_TABLE_CAPTION persists for html",
          _action_persists("GENERATE_TABLE_CAPTION", "html"))
    check("honesty matrix: NOT credited for pdf (no writer support)",
          not _action_persists("GENERATE_TABLE_CAPTION", "pdf"))

    # ---------------------------------------------------------------- Case A
    src = tmp / "report.html"
    src.write_text(DIRTY, encoding="utf-8")
    res = parse_to_tree(str(src))
    run_analyzers(res.tree)
    check("captionless table flagged TABLE_CAPTION_MISSING", _flag_count(res.tree, FLAG) == 1,
          f"got {_flag_count(res.tree, FLAG)}")

    plans = plan_remediations(res.tree, policy)
    execs = execute_plans(res.tree, plans)
    ok = [e for e in execs if e.status.value == "success" and e.action_code.value == "GENERATE_TABLE_CAPTION"]
    check("GENERATE_TABLE_CAPTION executed", len(ok) == 1,
          str([(e.action_code.value, e.status.value, e.notes) for e in execs]))

    out = tmp / "report.fixed.html"
    result = write_remediated_html(src, res.tree, out)
    applied = [a for a in result["applied"] if a.get("action") == "GENERATE_TABLE_CAPTION"]
    check("writer inserted exactly one caption", len(applied) == 1, str(result["applied"]))
    out_text = out.read_text(encoding="utf-8")
    check("output contains a <caption>", "<caption>" in out_text, out_text)
    # Heuristic caption is derived from the headers (grounded, not fabricated).
    check("caption grounded in table headers (mentions a header)",
          any(h in out_text for h in ("Region", "Q1", "Q2")), out_text)

    # Honesty round-trip: re-parse the OUTPUT, the flag clears.
    res2 = parse_to_tree(str(out))
    run_analyzers(res2.tree)
    check("re-parse: TABLE_CAPTION_MISSING cleared", _flag_count(res2.tree, FLAG) == 0,
          f"got {_flag_count(res2.tree, FLAG)}")

    # Idempotent: nothing to do on the captioned output.
    execs2 = execute_plans(res2.tree, plan_remediations(res2.tree, policy))
    check("idempotent: no further caption generated",
          not any(e.action_code.value == "GENERATE_TABLE_CAPTION" and e.status.value == "success" for e in execs2))

    # ---------------------------------------------------------------- Case B
    bsrc = tmp / "captioned.html"
    bsrc.write_text(CAPTIONED, encoding="utf-8")
    rb = parse_to_tree(str(bsrc))
    run_analyzers(rb.tree)
    check("B: already-captioned table NOT flagged", _flag_count(rb.tree, FLAG) == 0)
    execute_plans(rb.tree, plan_remediations(rb.tree, policy))
    bout = tmp / "captioned.out.html"
    rbres = write_remediated_html(bsrc, rb.tree, bout)
    check("B: no caption action applied (nothing to do)",
          not any(a.get("action") == "GENERATE_TABLE_CAPTION" for a in rbres["applied"]))
    check("B: output still has exactly one <caption>",
          bout.read_text(encoding="utf-8").count("<caption>") == 1)

    # ---------------------------------------------------------------- Case C
    lsrc = tmp / "labeled.html"
    lsrc.write_text(LABELED, encoding="utf-8")
    rl = parse_to_tree(str(lsrc))
    run_analyzers(rl.tree)
    check("C: table with a preceding label NOT flagged (no over-eager caption)",
          _flag_count(rl.tree, FLAG) == 0)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
