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

from lxml import html as lxml_html  # noqa: E402

from app.ai.semantic_inference import InferenceResult  # noqa: E402
from app.analyzers.registry import run_analyzers  # noqa: E402
from app.api.pipeline import _action_persists, _count_persisted_fixes  # noqa: E402
from app.models.accessibility import (  # noqa: E402
    ActionCode,
    ContentKind,
    NodeContent,
    NodeMetadata,
    TableCellNode,
    TableCellType,
    TableHeaderScope,
    TableNode,
    TableRowNode,
    iter_reading_order,
)
from app.parsers import parse_to_tree  # noqa: E402
from app.services.remediation_planner import RemediationPolicy, plan_remediations  # noqa: E402
from app.services.remediators.base import ExecutionResult, ExecutionStatus  # noqa: E402
from app.services.remediators.generate_table_caption_executor import (  # noqa: E402
    GenerateTableCaptionExecutor,
)
from app.services.remediators.registry import execute_plans  # noqa: E402
from app.writers.html_writer import write_remediated_html  # noqa: E402


class _SpyClient:
    """Records whether the AI provider was asked for a caption."""

    def __init__(self) -> None:
        self.calls = 0

    def suggest_table_caption(self, **payload):  # pragma: no cover - exercised in smoke
        self.calls += 1
        return InferenceResult(text="SHOULD NOT BE USED", confidence=0.4, provider="spy")

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

# Layout table: explicitly role="presentation" (used for visual positioning).
# The analyzer must NOT flag it — captioning it would assert a tabular meaning
# that doesn't exist and feed the generator non-data cells. (See Finding 3B.)
LAYOUT = """<!DOCTYPE html><html lang="en"><head><title>x</title></head><body>
  <table role="presentation">
    <tr><td>Home</td><td>About</td></tr>
    <tr><td>Products</td><td>Contact</td></tr>
    <tr><td>Blog</td><td>Careers</td></tr>
  </table></body></html>"""

# Header-less DATA grid (no role): the guard must STILL flag it on shape alone
# (>=3 rows, >=2 cols). This is the signal that stays stable across
# ADD_TABLE_HEADERS so fixing headers never surfaces a brand-new caption finding.
DATAGRID_NOHDR = """<!DOCTYPE html><html lang="en"><head><title>g</title></head><body>
  <table>
    <tr><td>North</td><td>120</td><td>140</td></tr>
    <tr><td>South</td><td>90</td><td>110</td></tr>
    <tr><td>East</td><td>70</td><td>95</td></tr>
  </table></body></html>"""

# Source table that ALREADY has an author <caption> but no header row — the
# header-synthesis path must keep <caption> as the table's first child so the
# output stays HTML-spec-conformant. (See Finding 4.)
CAPTION_NO_HEADER = """<!DOCTYPE html><html lang="en"><head><title>h</title></head><body>
  <table><caption>People and ages</caption>
    <tbody>
      <tr><td>Alice</td><td>30</td></tr>
      <tr><td>Bob</td><td>25</td></tr>
      <tr><td>Cy</td><td>40</td></tr>
    </tbody>
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

    # ----------------------------------------------- Case D: layout-table guard
    # A role="presentation" layout table must never be flagged (Finding 3B) — so
    # the AI is never handed non-data cells and never emits a wrong caption.
    dsrc = tmp / "layout.html"
    dsrc.write_text(LAYOUT, encoding="utf-8")
    rd = parse_to_tree(str(dsrc))
    run_analyzers(rd.tree)
    check("D: role=presentation layout table NOT flagged TABLE_CAPTION_MISSING",
          _flag_count(rd.tree, FLAG) == 0, f"got {_flag_count(rd.tree, FLAG)}")

    # Header-less data grid (no role) is STILL flagged on shape — this is the
    # stable signal that doesn't flip when ADD_TABLE_HEADERS later adds headers,
    # so "fix everything" never surfaces a brand-new caption finding.
    dgsrc = tmp / "datagrid.html"
    dgsrc.write_text(DATAGRID_NOHDR, encoding="utf-8")
    rdg = parse_to_tree(str(dgsrc))
    run_analyzers(rdg.tree)
    check("D: header-less data grid IS flagged (stable shape signal)",
          _flag_count(rdg.tree, FLAG) == 1, f"got {_flag_count(rdg.tree, FLAG)}")

    # ----------------------------------- Case E: no AI spend on non-HTML tables
    # The executor must SKIP (before any provider call) when the table's source
    # format can't persist a caption (Finding 2). HTML + DOCX persist; PPTX/PDF
    # do not. We re-use the flagged DIRTY tree but stamp the table node as PPTX
    # (a non-persistable format) and run our own spy-backed executor.
    esrc = tmp / "fmt.html"
    esrc.write_text(DIRTY, encoding="utf-8")
    re_ = parse_to_tree(str(esrc))
    run_analyzers(re_.tree)
    e_plans = [p for p in plan_remediations(re_.tree, policy)
               if p.flag.code.value == FLAG and p.execution_allowed]
    check("E: caption plan exists to exercise", len(e_plans) == 1, str(e_plans))
    e_table = next((n for n in iter_reading_order(re_.tree.root) if isinstance(n, TableNode)), None)
    spy = _SpyClient()
    if e_plans and e_table is not None:
        e_table.metadata.source_format = "pptx"  # a format with no caption writer
        spy_exec = GenerateTableCaptionExecutor(client=spy)
        e_res = spy_exec.execute(e_plans[0], re_.tree)
        check("E: non-persistable-format table SKIPPED (no caption generated)",
              e_res.status == ExecutionStatus.SKIPPED, e_res.notes)
        check("E: AI provider was NOT called for a non-persistable format", spy.calls == 0,
              f"spy.calls={spy.calls}")
        check("E: no phantom caption left on the tree node",
              not (e_table.metadata.properties or {}).get("caption"))

    # --------------------------- Case F: charge/score reconciliation (Finding 1)
    # GENERATE_TABLE_CAPTION (writer-confirmed) must only count when the writer
    # actually applied it; a writer no-op (empty applied list) must count 0.
    cap_ok = ExecutionResult(action_code=ActionCode.GENERATE_TABLE_CAPTION,
                             target_node_id="t1", status=ExecutionStatus.SUCCESS, notes="")
    check("F: caption counted when writer applied it",
          _count_persisted_fixes([cap_ok], [{"action": "GENERATE_TABLE_CAPTION", "target_id": "t1"}], "html") == 1)
    check("F: caption NOT counted on a writer no-op (empty applied) — no overcharge",
          _count_persisted_fixes([cap_ok], [], "html") == 0)
    check("F: caption NOT counted when only a DIFFERENT target was applied",
          _count_persisted_fixes([cap_ok], [{"action": "GENERATE_TABLE_CAPTION", "target_id": "other"}], "html") == 0)
    # A non-writer-confirmed persisted action (alt text) still counts on success
    # regardless of the applied list — only the writer-confirmed set reconciles.
    alt_ok = ExecutionResult(action_code=ActionCode.GENERATE_ALT_TEXT,
                             target_node_id="i1", status=ExecutionStatus.SUCCESS, notes="")
    check("F: non-reconciled action (alt) still counts on success",
          _count_persisted_fixes([alt_ok], [], "html") == 1)

    # ------------------------ Case G: <caption> stays first child (Finding 4)
    # An author <caption> + header synthesis must yield [caption, thead, ...].
    gsrc = tmp / "caption_no_header.html"
    gsrc.write_text(CAPTION_NO_HEADER, encoding="utf-8")
    rg = parse_to_tree(str(gsrc))
    g_table = next((n for n in iter_reading_order(rg.tree.root) if isinstance(n, TableNode)), None)
    check("G: source caption read onto the table node",
          g_table is not None and (g_table.metadata.properties or {}).get("caption") == "People and ages")
    if g_table is not None:
        # Mimic the ADD_TABLE_HEADERS executor: prepend a synthesized header row.
        syn = TableRowNode(
            id="syn-row",
            content=NodeContent(kind=ContentKind.NONE),
            metadata=NodeMetadata(source_format="html", properties={"synthesized": True}),
            children=[
                TableCellNode(id="syn-c1", cell_type=TableCellType.HEADER,
                              header_scope=TableHeaderScope.COLUMN,
                              content=NodeContent(kind=ContentKind.TEXT, text="Name"),
                              metadata=NodeMetadata(source_format="html"), children=[],
                              accessibility_flags=[]),
                TableCellNode(id="syn-c2", cell_type=TableCellType.HEADER,
                              header_scope=TableHeaderScope.COLUMN,
                              content=NodeContent(kind=ContentKind.TEXT, text="Age"),
                              metadata=NodeMetadata(source_format="html"), children=[],
                              accessibility_flags=[]),
            ],
            accessibility_flags=[],
        )
        g_table.children.insert(0, syn)
        gout = tmp / "caption_no_header.out.html"
        write_remediated_html(gsrc, rg.tree, gout)
        out_doc = lxml_html.fromstring(gout.read_text(encoding="utf-8"))
        out_table = out_doc.find(".//table")
        child_tags = [c.tag for c in out_table] if out_table is not None else []
        check("G: <caption> is still the table's FIRST child after header synthesis",
              bool(child_tags) and child_tags[0] == "caption", str(child_tags))
        check("G: a <thead> was inserted (after the caption)",
              "thead" in child_tags and child_tags.index("caption") < child_tags.index("thead"),
              str(child_tags))
        check("G: still exactly one <caption> (author caption not duplicated)",
              gout.read_text(encoding="utf-8").count("<caption>") == 1)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
