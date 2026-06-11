"""Smoke: remediated OUTPUT files are structurally valid (won't corrupt).

The audit found a PPTX list-conversion bug that appended a bullet AFTER an
existing a:defRPr, violating the OOXML child-order schema so PowerPoint
refused to open the "fixed" deck. python-pptx reopening it did NOT catch
that (the library is lenient). This locks the *class* of bug:

  - an adversarial PPTX whose list paragraphs ALREADY carry a:defRPr
    (the exact trigger) is remediated, and EVERY a:pPr is verified to put
    the bullet (a:buChar/a:buAutoNum) BEFORE a:defRPr / a:tabLst / a:extLst;
  - the output reopens cleanly in python-pptx;
  - the DOCX list path produces a valid numbering.xml + reopens.

Usage:
    python -m app.devtools.smoke_output_validity
"""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_ov_')}/s.db")

from docx import Document  # noqa: E402
from pptx import Presentation  # noqa: E402
from pptx.oxml.ns import qn as pqn  # noqa: E402
from pptx.util import Inches  # noqa: E402

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.services.remediation_planner import RemediationPolicy, plan_remediations  # noqa: E402
from app.services.remediators.registry import execute_plans  # noqa: E402
from app.writers import write_remediated  # noqa: E402

APPLY = RemediationPolicy(allow_ai_actions=True, require_human_review_for_all=False)
_TRAILING = ("a:tabLst", "a:defRPr", "a:extLst")
_BULLETS = ("a:buChar", "a:buAutoNum")


def _remediate_all(src: Path, out: Path) -> None:
    res = parse_to_tree(str(src))
    run_analyzers(res.tree)
    plans = plan_remediations(res.tree, APPLY)
    execute_plans(res.tree, plans)
    write_remediated(src, res.tree, out, source_format=res.format)


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="ov_"))

    # --- Adversarial PPTX: typed list lines whose paragraphs ALREADY have an
    #     a:defRPr (the exact condition that broke child order) ----------------
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    tb = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(3))
    tf = tb.text_frame
    lines = ["- first typed item", "- second typed item", "- third typed item"]
    tf.text = lines[0]
    for ln in lines[1:]:
        tf.add_paragraph().text = ln
    # Force an a:defRPr into every paragraph's a:pPr so the bullet MUST be
    # inserted before it (the bug appended after -> invalid order).
    for p in tf.paragraphs:
        pPr = p._p.get_or_add_pPr()
        if pPr.find(pqn("a:defRPr")) is None:
            pPr.append(pPr.makeelement(pqn("a:defRPr"), {}))
    src = tmp / "adversarial.pptx"
    prs.save(str(src))

    out = tmp / "adversarial_fixed.pptx"
    _remediate_all(src, out)

    # Reopen (must not raise) and assert child order in EVERY a:pPr.
    fixed = Presentation(str(out))
    bullets_seen = 0
    order_ok = True
    for sl in fixed.slides:
        for sh in sl.shapes:
            if not sh.has_text_frame:
                continue
            for p in sh.text_frame.paragraphs:
                pPr = p._p.find(pqn("a:pPr"))
                if pPr is None:
                    continue
                children = list(pPr)
                tags = [c.tag for c in children]
                bullet_idx = next((i for i, c in enumerate(children) if c.tag in (pqn(b) for b in _BULLETS)), None)
                if bullet_idx is None:
                    continue
                bullets_seen += 1
                # Every trailing element must come AFTER the bullet.
                for t in _TRAILING:
                    if pqn(t) in tags and tags.index(pqn(t)) < bullet_idx:
                        order_ok = False
    check("adversarial PPTX reopened in python-pptx", True)  # no exception above
    check("bullets were actually inserted", bullets_seen >= 3, f"seen={bullets_seen}")
    check("every bullet precedes defRPr/tabLst/extLst (schema-valid order)", order_ok)

    # Raw XML sanity: the slide part has no buChar AFTER defRPr in any pPr.
    with zipfile.ZipFile(out) as z:
        slide_xml = b""
        for nm in z.namelist():
            if nm.startswith("ppt/slides/slide") and nm.endswith(".xml"):
                slide_xml += z.read(nm)
    check("output contains real bullets", b"buChar" in slide_xml or b"buAutoNum" in slide_xml)

    # --- DOCX list path: valid numbering + reopens ---------------------------
    d = Document()
    d.core_properties.title = "DOCX Lists"
    d.add_paragraph("Intro paragraph.")
    d.add_paragraph("- alpha")
    d.add_paragraph("- bravo")
    d.add_paragraph("- charlie")
    dsrc = tmp / "lists.docx"
    d.save(str(dsrc))
    dout = tmp / "lists_fixed.docx"
    _remediate_all(dsrc, dout)
    Document(str(dout))  # reopen must not raise
    with zipfile.ZipFile(dout) as z:
        names = z.namelist()
        numbering = z.read("word/numbering.xml").decode("utf-8", "replace") if "word/numbering.xml" in names else ""
    check("DOCX output reopened in python-docx", True)
    check("DOCX numbering.xml present + has a definition", "word/numbering.xml" in names and "abstractNum" in numbering)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
