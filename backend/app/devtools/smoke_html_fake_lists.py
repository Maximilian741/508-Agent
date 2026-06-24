"""Smoke: typed HTML "fake lists" become real <ul>/<ol> (auto-fix).

Runs of plain <p> typed as "- item" or "1. item" read as a list visually but
are not a real list to assistive tech (no list role, no item count). DOCX and
PPTX already convert these; this brings HTML to parity:

  parser   -> marks each typed <p> with its signature and groups >=2 same-kind
              runs (shared _group_fake_list_runs); first node gets
              fake_list_run_ids
  analyzer -> LIST_STRUCTURE_INVALID fires on the run's first paragraph
  executor -> FIX_LIST_STRUCTURE sets convert_to_list on each member and strips
              the literal marker from the tree text
  writer   -> the run of <p> becomes a real <ul>/<ol> with <li> per item
  honesty  -> FIX_LIST_STRUCTURE persists for html; re-parsing the OUTPUT sees
              real ListNodes and the flag clears; non-lists are untouched

Usage:
    python -m app.devtools.smoke_html_fake_lists
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_hfl_')}/s.db")

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.api.pipeline import _action_persists  # noqa: E402
from app.models.accessibility import ListItemNode, ListNode  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.services.remediation_planner import RemediationPolicy, plan_remediations  # noqa: E402
from app.services.remediators.registry import execute_plans  # noqa: E402
from app.writers.html_writer import write_remediated_html  # noqa: E402

FLAG = "LIST_STRUCTURE_INVALID"

DIRTY = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Lists</title></head>
<body>
  <p>Shopping list:</p>
  <p>- Milk</p>
  <p>- Eggs</p>
  <p>- Bread</p>
  <p>Steps:</p>
  <p>1. First</p>
  <p>2. Second</p>
  <p>3. Third</p>
  <p>Only one bullet (not a list):</p>
  <p>- lonely</p>
  <p>A normal paragraph that must not be touched.</p>
  <ul><li>An existing real list item</li></ul>
</body></html>
"""


def _flag_count(tree, code):
    n = 0

    def walk(node):
        nonlocal n
        n += sum(1 for f in node.accessibility_flags if f.code.value == code)
        for ch in node.children:
            walk(ch)

    walk(tree.root)
    return n


def _list_items(tree):
    """All ListItemNode texts under real ListNodes."""
    out = []

    def walk(node):
        if isinstance(node, ListNode):
            for li in node.children:
                if isinstance(li, ListItemNode):
                    out.append((li.content.text or "").strip() if li.content else "")
        for ch in node.children:
            walk(ch)

    walk(tree.root)
    return out


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="html_fl_"))
    policy = RemediationPolicy(allow_ai_actions=True, require_human_review_for_all=False)

    check("honesty matrix: FIX_LIST_STRUCTURE persists for html",
          _action_persists("FIX_LIST_STRUCTURE", "html"))

    src = tmp / "lists.html"
    src.write_text(DIRTY, encoding="utf-8")
    res = parse_to_tree(str(src))
    run_analyzers(res.tree)

    check("two typed lists flagged (bullet run + decimal run)",
          _flag_count(res.tree, FLAG) == 2, f"got {_flag_count(res.tree, FLAG)}")

    plans = plan_remediations(res.tree, policy)
    execs = execute_plans(res.tree, plans)
    ok = [e for e in execs if e.status.value == "success" and e.action_code.value == "FIX_LIST_STRUCTURE"]
    check("FIX_LIST_STRUCTURE executed for both runs", len(ok) == 2,
          str([(e.action_code.value, e.status.value) for e in execs]))

    out = tmp / "lists.fixed.html"
    result = write_remediated_html(src, res.tree, out)
    applied = [a for a in result["applied"] if a.get("action") == "FIX_LIST_STRUCTURE"]
    check("writer converted exactly 2 runs", len(applied) == 2, str(result["applied"]))

    out_text = out.read_text(encoding="utf-8")
    check("output has a <ul> and an <ol>", "<ul>" in out_text and "<ol>" in out_text, out_text)
    check("literal '- Milk' marker gone from output", "- Milk" not in out_text)
    check("literal '1. First' marker gone from output", "1. First" not in out_text)
    check("single '- lonely' bullet NOT converted (still literal)", "- lonely" in out_text)

    # Re-parse the OUTPUT: real lists now, flag cleared, items correct.
    res2 = parse_to_tree(str(out))
    run_analyzers(res2.tree)
    check("re-parse: LIST_STRUCTURE_INVALID cleared", _flag_count(res2.tree, FLAG) == 0,
          f"got {_flag_count(res2.tree, FLAG)}")
    items = _list_items(res2.tree)
    for want in ("Milk", "Eggs", "Bread", "First", "Second", "Third", "An existing real list item"):
        check(f"output list item present + marker-stripped: {want!r}", want in items, str(items))

    # Idempotent: nothing left to convert on the cleaned doc.
    execs2 = execute_plans(res2.tree, [p for p in plan_remediations(res2.tree, policy)])
    check("idempotent: no further FIX_LIST_STRUCTURE",
          not any(e.action_code.value == "FIX_LIST_STRUCTURE" and e.status.value == "success" for e in execs2))

    # Clean doc: a real list only -> no flag, no action.
    csrc = tmp / "clean.html"
    csrc.write_text("<!DOCTYPE html><html lang=en><head><title>c</title></head>"
                    "<body><ul><li>a</li><li>b</li></ul><p>Plain text.</p></body></html>", encoding="utf-8")
    rc = parse_to_tree(str(csrc))
    run_analyzers(rc.tree)
    check("clean doc: no LIST_STRUCTURE_INVALID flag", _flag_count(rc.tree, FLAG) == 0)

    # =========================================================================
    # Adversarial-review regressions (content-loss / false-conversion bugs).
    # =========================================================================
    def _run(body: str):
        """parse -> analyze -> remediate -> write; return (flags, out_text, applied)."""
        sp = tmp / f"r{abs(hash(body)) % 100000}.html"
        sp.write_text(f"<!DOCTYPE html><html lang=en><head><meta charset=utf-8><title>r</title></head><body>{body}</body></html>",
                      encoding="utf-8")
        r = parse_to_tree(str(sp))
        run_analyzers(r.tree)
        flags = _flag_count(r.tree, FLAG)
        execute_plans(r.tree, plan_remediations(r.tree, policy))
        op = tmp / (sp.stem + ".out.html")
        res = write_remediated_html(sp, r.tree, op)
        return flags, op.read_text(encoding="utf-8"), res["applied"]

    # Inline markup must NOT be swept in (would flatten <strong>/<em>).
    f, out, _ = _run("<p>- Buy <strong>organic</strong> milk</p><p>- plain two</p>")
    check("inline <strong> bullet NOT flagged (markup safe)", f == 0, f"flags={f}")
    check("inline <strong> preserved in output", "<strong>" in out)

    # <br> must NOT be swept in (would fuse 'line aline b').
    f, out, _ = _run("<p>- line a<br>line b</p><p>- second</p>")
    check("<br> bullet NOT flagged", f == 0, f"flags={f}")

    # Code context: a leading '-' is a diff marker, not a bullet.
    f, _o, _ = _run("<pre><p>- old line</p><p>- another removed line</p></pre>")
    check("bullets inside <pre> NOT flagged", f == 0, f"flags={f}")

    # Numbered PROSE (long sentences) must NOT become an <ol>.
    f, _o, _ = _run("<p>1. This is actually a full sentence describing the first thing in great detail here today</p>"
                    "<p>2. And the second one is also long prose that is clearly not a short list item at all</p>")
    check("numbered long-prose NOT flagged (>12 words)", f == 0, f"flags={f}")

    # Tail text between members must survive the conversion.
    f, out, applied = _run("<p>- Milk</p>KEEP-THIS-TAIL<p>- Eggs</p><p>- Bread</p>")
    converted = [a for a in applied if a.get("action") == "FIX_LIST_STRUCTURE"]
    check("tail case: list converted", len(converted) == 1, str(applied))
    check("tail text 'KEEP-THIS-TAIL' preserved in output", "KEEP-THIS-TAIL" in out, out)

    # Members under different DOM parents -> writer skips (no scramble/loss).
    f, out, applied = _run("<p>- Milk</p><span><p>- Eggs</p></span><p>- Bread</p>")
    check("cross-parent run NOT converted (skipped)",
          not any(a.get("action") == "FIX_LIST_STRUCTURE" for a in applied), str(applied))
    check("cross-parent: all content preserved", "Milk" in out and "Eggs" in out and "Bread" in out)

    # Low-contrast + fake-list: contrast persists, list conversion skips (no
    # charged-but-absent recolour).
    f, out, applied = _run('<p style="color:#bbbbbb;background:#ffffff">- Milk</p>'
                           '<p style="color:#bbbbbb;background:#ffffff">- Eggs</p>'
                           '<p style="color:#bbbbbb;background:#ffffff">- Bread</p>')
    contrasts = [a for a in applied if a.get("action") == "FIX_CONTRAST"]
    lists = [a for a in applied if a.get("action") == "FIX_LIST_STRUCTURE"]
    check("contrast+list: FIX_CONTRAST applied, FIX_LIST_STRUCTURE skipped",
          len(contrasts) >= 1 and len(lists) == 0, f"contrast={len(contrasts)} list={len(lists)}")
    check("contrast+list: '#bbbbbb' replaced in output (recolour persisted)",
          "#bbbbbb" not in out.lower(), out)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
