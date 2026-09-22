"""Smoke: the Word shapes real customers upload are read correctly, fixed in
the right place, and never come back as a file Word cannot open.

Each case reproduces something the robustness corpus (content controls,
tracked changes, nested tables, text boxes, 500 pages) showed going wrong:

  * tracked changes — python-docx's ``paragraph.text`` drops text inside
    ``w:ins`` and inline content controls: "work up to <ins>three</ins> days"
    read "work up to  days", and a paragraph that is one tracked insertion
    read as empty and was never audited. Deleted text must stay out.
  * a Heading-styled paragraph with its own Word numbering is a HEADING (it
    was swallowed as a list item, breaking the outline)
  * a table nested in a cell is reported (TABLE_NESTED could never fire on a
    Word file), and a header fix for the INNER table lands on the inner
    table's row — the outer table is untouched
  * a picture in a table cell (a form's logo cell) is found and its alt lands
  * a modern text box is stored twice (mc:Choice + mc:Fallback); its "click
    here" is ONE finding, not two
  * schema order: a header row whose trPr carries a tracked-change marker,
    styles.xml whose docDefaults has only pPrDefault, and a Mac numbering
    part ending in numIdMacAtCleanup all stay in schema order after a fix
  * nothing unapproved is rewritten: a bold first row and a custom heading
    style are left alone when only the title is fixed
  * the output gate: if the saved file does not re-open, the SOURCE bytes are
    restored, nothing is reported applied, and the pipeline counts 0

Usage:
    python -m app.devtools.smoke_docx_robustness
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_drb_')}/s.db")
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["SEMANTIC_PROVIDER"] = "heuristic"

from docx import Document  # noqa: E402
from docx.oxml import OxmlElement, parse_xml  # noqa: E402
from docx.oxml.ns import nsdecls, qn  # noqa: E402
from docx.shared import Inches  # noqa: E402
from lxml import etree  # noqa: E402
from PIL import Image  # noqa: E402

import app.writers.docx_writer as docx_writer  # noqa: E402
from app.analyzers.registry import run_analyzers  # noqa: E402
from app.api.pipeline import _count_persisted_fixes  # noqa: E402
from app.models.accessibility import (  # noqa: E402
    HeadingNode,
    ImageNode,
    LinkNode,
    ListItemNode,
    ParagraphNode,
    TableCellType,
    TableNode,
    iter_reading_order,
)
from app.parsers import parse_to_tree  # noqa: E402
from app.services.remediation_planner import RemediationPolicy, plan_remediations  # noqa: E402
from app.services.remediators.registry import execute_plans  # noqa: E402
from app.writers.docx_writer import write_remediated_docx  # noqa: E402

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
POL = RemediationPolicy(allow_ai_actions=True, require_human_review_for_all=False)


def _png(seed: int) -> io.BytesIO:
    b = io.BytesIO()
    Image.new("RGB", (30 + seed, 20), (seed * 20 % 255, 80, 140)).save(b, "PNG")
    b.seek(0)
    return b


def _ins(text: str, rid: str = "1"):
    ins = OxmlElement("w:ins")
    ins.set(qn("w:id"), rid)
    ins.set(qn("w:author"), "Reviewer")
    ins.set(qn("w:date"), "2026-09-01T00:00:00Z")
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = text
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    r.append(t)
    ins.append(r)
    return ins


def _del(text: str, rid: str = "2"):
    d = OxmlElement("w:del")
    d.set(qn("w:id"), rid)
    d.set(qn("w:author"), "Reviewer")
    d.set(qn("w:date"), "2026-09-01T00:00:00Z")
    r = OxmlElement("w:r")
    t = OxmlElement("w:delText")
    t.text = text
    r.append(t)
    d.append(r)
    return d


def _nodes(tree, kind):
    return [n for n in iter_reading_order(tree.root) if isinstance(n, kind)]


def _codes(tree):
    return [f.code.value for n in iter_reading_order(tree.root) for f in n.accessibility_flags]


def _rewrite_part(path: Path, name: str, fn) -> None:
    with zipfile.ZipFile(path) as z:
        items = {n: z.read(n) for n in z.namelist()}
    root = etree.fromstring(items[name])
    fn(root)
    items[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in items.items():
            z.writestr(n, b)


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="508_drb_"))

    # ===== 1. Tracked changes =================================================
    d = Document()
    d.core_properties.title = "Remote Work Policy"
    d.add_heading("Remote Work Policy", 1)
    p = d.add_paragraph("Employees may work remotely up to ")
    p._p.append(_ins("three", "11"))
    p._p.append(_del("two", "12"))
    p.add_run(" days per week.")
    whole = d.add_paragraph()
    whole._p.append(_ins("New: equipment stipends are reviewed annually.", "13"))
    gone = d.add_paragraph()
    gone._p.append(_del("This whole sentence was removed by the reviewer.", "14"))
    src = tmp / "tracked.docx"
    d.save(str(src))
    res = parse_to_tree(str(src))
    texts = [n.content.text for n in _nodes(res.tree, ParagraphNode)]
    check("tracked: inserted word is part of the sentence",
          "Employees may work remotely up to three days per week." in texts, str(texts))
    check("tracked: a paragraph that is one tracked insertion is audited",
          "New: equipment stipends are reviewed annually." in texts, str(texts))
    check("tracked: deleted text is not treated as content",
          not any("removed by the reviewer" in t or "twodays" in t or " two " in t for t in texts), str(texts))

    # ===== 2. Numbered Heading paragraph is a heading ========================
    d = Document()
    d.core_properties.title = "Numbered headings"
    h1 = d.add_heading("Introduction", 1)
    h2 = d.add_heading("Scope", 2)
    for h in (h1, h2):
        numpr = parse_xml(f'<w:numPr {nsdecls("w")}><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>')
        h._p.get_or_add_pPr().append(numpr)
    d.add_paragraph("Body text of the scope section that is long enough to read as prose.")
    src = tmp / "numbered_headings.docx"
    d.save(str(src))
    res = parse_to_tree(str(src))
    run_analyzers(res.tree)
    hs = [(n.content.text, n.level) for n in _nodes(res.tree, HeadingNode)]
    check("numbered headings: both are HeadingNodes (not list items)",
          hs == [("Introduction", 1), ("Scope", 2)] and not _nodes(res.tree, ListItemNode), str(hs))
    check("numbered headings: no invented heading jump", "HEADING_LEVEL_JUMP" not in _codes(res.tree))

    # ===== 2b. Outline levels make headings (custom heading styles) ==========
    d = Document()
    d.core_properties.title = "Agency template"
    agency = d.styles.add_style("Agency Heading One", 1)
    agency.base_style = d.styles["Heading 1"]            # inherits outline level 0
    d.add_paragraph("Program Overview", style="Agency Heading One")
    d.add_paragraph("Body text under the overview that is long enough to be prose.")
    direct = d.add_paragraph("Eligibility")               # Normal + direct "Level 2"
    lvl = OxmlElement("w:outlineLvl"); lvl.set(qn("w:val"), "1")
    direct._p.get_or_add_pPr().append(lvl)
    body_lvl = d.add_paragraph("Plain body text explicitly marked as body level text here.")
    lvl9 = OxmlElement("w:outlineLvl"); lvl9.set(qn("w:val"), "9")
    body_lvl._p.get_or_add_pPr().append(lvl9)
    src = tmp / "outline_levels.docx"
    d.save(str(src))
    res = parse_to_tree(str(src))
    run_analyzers(res.tree)
    hs = [(n.content.text, n.level) for n in _nodes(res.tree, HeadingNode)]
    check("outline levels: a style based on Heading 1 and a direct Level 2 are headings; Level 9 is body",
          hs == [("Program Overview", 1), ("Eligibility", 2)], str(hs))
    check("outline levels: no false 'document has no headings'", "DOCUMENT_NO_HEADINGS" not in _codes(res.tree))

    # ===== 2c. Internal links (TOC entries) point at bookmarks ================
    d = Document()
    d.core_properties.title = "Handbook"
    toc = d.add_paragraph()
    for anchor, label in (("_Toc100", "Introduction"), ("_Toc999", "Missing section")):
        hl = OxmlElement("w:hyperlink"); hl.set(qn("w:anchor"), anchor)
        r = OxmlElement("w:r"); t = OxmlElement("w:t"); t.text = label; r.append(t); hl.append(r)
        toc._p.append(hl)
    fld = OxmlElement("w:fldSimple"); fld.set(qn("w:instr"), ' HYPERLINK \\l "_Toc100" ')
    fr = OxmlElement("w:r"); ft = OxmlElement("w:t"); ft.text = "Back to the introduction"; fr.append(ft); fld.append(fr)
    d.add_paragraph()._p.append(fld)
    h = d.add_heading("Introduction", 1)
    bs = OxmlElement("w:bookmarkStart"); bs.set(qn("w:id"), "0"); bs.set(qn("w:name"), "_Toc100")
    be = OxmlElement("w:bookmarkEnd"); be.set(qn("w:id"), "0")
    h._p.insert(1, bs)
    h._p.append(be)
    d.add_paragraph("Introduction body text that is long enough to be prose in this handbook.")
    src = tmp / "toc_links.docx"
    d.save(str(src))
    res = parse_to_tree(str(src))
    run_analyzers(res.tree)
    lk = {n.content.text: n for n in _nodes(res.tree, LinkNode)}
    broken = {n.content.text for n in lk.values() if any(f.code.value == "LINK_TARGET_BROKEN" for f in n.accessibility_flags)}
    check("toc: an entry pointing at an existing bookmark is a valid internal link",
          lk.get("Introduction") is not None and lk["Introduction"].target == "#_Toc100"
          and "Introduction" not in broken, str({k: v.target for k, v in lk.items()}))
    check("toc: HYPERLINK \\l field resolves to the bookmark, not to the switch",
          lk.get("Back to the introduction") is not None and lk["Back to the introduction"].target == "#_Toc100")
    check("toc: an entry pointing at a bookmark that does not exist IS reported broken",
          broken == {"Missing section"}, str(broken))

    # ===== 3. Nested table + picture in a cell ================================
    d = Document()
    d.core_properties.title = "Grant Budget"
    d.add_heading("Grant Budget", 1)
    outer = d.add_table(rows=2, cols=2)
    outer.cell(0, 0).text = "Personnel"
    outer.cell(0, 0).paragraphs[0].runs[0].bold = True
    outer.cell(0, 1).text = "Breakdown"
    outer.cell(0, 1).paragraphs[0].runs[0].bold = True
    inner = outer.cell(1, 1).add_table(rows=3, cols=3)
    for r, row in enumerate([["Role", "FTE", "Cost"], ["PI", "0.25", "42,000"], ["Analyst", "1.0", "88,000"]]):
        for c, v in enumerate(row):
            inner.cell(r, c).text = v
    outer.cell(1, 0).paragraphs[0].add_run().add_picture(_png(3), width=Inches(0.5))
    src = tmp / "nested.docx"
    d.save(str(src))
    res = parse_to_tree(str(src))
    run_analyzers(res.tree)
    tables = _nodes(res.tree, TableNode)
    nested = [t for t in tables if (t.metadata.properties or {}).get("nested_table")]
    check("nested: the inner table is a node and TABLE_NESTED fires once",
          len(tables) == 2 and len(nested) == 1 and _codes(res.tree).count("TABLE_NESTED") == 1,
          str(([tb.id for tb in tables], _codes(res.tree))))
    cell_imgs = [n for n in _nodes(res.tree, ImageNode) if (n.metadata.properties or {}).get("docx_story") == "table"]
    check("cell picture: found and flagged", len(cell_imgs) == 1
          and any(f.code.value == "MISSING_ALT_TEXT" for f in cell_imgs[0].accessibility_flags))
    check("nested: inner data table without a header row is flagged",
          any(f.code.value == "TABLE_MISSING_HEADERS" for f in nested[0].accessibility_flags) if nested else False)
    # Fix: promote the inner table's first row; give the cell picture alt.
    first_row = nested[0].children[0]
    for c in first_row.children:
        c.cell_type = TableCellType.HEADER
    cell_imgs[0].alt_text = "Agency seal"
    out = tmp / "nested_fixed.docx"
    wr = write_remediated_docx(src, res.tree, out)
    with zipfile.ZipFile(out) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    outer_tbl = root.find(f".//{W}body/{W}tbl")
    inner_tbl = outer_tbl.find(f".//{W}tc/{W}tbl")
    inner_hdr = inner_tbl.find(f"{W}tr/{W}trPr/{W}tblHeader") if inner_tbl is not None else None
    outer_hdr = outer_tbl.find(f"{W}tr/{W}trPr/{W}tblHeader")
    check("nested: tblHeader landed on the INNER table's first row", inner_hdr is not None)
    check("nested: the outer (already bold-header) table was NOT touched", outer_hdr is None)
    descrs = [dp.get("descr") for dp in root.iter("{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}docPr")]
    check("cell picture: alt landed on it", descrs == ["Agency seal"], str(descrs))
    res2 = parse_to_tree(str(out))
    run_analyzers(res2.tree)
    check("nested: re-parse — inner headers fixed, alt fixed, only the manual TABLE_NESTED remains",
          "TABLE_MISSING_HEADERS" not in _codes(res2.tree) and "MISSING_ALT_TEXT" not in _codes(res2.tree)
          and "TABLE_NESTED" in _codes(res2.tree), str(_codes(res2.tree)))

    # ===== 4. mc:AlternateContent text box: one finding, not two ==============
    d = Document()
    d.core_properties.title = "Newsletter"
    d.add_heading("Newsletter", 1)
    host = d.add_paragraph("Sidebar anchor paragraph with enough words to be prose here.")
    rid = d.part.relate_to("https://example.gov/sidebar",
                           "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                           is_external=True)
    tb_p = (f'<w:p><w:r><w:t xml:space="preserve">Sidebar: </w:t></w:r>'
            f'<w:hyperlink r:id="{rid}"><w:r><w:t>click here</w:t></w:r></w:hyperlink></w:p>')
    extra_ns = (
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"'
    )
    alt = parse_xml(
        f'<w:r {nsdecls("w", "r", "wp", "a")} {extra_ns}><mc:AlternateContent>'
        '<mc:Choice Requires="wps"><w:drawing><wp:anchor distT="0" distB="0" distL="0" distR="0" simplePos="0" '
        'relativeHeight="1" behindDoc="0" locked="0" layoutInCell="1" allowOverlap="1">'
        '<wp:simplePos x="0" y="0"/><wp:positionH relativeFrom="column"><wp:posOffset>0</wp:posOffset></wp:positionH>'
        '<wp:positionV relativeFrom="paragraph"><wp:posOffset>0</wp:posOffset></wp:positionV>'
        '<wp:extent cx="914400" cy="457200"/><wp:wrapNone/><wp:docPr id="50" name="Text Box 50"/>'
        '<a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
        f'<wps:wsp><wps:cNvSpPr txBox="1"/><wps:spPr/><wps:txbx><w:txbxContent>{tb_p}</w:txbxContent></wps:txbx>'
        '<wps:bodyPr/></wps:wsp></a:graphicData></a:graphic></wp:anchor></w:drawing></mc:Choice>'
        f'<mc:Fallback><w:pict><v:shape><v:textbox><w:txbxContent>{tb_p}</w:txbxContent></v:textbox></v:shape>'
        '</w:pict></mc:Fallback></mc:AlternateContent></w:r>'
    )
    host._p.append(alt)
    src = tmp / "textbox_alt.docx"
    d.save(str(src))
    res = parse_to_tree(str(src))
    run_analyzers(res.tree)
    sidebar = [n for n in _nodes(res.tree, LinkNode) if n.target == "https://example.gov/sidebar"]
    check("text box stored twice (Choice + Fallback): its link is ONE node",
          len(sidebar) == 1, str([n.id for n in sidebar]))
    check("... and ONE finding", _codes(res.tree).count("LINK_TEXT_NON_DESCRIPTIVE") == 1, str(_codes(res.tree)))
    check("host paragraph text excludes the text box copy (no duplicated words)",
          any(n.content.text == "Sidebar anchor paragraph with enough words to be prose here."
              for n in _nodes(res.tree, ParagraphNode)))

    # ===== 5. Schema order is kept ===========================================
    d = Document()
    d.add_heading("Quarterly figures", 1)
    t = d.add_table(rows=4, cols=3)
    for r in range(4):
        for c in range(3):
            t.cell(r, c).text = f"v{r}{c}" if r else ["Region", "Q1", "Q2"][c]
    t.rows[0]._tr.get_or_add_trPr().append(parse_xml(
        f'<w:ins {nsdecls("w")} w:id="90" w:author="R" w:date="2026-09-01T00:00:00Z"/>'))
    d.add_paragraph("- first typed item")
    d.add_paragraph("- second typed item")
    src = tmp / "schema.docx"
    d.save(str(src))

    def _pprdefault_only(root):
        dd = root.find(f"{W}docDefaults")
        for child in list(dd):
            if child.tag == f"{W}rPrDefault":
                dd.remove(child)

    _rewrite_part(src, "word/styles.xml", _pprdefault_only)

    def _mac_cleanup(root):
        mac = etree.SubElement(root, f"{W}numIdMacAtCleanup")
        mac.set(f"{W}val", "3")

    _rewrite_part(src, "word/numbering.xml", _mac_cleanup)
    res = parse_to_tree(str(src))
    run_analyzers(res.tree)
    res.tree.root.metadata.language = "en-US"            # SET_DOCUMENT_LANGUAGE's effect
    for c in _nodes(res.tree, TableNode)[0].children[0].children:
        c.cell_type = TableCellType.HEADER                # ADD_TABLE_HEADERS' effect
    plans = [p for p in plan_remediations(res.tree, POL) if p.flag.code.value == "LIST_STRUCTURE_INVALID"]
    execute_plans(res.tree, plans)
    out = tmp / "schema_fixed.docx"
    wr = write_remediated_docx(src, res.tree, out)
    with zipfile.ZipFile(out) as z:
        doc_root = etree.fromstring(z.read("word/document.xml"))
        styles_root = etree.fromstring(z.read("word/styles.xml"))
        num_root = etree.fromstring(z.read("word/numbering.xml"))
    trpr = doc_root.find(f".//{W}tbl/{W}tr/{W}trPr")
    order = [etree.QName(c).localname for c in trpr]
    check("schema: tblHeader goes BEFORE the row's tracked-change marker",
          order.index("tblHeader") < order.index("ins"), str(order))
    dd = [etree.QName(c).localname for c in styles_root.find(f"{W}docDefaults")]
    check("schema: rPrDefault precedes pPrDefault", dd == ["rPrDefault", "pPrDefault"], str(dd))
    tail = [etree.QName(c).localname for c in num_root][-1]
    check("schema: numIdMacAtCleanup stays last in numbering.xml", tail == "numIdMacAtCleanup",
          str([etree.QName(c).localname for c in num_root][-4:]))
    check("schema: the fixes were applied", any(a.get("kind") == "list_conversion" for a in wr["applied"])
          and any(a.get("kind") == "table_header_row" for a in wr["applied"]), str(wr["applied"]))

    # ===== 6. Nothing unapproved is rewritten ================================
    d = Document()
    custom = d.styles.add_style("Agency Heading", 1)  # paragraph style
    custom.base_style = d.styles["Heading 2"]
    d.add_heading("Report", 1)
    hp = d.add_paragraph("Agency section", style="Heading 2")
    t = d.add_table(rows=2, cols=2)          # small table: row 0 typed header by default
    t.cell(0, 0).text = "Name"
    t.cell(0, 1).text = "Role"
    t.cell(1, 0).text = "Ana"
    t.cell(1, 1).text = "Lead"
    src = tmp / "untouched.docx"
    d.save(str(src))
    res = parse_to_tree(str(src))
    res.tree.root.metadata.properties["title"] = "Staff Report"  # only the title is fixed
    out = tmp / "untouched_fixed.docx"
    wr = write_remediated_docx(src, res.tree, out)
    kinds = sorted({a.get("kind") for a in wr["applied"]})
    check("unapproved: only the title was written (no heading re-assert, no tblHeader)",
          kinds == ["document_title"], str(wr["applied"]))
    with zipfile.ZipFile(out) as z:
        x = z.read("word/document.xml")
    check("unapproved: no tblHeader appeared", b"tblHeader" not in x)

    # ===== 7. The output gate =================================================
    d = Document()
    d.add_paragraph("Body text long enough to be a real paragraph of prose.")
    src = tmp / "gate.docx"
    d.save(str(src))
    res = parse_to_tree(str(src))
    run_analyzers(res.tree)
    plans = [p for p in plan_remediations(res.tree, POL) if p.flag.code.value == "DOCUMENT_TITLE_MISSING"]
    execs = execute_plans(res.tree, plans)
    real = docx_writer._output_problem
    docx_writer._output_problem = lambda _p: "simulated: word/document.xml is not well-formed"
    try:
        out = tmp / "gate_fixed.docx"
        wr = write_remediated_docx(src, res.tree, out)
    finally:
        docx_writer._output_problem = real
    check("gate: a file that does not re-open is NOT delivered (source bytes restored)",
          out.read_bytes() == src.read_bytes())
    check("gate: nothing reported applied", wr["applied"] == [], str(wr["applied"]))
    check("gate: failed_to_save reason present",
          any(str(s.get("reason", "")).startswith("failed_to_save") for s in wr["skipped"]))
    check("gate: pipeline counts 0 (no charge)", _count_persisted_fixes(execs, wr["applied"], "docx", wr["skipped"]) == 0)
    check("gate: the real check passes a sound file", real(src) is None)
    bad = tmp / "broken.docx"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(bad, "w") as zout:
        for n in zin.namelist():
            data = zin.read(n)
            zout.writestr(n, data[:-20] if n == "word/document.xml" else data)
    check("gate: the real check rejects a truncated document.xml", real(bad) is not None)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
