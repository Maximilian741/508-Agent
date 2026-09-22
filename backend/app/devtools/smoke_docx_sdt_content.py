"""Smoke: content inside Word content controls (w:sdt) is seen AND fixable.

Government templates — our exact market — wrap sections, paragraphs and
repeating table rows in content controls. python-docx's ``doc.paragraphs`` /
``doc.tables`` / ``table.rows`` read only direct children, so everything
inside an SDT was invisible: the audited copy of a form analyzed with FEWER
findings than the same document without the controls (the auditor measured 8
violation codes vs 3), its fixable issues were never detected, and a "clean"
report went out for a document that was not clean.

The fix is a set of shared SDT-descending iterators used by BOTH the parser
and the writer's id-pairing indexes — they must walk identically or fixes
land on the wrong elements. Pinned here:

  * the same content built PLAIN and SDT-WRAPPED yields the same violation
    codes and the same node count
  * remediating the SDT version actually persists fixes INSIDE the controls:
    a "click here" link inside an SDT gets new link text in the output XML,
    and a typed fake list inside an SDT becomes a real list
  * a table whose data rows are wrapped in a repeating-section SDT keeps ALL
    its rows (plain 4 rows == SDT 4 rows), so TABLE_MISSING_HEADERS logic
    sees the real shape

Usage:
    python -m app.devtools.smoke_docx_sdt_content
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_sdt_')}/s.db")
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["SEMANTIC_PROVIDER"] = "heuristic"

from pathlib import Path  # noqa: E402

import docx  # noqa: E402
from lxml import etree  # noqa: E402

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.models.accessibility import iter_reading_order  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.services.remediation_engine import RemediationEngine  # noqa: E402
from app.services.remediation_planner import RemediationPolicy  # noqa: E402
from app.writers.docx_writer import write_remediated_docx  # noqa: E402

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_POL = RemediationPolicy(allow_ai_actions=True, require_human_review_for_all=False)


def _build(path: Path) -> None:
    """H1, jumped H4, a 'click here' hyperlink, typed fake bullets, and a
    4-row table with an unmarked header row."""
    d = docx.Document()
    d.add_heading("Grant Application Form", level=1)
    d.add_heading("Applicant details", level=4)  # heading level JUMP 1 -> 4
    p = d.add_paragraph("For instructions, see ")
    # a real w:hyperlink with generic text
    part = d.part
    # The address carries real words, so the offline rules can name the link
    # from it ("Grant application instructions"); a one-word slug like
    # "/instructions" is refused as not clearly better than "click here".
    r_id = part.relate_to("https://example.gov/grant-application-instructions",
                          "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                          is_external=True)
    link = p._p.makeelement(f"{_W}hyperlink", {f"{{http://schemas.openxmlformats.org/officeDocument/2006/relationships}}id": r_id})
    run = p._p.makeelement(f"{_W}r", {})
    t = p._p.makeelement(f"{_W}t", {})
    t.text = "click here"
    run.append(t)
    link.append(run)
    p._p.append(link)
    d.add_paragraph("- provide your full legal name")   # typed fake list
    d.add_paragraph("- provide your mailing address")
    tbl = d.add_table(rows=4, cols=3)
    for c in range(3):
        tbl.cell(0, c).text = f"Column {c + 1}"
    for r in range(1, 4):
        for c in range(3):
            tbl.cell(r, c).text = f"v{r}{c}"
    d.save(str(path))


def _wrap_in_sdt(path: Path, wrap_table_rows: bool = True) -> None:
    """Wrap everything after the H1 in one body-level SDT, and the table's
    data rows in a nested repeating-section SDT — the shape Word emits."""
    with zipfile.ZipFile(path) as z:
        items = {n: z.read(n) for n in z.namelist()}
    root = etree.fromstring(items["word/document.xml"])
    body = root.find(f"{_W}body")
    blocks = [el for el in body if el.tag in (f"{_W}p", f"{_W}tbl")]
    to_wrap = blocks[1:]  # everything after the H1
    sdt = etree.SubElement(body, f"{_W}sdt")
    pr = etree.SubElement(sdt, f"{_W}sdtPr")
    alias = etree.SubElement(pr, f"{_W}alias")
    alias.set(f"{_W}val", "Form section")
    content = etree.SubElement(sdt, f"{_W}sdtContent")
    anchor_index = list(body).index(to_wrap[0])
    for el in to_wrap:
        body.remove(el)
        content.append(el)
    body.remove(sdt)
    body.insert(anchor_index, sdt)
    if wrap_table_rows:
        tbl = content.find(f"{_W}tbl")
        trs = tbl.findall(f"{_W}tr")
        row_sdt = etree.SubElement(tbl, f"{_W}sdt")
        etree.SubElement(row_sdt, f"{_W}sdtPr")
        row_content = etree.SubElement(row_sdt, f"{_W}sdtContent")
        for tr in trs[1:]:  # data rows into a repeating-section control
            tbl.remove(tr)
            row_content.append(tr)
        tbl.remove(row_sdt)
        tbl.append(row_sdt)
    items["word/document.xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in items.items():
            z.writestr(n, b)
    path.write_bytes(buf.getvalue())


def _analyze(path: Path):
    res = parse_to_tree(str(path))
    run_analyzers(res.tree)
    codes = set()
    nodes = 0
    for n in iter_reading_order(res.tree.root):
        nodes += 1
        for f in n.accessibility_flags or []:
            codes.add(f.code.value)
    return res.tree, codes, nodes


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="508_sdt_"))
    plain = tmp / "plain.docx"
    sdt = tmp / "sdt.docx"
    _build(plain)
    _build(sdt)
    _wrap_in_sdt(sdt)

    _tree_p, codes_p, nodes_p = _analyze(plain)
    tree_s, codes_s, nodes_s = _analyze(sdt)
    check("plain fixture finds the expected issue mix",
          {"HEADING_LEVEL_JUMP", "LINK_TEXT_NON_DESCRIPTIVE", "LIST_STRUCTURE_INVALID"} <= codes_p, str(sorted(codes_p)))
    # SDT adds its own unlabeled-control signal; compare after removing it.
    check("SDT-wrapped content yields the SAME violation codes (was: most vanished)",
          codes_p <= codes_s, f"plain={sorted(codes_p)} sdt={sorted(codes_s)}")
    check("SDT-wrapped content yields the SAME node count", nodes_p == nodes_s, f"{nodes_p} vs {nodes_s}")

    # Table shape survives the repeating-section control.
    from app.models.accessibility import TableNode

    t_s = [n for n in iter_reading_order(tree_s.root) if isinstance(n, TableNode)]
    check("the table inside the SDT keeps all 4 rows (data rows were invisible)",
          len(t_s) == 1 and len(t_s[0].children) == 4, str([len(t.children) for t in t_s]))

    # ---- remediate the SDT version: fixes must land INSIDE the controls ----
    eng = RemediationEngine(policy=_POL)
    eng.detect_violations(tree_s)
    eng.execute(tree_s)
    out = tmp / "sdt-fixed.docx"
    rep = write_remediated_docx(sdt, tree_s, out)
    with zipfile.ZipFile(out) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    check("writer applied fixes (non-empty applied list)", len(rep.get("applied", [])) > 0, str(rep.get("skipped"))[:200])
    check("the 'click here' link INSIDE the SDT got real link text in the output",
          "click here" not in xml, "still contains 'click here'")
    check("the typed fake list INSIDE the SDT became a real numbered/bulleted list",
          xml.count("<w:numPr>") >= 2, f"numPr count={xml.count('<w:numPr>')}")
    reopened = docx.Document(str(out))
    check("output re-opens", reopened is not None)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
