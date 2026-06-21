"""Smoke: FIX_CONTRAST — auto-recolour low-contrast HTML text to pass WCAG AA.

Proves the new auto-fix end to end AND the honesty invariant: the analyzer's
nearest AA-passing colour is written into the output bytes as an inline style,
re-parsing the OUTPUT clears LOW_CONTRAST_TEXT, text that already passes is never
touched, and the action is credited as persisted for HTML.

  detect    -> grey-on-white text flagged; analyzer stores suggested_fg
  execute   -> FixContrastExecutor authorises the recolour (sets contrast_fix_fg)
  write     -> html_writer sets inline color:#<suggested> on the element
  honesty   -> re-parsing the OUTPUT clears the flag; passing text untouched

Usage:
    python -m app.devtools.smoke_fix_contrast
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_fixc_')}/s.db")

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.api.pipeline import _action_persists  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.services.remediation_planner import RemediationPolicy, plan_remediations  # noqa: E402
from app.services.remediators.registry import execute_plans  # noqa: E402
from app.writers.html_writer import write_remediated_html  # noqa: E402

CONTRAST = "LOW_CONTRAST_TEXT"

# h1 + first <p> fail AA on white; the dark <p> already passes (control).
CONTRAST_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Contrast test</title></head>
<body>
<h1 style="color:#9a9a9a;background:#ffffff">Low-contrast heading on white</h1>
<p style="color:#888888;background:#ffffff">Grey text on white (~3.5:1) — fails AA and must be auto-darkened.</p>
<p style="color:#111111;background:#ffffff">This dark paragraph already passes AA and must be left alone.</p>
</body>
</html>
"""


def _contrast_nodes(tree):
    """All nodes carrying a LOW_CONTRAST_TEXT flag."""
    out = []

    def walk(node):
        if any(f.code.value == CONTRAST for f in node.accessibility_flags):
            out.append(node)
        for ch in node.children:
            walk(ch)

    walk(tree.root)
    return out


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

    tmp = Path(tempfile.mkdtemp(prefix="fixc_smoke_"))
    policy = RemediationPolicy(allow_ai_actions=True, require_human_review_for_all=False)

    check("honesty matrix: FIX_CONTRAST persists for html", _action_persists("FIX_CONTRAST", "html"))
    check("honesty matrix: FIX_CONTRAST NOT credited for pdf (no writer support)",
          not _action_persists("FIX_CONTRAST", "pdf"))

    # ----------------------------------------------------------------- detection
    src = tmp / "contrast.html"
    src.write_text(CONTRAST_HTML, encoding="utf-8")
    res = parse_to_tree(str(src))
    run_analyzers(res.tree)

    flagged = _contrast_nodes(res.tree)
    check("two low-contrast nodes flagged (h1 + grey p)", len(flagged) == 2, f"got {len(flagged)}")
    check("each flagged node carries a suggested AA colour",
          all((n.metadata.properties or {}).get("contrast_finding", {}).get("suggested_fg") for n in flagged),
          str([(n.metadata.properties or {}).get("contrast_finding") for n in flagged]))

    # ----------------------------------------------------------------- remediate
    plans = plan_remediations(res.tree, policy)
    execs = execute_plans(res.tree, plans)
    fix_successes = [e for e in execs if e.action_code.value == "FIX_CONTRAST" and e.status.value == "success"]
    check("FIX_CONTRAST executed for both failing nodes", len(fix_successes) == 2, f"got {len(fix_successes)}")
    check("executor set contrast_fix_fg on flagged nodes",
          all((n.metadata.properties or {}).get("contrast_fix_fg") for n in flagged))

    out = tmp / "contrast.fixed.html"
    result = write_remediated_html(src, res.tree, out)
    applied_contrast = [a for a in result["applied"] if a.get("action") == "FIX_CONTRAST"]
    check("writer applied FIX_CONTRAST twice", len(applied_contrast) == 2, str(result["applied"]))
    out_text = out.read_text(encoding="utf-8")

    # The original failing colours are replaced; the passing one is untouched.
    check("grey #888888 no longer the text colour", "color:#888888" not in out_text.replace(" ", "").lower()
          and "color: #888888" not in out_text.lower())
    check("heading grey #9a9a9a replaced", "color:#9a9a9a" not in out_text.replace(" ", "").lower())
    check("passing dark paragraph (#111111) left intact", "#111111" in out_text.lower())

    # ----------------------------------------------------------------- re-analysis
    res2 = parse_to_tree(str(out))
    run_analyzers(res2.tree)
    check("re-parse: ALL low-contrast flags cleared", _flag_count(res2.tree, CONTRAST) == 0,
          f"got {_flag_count(res2.tree, CONTRAST)}")

    # ----------------------------------------------------------------- idempotency
    plans2 = plan_remediations(res2.tree, policy)
    execs2 = execute_plans(res2.tree, plans2)
    check("idempotent: nothing left to fix on the cleaned doc",
          not any(e.action_code.value == "FIX_CONTRAST" and e.status.value == "success" for e in execs2))

    # -------------------------------------------------- honesty: unresolved node
    # An approved recolour whose element can't be located must NOT be written and
    # must NOT be credited (so it can never be charged). We simulate the executor
    # approving a node, then break its locator.
    res3 = parse_to_tree(str(src))
    run_analyzers(res3.tree)
    target = _contrast_nodes(res3.tree)[0]
    p3 = target.metadata.properties or {}
    p3["contrast_fix_fg"] = "595959"               # executor approval marker
    p3["__xpath"] = "/html/body/nonexistent[99]"   # break the locator
    target.metadata.properties = p3
    out3 = tmp / "contrast.broken.html"
    res_broken = write_remediated_html(src, res3.tree, out3)
    applied_ids = {a.get("target_id") for a in res_broken["applied"] if a.get("action") == "FIX_CONTRAST"}
    skipped_ids = {s.get("target_id") for s in res_broken["skipped"]
                   if s.get("reason") == "contrast_element_not_resolved"}
    check("unresolved recolour NOT in applied (cannot be credited/charged)", target.id not in applied_ids)
    check("unresolved recolour recorded in skipped", target.id in skipped_ids)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
