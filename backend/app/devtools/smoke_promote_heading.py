"""Smoke: fake (styled-only) headings are PROMOTED to real headings.

The most common real-world failure is a title-page / section header typed as
big bold text (or Word's Title/Subtitle style) instead of a real Heading —
invisible to screen-reader heading navigation. Previously we only *flagged*
this (TEXT_STYLED_AS_HEADING, detect-only). This pins the new auto-fix end to
end:

  parser   -> ``looks_like_heading`` on the offending paragraph
  analyzer -> TEXT_STYLED_AS_HEADING fires (and ONLY on the fake ones)
  executor -> PROMOTE_HEADING succeeds, choosing a hierarchy-safe level
              (sibling of the nearest preceding heading, else H1)
  writer   -> output bytes: the paragraph's style becomes ``Heading N``
  honesty  -> PROMOTE_HEADING persists for docx; re-parsing the OUTPUT yields a
              real HeadingNode, the fake-heading flag is gone, and no NEW
              heading-level jump was introduced (idempotent + safe).

Usage:
    python -m app.devtools.smoke_promote_heading
"""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_ph_')}/s.db")

from docx import Document  # noqa: E402
from docx.shared import Pt  # noqa: E402

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.api.pipeline import _action_persists  # noqa: E402
from app.models.accessibility import HeadingNode  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.services.remediation_planner import RemediationPolicy, plan_remediations  # noqa: E402
from app.services.remediators.registry import execute_plans  # noqa: E402
from app.writers.docx_writer import write_remediated_docx  # noqa: E402

FAKE_H = "TEXT_STYLED_AS_HEADING"
JUMP = "HEADING_LEVEL_JUMP"


def _flag_count(tree, code: str) -> int:
    n = 0

    def walk(node):
        nonlocal n
        n += sum(1 for f in node.accessibility_flags if f.code.value == code)
        for ch in node.children:
            walk(ch)

    walk(tree.root)
    return n


def _heading_texts(tree) -> list[str]:
    out: list[str] = []

    def walk(node):
        if isinstance(node, HeadingNode):
            out.append((node.content.text if node.content else "") or "")
        for ch in node.children:
            walk(ch)

    walk(tree.root)
    return out


def _heading_level_by_text(tree) -> dict[str, int]:
    out: dict[str, int] = {}

    def walk(node):
        if isinstance(node, HeadingNode):
            out[((node.content.text if node.content else "") or "").strip()] = node.level
        for ch in node.children:
            walk(ch)

    walk(tree.root)
    return out


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="promote_heading_"))
    apply_policy = RemediationPolicy(allow_ai_actions=True, require_human_review_for_all=False)

    # =========================================================================
    # Fixture A — a real H1 precedes the fakes (promotion = sibling H1).
    # =========================================================================
    d = Document()
    d.core_properties.title = "Promote Heading Test"
    d.add_heading("Overview", level=1)                         # real heading
    p_big = d.add_paragraph()                                  # fake (big+bold)
    rb = p_big.add_run("Annual Report 2026")
    rb.bold = True
    rb.font.size = Pt(24)
    d.add_paragraph("This is an ordinary sentence of body text in the document.")
    tp = d.add_paragraph("Quarterly Review")                   # fake (Title style)
    tp.style = d.styles["Title"]
    p_emph = d.add_paragraph()                                 # NOT fake (small bold)
    p_emph.add_run("Important note").bold = True
    src = tmp / "fakeheadings.docx"
    d.save(str(src))

    res = parse_to_tree(str(src))
    run_analyzers(res.tree)
    check("two fake headings flagged (big-bold + Title); body/emphasis quiet",
          _flag_count(res.tree, FAKE_H) == 2, f"got {_flag_count(res.tree, FAKE_H)}")

    plans = [p for p in plan_remediations(res.tree, apply_policy) if p.flag.code.value == FAKE_H]
    check("two PROMOTE_HEADING plans", len(plans) == 2, f"got {len(plans)}")
    execs = execute_plans(res.tree, plans)
    ok = [e for e in execs if e.status.value == "success"]
    check("both promotions execute successfully", len(ok) == 2,
          str([(e.status.value, e.notes) for e in execs]))
    check("PROMOTE_HEADING persists for docx (honesty matrix)", _action_persists("PROMOTE_HEADING", "docx"))

    # writer -> bytes
    out = tmp / "fixed.docx"
    result = write_remediated_docx(src, res.tree, out)
    promotions = [a for a in result["applied"] if a.get("kind") == "promote_heading"]
    check("writer applied 2 heading promotions", len(promotions) == 2, str(result))

    with zipfile.ZipFile(out) as z:
        doc_xml = z.read("word/document.xml").decode("utf-8", "replace")
    check("promoted paragraphs carry a Heading pStyle in the bytes",
          'w:val="Heading1"' in doc_xml or 'w:val="Heading 1"' in doc_xml.replace("&#160;", " "),
          "no Heading pStyle found")
    check("promoted text preserved", "Annual Report 2026" in doc_xml and "Quarterly Review" in doc_xml)

    # re-parse OUTPUT: fakes are now real headings, flag gone, no new jump
    res2 = parse_to_tree(str(out))
    run_analyzers(res2.tree)
    check("re-analysis of output raises NO fake-heading flag (idempotent)",
          _flag_count(res2.tree, FAKE_H) == 0, f"got {_flag_count(res2.tree, FAKE_H)}")
    htexts = _heading_texts(res2.tree)
    check("promoted lines are now real HeadingNodes",
          "Annual Report 2026" in htexts and "Quarterly Review" in htexts, str(htexts))
    check("no NEW heading-level jump introduced by promotion",
          _flag_count(res2.tree, JUMP) == 0, f"got {_flag_count(res2.tree, JUMP)}")

    # =========================================================================
    # Fixture B — fake heading at the very top (no preceding heading -> H1).
    # =========================================================================
    d2 = Document()
    d2.core_properties.title = "Top Title Test"
    tp2 = d2.add_paragraph("Strategic Plan")                   # fake Title, first thing
    tp2.style = d2.styles["Title"]
    d2.add_paragraph("Body paragraph that follows the title.")
    src2 = tmp / "toptitle.docx"
    d2.save(str(src2))

    r = parse_to_tree(str(src2))
    run_analyzers(r.tree)
    check("top Title-style line flagged as fake heading", _flag_count(r.tree, FAKE_H) == 1)
    pl = [p for p in plan_remediations(r.tree, apply_policy) if p.flag.code.value == FAKE_H]
    ex = execute_plans(r.tree, pl)
    check("top fake heading promotes successfully",
          len([e for e in ex if e.status.value == "success"]) == 1,
          str([(e.status.value, e.notes) for e in ex]))
    out2 = tmp / "toptitle_fixed.docx"
    write_remediated_docx(src2, r.tree, out2)
    r2 = parse_to_tree(str(out2))
    run_analyzers(r2.tree)
    check("top fake heading is now a real H1, flag gone",
          _flag_count(r2.tree, FAKE_H) == 0 and "Strategic Plan" in _heading_texts(r2.tree))

    # =========================================================================
    # Fixture C — clean doc: a real heading + body, nothing fake.
    # =========================================================================
    c = Document()
    c.core_properties.title = "Clean"
    c.add_heading("Real Section", level=1)
    c.add_paragraph("An ordinary body paragraph with no heading pretensions.")
    csrc = tmp / "clean.docx"
    c.save(str(csrc))
    rc = parse_to_tree(str(csrc))
    run_analyzers(rc.tree)
    check("clean doc: no fake-heading flag", _flag_count(rc.tree, FAKE_H) == 0)
    cplans = [p for p in plan_remediations(rc.tree, apply_policy) if p.flag.code.value == FAKE_H]
    check("clean doc: no PROMOTE_HEADING plan", len(cplans) == 0)
    # The real heading must survive a write untouched (no spurious promotion).
    cout = tmp / "clean_fixed.docx"
    cres = write_remediated_docx(csrc, rc.tree, cout)
    check("clean doc: writer made no heading promotions",
          not any(a.get("kind") == "promote_heading" for a in cres["applied"]))

    # =========================================================================
    # Fixture D — bold, large, NUMBERED section headers ("1. Introduction").
    # These look like BOTH a fake heading AND a fake list. Regression guard:
    # the parser must treat them as headings only (never tag fake-list), so we
    # promote them (numbers preserved) instead of silently dropping the
    # promotion and bulleting them — the honesty bug the adversarial pass found.
    # =========================================================================
    LIST = "LIST_STRUCTURE_INVALID"
    d4 = Document()
    d4.core_properties.title = "Numbered Headers"
    for txt in ("1. Introduction", "2. Background", "3. Methodology"):
        p = d4.add_paragraph()
        rr = p.add_run(txt)
        rr.bold = True
        rr.font.size = Pt(18)
    d4.add_paragraph("Body text under the numbered section headers.")
    src4 = tmp / "numbered_headers.docx"
    d4.save(str(src4))

    r4 = parse_to_tree(str(src4))
    run_analyzers(r4.tree)
    check("numbered bold headers flagged as fake HEADINGS, not fake lists",
          _flag_count(r4.tree, FAKE_H) == 3 and _flag_count(r4.tree, LIST) == 0,
          f"fakeH={_flag_count(r4.tree, FAKE_H)} list={_flag_count(r4.tree, LIST)}")
    plans4 = plan_remediations(r4.tree, apply_policy)
    promote4 = [p for p in plans4 if p.flag.code.value == FAKE_H]
    listplans4 = [p for p in plans4 if p.flag.code.value == LIST]
    check("3 promote plans, 0 list-conversion plans", len(promote4) == 3 and len(listplans4) == 0,
          f"promote={len(promote4)} list={len(listplans4)}")
    execute_plans(r4.tree, promote4)
    out4 = tmp / "numbered_fixed.docx"
    res4 = write_remediated_docx(src4, r4.tree, out4)
    check("writer promoted 3 headings and converted 0 lists",
          len([a for a in res4["applied"] if a.get("kind") == "promote_heading"]) == 3
          and len([a for a in res4["applied"] if a.get("kind") == "list_conversion"]) == 0,
          str(res4["applied"]))
    with zipfile.ZipFile(out4) as z:
        doc_xml4 = z.read("word/document.xml").decode("utf-8", "replace")
    check("numbers PRESERVED (not stripped as if bulleted)",
          "1. Introduction" in doc_xml4 and "2. Background" in doc_xml4, "numbers were mangled")
    r4b = parse_to_tree(str(out4))
    run_analyzers(r4b.tree)
    check("re-parse: 3 real headings, no fake-heading or fake-list flags",
          len(_heading_texts(r4b.tree)) >= 3
          and _flag_count(r4b.tree, FAKE_H) == 0
          and _flag_count(r4b.tree, LIST) == 0,
          f"headings={_heading_texts(r4b.tree)}")

    # =========================================================================
    # Fixture E — fake cover title with NO preceding heading, but the first real
    # heading is mis-styled H3 (author skipped H1/H2). Promoting the title to H1
    # would CREATE a new H1->H3 jump. Regression guard: we promote to H2
    # (max(1, nextLevel-1)) so NO new jump is introduced.
    # =========================================================================
    d5 = Document()
    d5.core_properties.title = "Jump Guard"
    tp5 = d5.add_paragraph()
    r5run = tp5.add_run("Company Strategy Deck")  # fake cover title
    r5run.bold = True
    r5run.font.size = Pt(28)
    d5.add_paragraph("Intro paragraph between the title and the first section.")
    d5.add_heading("Market Analysis", level=3)    # real heading, mis-styled H3
    d5.add_paragraph("Section body text.")
    src5 = tmp / "jumpguard.docx"
    d5.save(str(src5))

    r5 = parse_to_tree(str(src5))
    run_analyzers(r5.tree)
    check("jumpguard: fake title flagged; NO pre-existing heading jump",
          _flag_count(r5.tree, FAKE_H) == 1 and _flag_count(r5.tree, JUMP) == 0,
          f"fakeH={_flag_count(r5.tree, FAKE_H)} jump={_flag_count(r5.tree, JUMP)}")
    pl5 = [p for p in plan_remediations(r5.tree, apply_policy) if p.flag.code.value == FAKE_H]
    execute_plans(r5.tree, pl5)
    out5 = tmp / "jumpguard_fixed.docx"
    write_remediated_docx(src5, r5.tree, out5)
    r5b = parse_to_tree(str(out5))
    run_analyzers(r5b.tree)
    levels5 = _heading_level_by_text(r5b.tree)
    check("jumpguard: title promoted to H2 (one shallower than the H3), not H1",
          levels5.get("Company Strategy Deck") == 2, f"levels={levels5}")
    check("jumpguard: NO new heading-level jump introduced",
          _flag_count(r5b.tree, JUMP) == 0, f"jump={_flag_count(r5b.tree, JUMP)}")

    # Sanity: the ORIGINAL fake-list path still works (no heading styling) so the
    # parser change didn't suppress genuine fake lists.
    d6 = Document()
    d6.core_properties.title = "Real Fake List"
    d6.add_paragraph("- alpha")
    d6.add_paragraph("- beta")
    d6.add_paragraph("- gamma")
    src6 = tmp / "reallist.docx"
    d6.save(str(src6))
    r6 = parse_to_tree(str(src6))
    run_analyzers(r6.tree)
    check("plain '- ' bullets still detected as a fake list (1 flag), not headings",
          _flag_count(r6.tree, LIST) == 1 and _flag_count(r6.tree, FAKE_H) == 0,
          f"list={_flag_count(r6.tree, LIST)} fakeH={_flag_count(r6.tree, FAKE_H)}")

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
