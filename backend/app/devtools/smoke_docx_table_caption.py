"""Smoke: AI-generated table caption auto-fix for DOCX (GENERATE_TABLE_CAPTION).

A Word data table with no caption gives a screen-reader user no context before
the row-by-row read-out (WCAG 1.3.1). Word's accessible caption is a paragraph
styled "Caption" adjacent to the table. This pins the new auto-fix end to end
AND the honesty invariant:

  detect   -> caption-less data table flagged TABLE_CAPTION_MISSING
  execute  -> GenerateTableCaptionExecutor derives a caption from the table's
              own headers/rows (heuristic provider here — deterministic/offline)
              and stores it in metadata.properties['caption']
  write    -> docx_writer inserts a Caption-styled <w:p> above the <w:tbl>
  honesty  -> GENERATE_TABLE_CAPTION persists for docx; re-parsing the OUTPUT
              reads the Caption paragraph back and TABLE_CAPTION_MISSING clears;
              a table that already has a Caption paragraph is untouched, and a
              writer no-op is never credited/charged.

Usage:
    python -m app.devtools.smoke_docx_table_caption
"""

from __future__ import annotations

import os
import sys
import tempfile

# Deterministic, offline AI: derive the caption from headers, no network.
os.environ["SEMANTIC_PROVIDER"] = "heuristic"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_dcap_')}/s.db")

from pathlib import Path  # noqa: E402

from docx import Document  # noqa: E402
from docx.oxml import OxmlElement  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.api.pipeline import _action_persists, _count_persisted_fixes  # noqa: E402
from app.models.accessibility import ActionCode, TableNode, iter_reading_order  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.services.remediation_planner import RemediationPolicy, plan_remediations  # noqa: E402
from app.services.remediators.base import ExecutionResult, ExecutionStatus  # noqa: E402
from app.services.remediators.registry import execute_plans  # noqa: E402
from app.writers.docx_writer import write_remediated_docx  # noqa: E402

FLAG = "TABLE_CAPTION_MISSING"


def _bold_header(cell, text):
    cell.text = text
    cell.paragraphs[0].runs[0].font.bold = True  # makes row 0 a real header row


def _strip_caption_style(doc) -> None:
    """Remove any defined Caption style — mimics a real authored .docx that
    never had a caption (Word adds the style only on first caption insert)."""
    styles_el = doc.styles.element
    for st in list(styles_el.findall(qn("w:style"))):
        if (st.get(qn("w:styleId")) or "").strip().lower() == "caption":
            styles_el.remove(st)


def _has_caption_style(doc) -> bool:
    return any(
        (st.get(qn("w:styleId")) or "").strip().lower() == "caption"
        for st in doc.styles.element.findall(qn("w:style"))
    )


def _fill_3x3(table) -> None:
    for c, txt in zip(table.rows[0].cells, ["Region", "Q1", "Q2"]):
        _bold_header(c, txt)
    for c, txt in zip(table.rows[1].cells, ["North", "120", "140"]):
        c.text = txt
    for c, txt in zip(table.rows[2].cells, ["South", "90", "110"]):
        c.text = txt


def _build_uncaptioned(path: Path) -> None:
    """A 3x3 data table (bold header row + 2 data rows), no caption."""
    doc = Document()
    table = doc.add_table(rows=3, cols=3)
    _fill_3x3(table)
    doc.save(str(path))


def _build_captioned(path: Path) -> None:
    """Same table, but preceded by a Caption-styled paragraph (control)."""
    doc = Document()
    cap = doc.add_paragraph("Quarterly sales by region")
    pPr = cap._p.get_or_add_pPr()
    pStyle = OxmlElement("w:pStyle")
    pStyle.set(qn("w:val"), "Caption")
    pPr.append(pStyle)
    table = doc.add_table(rows=3, cols=3)
    for c, txt in zip(table.rows[0].cells, ["Region", "Q1", "Q2"]):
        _bold_header(c, txt)
    for c, txt in zip(table.rows[1].cells, ["North", "120", "140"]):
        c.text = txt
    for c, txt in zip(table.rows[2].cells, ["South", "90", "110"]):
        c.text = txt
    doc.save(str(path))


def _flag_count(tree, code):
    n = 0

    def walk(node):
        nonlocal n
        n += sum(1 for f in node.accessibility_flags if f.code.value == code)
        for ch in node.children:
            walk(ch)

    walk(tree.root)
    return n


def _first_table_caption(tree):
    for node in iter_reading_order(tree.root):
        if isinstance(node, TableNode):
            return (node.metadata.properties or {}).get("caption")
    return None


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="docx_caption_smoke_"))
    policy = RemediationPolicy(allow_ai_actions=True, require_human_review_for_all=False)

    check("honesty matrix: GENERATE_TABLE_CAPTION persists for docx",
          _action_persists("GENERATE_TABLE_CAPTION", "docx"))
    check("honesty matrix: NOT credited for pptx (no writer support)",
          not _action_persists("GENERATE_TABLE_CAPTION", "pptx"))

    # ---------------------------------------------------------------- Case A
    src = tmp / "report.docx"
    _build_uncaptioned(src)
    res = parse_to_tree(str(src))
    run_analyzers(res.tree)
    check("caption-less data table flagged TABLE_CAPTION_MISSING", _flag_count(res.tree, FLAG) == 1,
          f"got {_flag_count(res.tree, FLAG)}")

    plans = plan_remediations(res.tree, policy)
    execs = execute_plans(res.tree, plans)
    ok = [e for e in execs if e.status.value == "success" and e.action_code.value == "GENERATE_TABLE_CAPTION"]
    check("GENERATE_TABLE_CAPTION executed", len(ok) == 1,
          str([(e.action_code.value, e.status.value, e.notes) for e in execs]))
    gen_caption = _first_table_caption(res.tree)
    check("caption grounded in the table's headers",
          bool(gen_caption) and any(h in gen_caption for h in ("Region", "Q1", "Q2")), str(gen_caption))

    out = tmp / "report.fixed.docx"
    result = write_remediated_docx(src, res.tree, out)
    applied = [a for a in result["applied"] if a.get("action") == "GENERATE_TABLE_CAPTION"]
    check("writer applied exactly one caption", len(applied) == 1, str(result["applied"]))

    # Honesty: charge/score reconciliation counts it (writer confirmed the edit).
    check("reconciliation counts the persisted caption",
          _count_persisted_fixes(execs, result["applied"], "docx") >= 1)

    # Honesty round-trip: re-parse the OUTPUT, the flag clears + caption present.
    res2 = parse_to_tree(str(out))
    run_analyzers(res2.tree)
    check("re-parse: Caption paragraph read back onto the table",
          _first_table_caption(res2.tree) == gen_caption, str(_first_table_caption(res2.tree)))
    check("re-parse: TABLE_CAPTION_MISSING cleared", _flag_count(res2.tree, FLAG) == 0,
          f"got {_flag_count(res2.tree, FLAG)}")

    # Idempotent: nothing to do on the captioned output.
    execs2 = execute_plans(res2.tree, plan_remediations(res2.tree, policy))
    check("idempotent: no further caption generated",
          not any(e.action_code.value == "GENERATE_TABLE_CAPTION" and e.status.value == "success" for e in execs2))
    out2 = tmp / "report.fixed2.docx"
    r2 = write_remediated_docx(out, res2.tree, out2)
    check("idempotent: writer does NOT add a second caption",
          not any(a.get("action") == "GENERATE_TABLE_CAPTION" for a in r2["applied"]), str(r2["applied"]))
    # Exactly one Caption paragraph in the final bytes.
    res3 = parse_to_tree(str(out2))
    check("output still has exactly one caption", _first_table_caption(res3.tree) == gen_caption)

    # ---------------------------------------------------------------- Case B
    bsrc = tmp / "captioned.docx"
    _build_captioned(bsrc)
    rb = parse_to_tree(str(bsrc))
    run_analyzers(rb.tree)
    check("B: already-captioned table read its caption",
          _first_table_caption(rb.tree) == "Quarterly sales by region", str(_first_table_caption(rb.tree)))
    check("B: already-captioned table NOT flagged", _flag_count(rb.tree, FLAG) == 0)
    execute_plans(rb.tree, plan_remediations(rb.tree, policy))
    bout = tmp / "captioned.out.docx"
    rbres = write_remediated_docx(bsrc, rb.tree, bout)
    check("B: writer applied no caption (nothing to do)",
          not any(a.get("action") == "GENERATE_TABLE_CAPTION" for a in rbres["applied"]))

    # ----------------------------------- Case C: reconciliation rejects no-op
    cap_ok = ExecutionResult(action_code=ActionCode.GENERATE_TABLE_CAPTION,
                             target_node_id="docx-table-1", status=ExecutionStatus.SUCCESS, notes="")
    check("C: caption NOT counted on a writer no-op (empty applied) — no overcharge",
          _count_persisted_fixes([cap_ok], [], "docx") == 0)

    # ------- Case D: undefined Caption style is created so the fix is a REAL
    # caption (not Normal body text). Build a source with NO Caption style.
    dsrc = tmp / "no_style.docx"
    ddoc = Document()
    _strip_caption_style(ddoc)
    _fill_3x3(ddoc.add_table(rows=3, cols=3))
    ddoc.save(str(dsrc))
    check("D: source genuinely has no Caption style", not _has_caption_style(Document(str(dsrc))))
    rd = parse_to_tree(str(dsrc))
    run_analyzers(rd.tree)
    check("D: caption-less table flagged", _flag_count(rd.tree, FLAG) == 1)
    execute_plans(rd.tree, plan_remediations(rd.tree, policy))
    dout = tmp / "no_style.fixed.docx"
    write_remediated_docx(dsrc, rd.tree, dout)
    check("D: output now DEFINES a real Caption style (pStyle reference resolves)",
          _has_caption_style(Document(str(dout))))
    rd2 = parse_to_tree(str(dout))
    run_analyzers(rd2.tree)
    check("D: re-parse clears the flag", _flag_count(rd2.tree, FLAG) == 0)

    # ------- Case E: a 5x2 layout grid (no role signal in DOCX) must NOT be
    # flagged — we never fabricate a data-table caption for a layout table.
    esrc = tmp / "layout.docx"
    edoc = Document()
    etable = edoc.add_table(rows=5, cols=2)
    for r, (a, b) in enumerate([("2020", "Senior Engineer"), ("2018", "Engineer"),
                                ("2016", "Junior Engineer"), ("2014", "Intern"),
                                ("2012", "Student")]):
        etable.rows[r].cells[0].text = a
        etable.rows[r].cells[1].text = b
    edoc.save(str(esrc))
    re_ = parse_to_tree(str(esrc))
    run_analyzers(re_.tree)
    check("E: 5x2 layout grid NOT flagged TABLE_CAPTION_MISSING (>=3 cols required)",
          _flag_count(re_.tree, FLAG) == 0, f"got {_flag_count(re_.tree, FLAG)}")

    # ------- Case F: a control char in the caption must not abort the write.
    fsrc = tmp / "ctrl.docx"
    _build_uncaptioned(fsrc)
    rf = parse_to_tree(str(fsrc))
    ftable = next((n for n in iter_reading_order(rf.tree.root) if isinstance(n, TableNode)), None)
    ftable.metadata.properties["caption"] = "Sales\x07 report"  # injected control char
    fout = tmp / "ctrl.fixed.docx"
    try:
        fres = write_remediated_docx(fsrc, rf.tree, fout)
        wrote = True
    except Exception as exc:  # pragma: no cover
        wrote = False
        fres = {"applied": [], "skipped": [str(exc)]}
    check("F: write completes despite a control char in the caption", wrote)
    rf2 = parse_to_tree(str(fout)) if wrote else None
    check("F: caption persisted with the control char stripped",
          bool(rf2) and _first_table_caption(rf2.tree) == "Sales report",
          str(_first_table_caption(rf2.tree)) if rf2 else "no output")

    # ------- Case G: caption between two tables belongs to the LOWER table;
    # the upper table is still correctly flagged as uncaptioned.
    gsrc = tmp / "between.docx"
    gdoc = Document()
    _fill_3x3(gdoc.add_table(rows=3, cols=3))  # table A (uncaptioned)
    capg = gdoc.add_paragraph("Caption for table B")
    pPr = capg._p.get_or_add_pPr()
    ps = OxmlElement("w:pStyle")
    ps.set(qn("w:val"), "Caption")
    pPr.append(ps)
    _fill_3x3(gdoc.add_table(rows=3, cols=3))  # table B (its caption is above it)
    gdoc.save(str(gsrc))
    rg = parse_to_tree(str(gsrc))
    run_analyzers(rg.tree)
    tables = [n for n in iter_reading_order(rg.tree.root) if isinstance(n, TableNode)]
    check("G: two tables parsed", len(tables) == 2, f"got {len(tables)}")
    check("G: upper table did NOT absorb the lower table's caption",
          not (tables[0].metadata.properties or {}).get("caption"))
    check("G: lower table reads its caption",
          (tables[1].metadata.properties or {}).get("caption") == "Caption for table B")
    check("G: exactly one TABLE_CAPTION_MISSING (the uncaptioned upper table)",
          _flag_count(rg.tree, FLAG) == 1, f"got {_flag_count(rg.tree, FLAG)}")

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
