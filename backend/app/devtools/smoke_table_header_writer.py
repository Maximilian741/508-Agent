"""Smoke: ADD_TABLE_HEADERS genuinely PERSISTS for both executor paths.

After the parser learned to leave a header-less data table's first row as DATA
(so TABLE_MISSING_HEADERS fires), the remediation must actually reach the file:

* Promote path — the first row reads like a header (short labels): the writer
  sets <w:trPr><w:tblHeader/></w:trPr> on that existing row.
* Synthesize path — the first row does NOT read like a header (a sentence): the
  executor inserts a placeholder header row in the tree; the writer now
  materialises it as a real new <w:tr> (tblHeader + bold cells). Previously this
  was an honesty gap — the executor reported success but no row reached the file.

Usage:
    python -m app.devtools.smoke_table_header_writer
"""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_tblhdr_')}/s.db"

from docx import Document  # noqa: E402

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.api.pipeline import _action_persists  # noqa: E402
from app.models.accessibility import AccessibilityFlagCode as F  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.services.remediation_planner import RemediationPolicy, plan_remediations  # noqa: E402
from app.services.remediators.registry import execute_plans  # noqa: E402
from app.writers import write_remediated  # noqa: E402

_POLICY = RemediationPolicy(allow_ai_actions=True, require_human_review_for_all=False)


def _remediate_table(path: Path, out: Path):
    res = parse_to_tree(str(path))
    run_analyzers(res.tree)
    plans = [p for p in plan_remediations(res.tree, _POLICY) if p.flag.code == F.TABLE_MISSING_HEADERS]
    execs = execute_plans(res.tree, plans) if plans else []
    write_remediated(path, res.tree, out, source_format=res.format)
    with zipfile.ZipFile(out) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    return execs, xml


def _build(path: Path, first_row_text: str):
    doc = Document()
    t = doc.add_table(rows=3, cols=3)
    for r in range(3):
        for c in range(3):
            cell = t.cell(r, c)
            cell.text = ""
            cell.paragraphs[0].add_run(first_row_text if r == 0 else f"val{r}{c}")
    doc.save(str(path))


def main() -> int:
    failures = 0

    def check(name, cond):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp())

    # scoring sanity: the action is credited as persisted for docx
    check("ADD_TABLE_HEADERS persists for docx (score honesty)", _action_persists("ADD_TABLE_HEADERS", "docx"))

    # --- Promote path: label-like first row ---
    p = tmp / "promote.docx"
    _build(p, "Region")
    execs, xml = _remediate_table(p, tmp / "promote_out.docx")
    check("promote: ADD_TABLE_HEADERS ran", any(e.action_code.value == "ADD_TABLE_HEADERS" and e.status.value == "success" for e in execs))
    check("promote: tblHeader persisted to output", "tblHeader" in xml)
    d = Document(str(tmp / "promote_out.docx"))
    check("promote: still 3 rows (existing row promoted, none added)", len(d.tables[0].rows) == 3)
    check("promote: output reopens cleanly", True)

    # --- Synthesize path: sentence first row (not header-like) ---
    p2 = tmp / "synth.docx"
    _build(p2, "This is a full sentence describing the row.")
    execs2, xml2 = _remediate_table(p2, tmp / "synth_out.docx")
    check("synth: ADD_TABLE_HEADERS ran", any(e.action_code.value == "ADD_TABLE_HEADERS" and e.status.value == "success" for e in execs2))
    check("synth: tblHeader persisted to output", "tblHeader" in xml2)
    check("synth: placeholder header text written", "Column 1" in xml2)
    d2 = Document(str(tmp / "synth_out.docx"))
    check("synth: a real header row was INSERTED (4 rows now)", len(d2.tables[0].rows) == 4)
    check("synth: inserted row has 3 cells matching the grid", len(d2.tables[0].rows[0].cells) == 3)
    check("synth: output reopens cleanly", True)

    # --- Jagged table: a body row has an EXTRA cell. The inserted header must
    # match the table grid (3), not the widest row (4) — otherwise a real Office
    # engine reflows the grid and mangles the table. (Expert-found defect.) ---
    from docx.oxml import OxmlElement  # noqa: E402

    doc = Document()
    t = doc.add_table(rows=3, cols=3)
    for r in range(3):
        for c in range(3):
            cell = t.cell(r, c)
            cell.text = ""
            cell.paragraphs[0].add_run("This row is a whole sentence." if r == 0 else f"v{r}{c}")
    extra = OxmlElement("w:tc")
    extra.append(OxmlElement("w:p"))
    t.rows[1]._tr.append(extra)  # row 1 now has 4 cells; grid still declares 3
    pj = tmp / "jagged.docx"
    doc.save(str(pj))
    _, xmlj = _remediate_table(pj, tmp / "jagged_out.docx")
    grid_cols = xmlj.count("<w:gridCol")
    first_tr = xmlj[xmlj.find("<w:tr") : xmlj.find("</w:tr>") + 7]
    header_tc = first_tr.count("<w:tc>") + first_tr.count("<w:tc ")
    check("jagged: grid still declares 3 columns", grid_cols == 3)
    check("jagged: inserted header width == grid (3), not widest row (4)", header_tc == 3)
    Document(str(tmp / "jagged_out.docx"))
    check("jagged: output reopens cleanly", True)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
