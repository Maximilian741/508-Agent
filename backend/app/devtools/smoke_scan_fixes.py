"""Smoke: "Fix it yourself" — per-finding fix guidance for the free URL scan.

A live page can't be remediated by us, so the scan's value is telling a
developer EXACTLY what to change. Two sources, and the distinction is the whole
point of the honesty story:

  writer   -> a REAL before/after diff, emitted only when the writer's own
              ``applied`` list confirms it made that change
  guidance -> a hand-written pattern for findings we deliberately never
              auto-fix; no ``before``, flagged requiresHumanVerification

This pins both, plus the hard invariants: NO AI action may run during a scan
(free page, real money), and nothing is charged/persisted.

Usage:
    python -m app.devtools.smoke_scan_fixes
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_fixes_')}/s.db")
# If any AI action DID run, this provider would be used — the spy below proves
# none is even constructed for a scan.
os.environ["SEMANTIC_PROVIDER"] = "heuristic"

from pathlib import Path  # noqa: E402

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.services.fix_guidance import guidance_for  # noqa: E402
from app.services.scan_fixes import _SCAN_ACTION_CODES, derive_scan_fixes  # noqa: E402

# A page with a mix of fixable and human-judgment findings.
PAGE = """<!DOCTYPE html>
<html><head><title>Untitled</title></head>
<body>
  <h1>Report</h1>
  <h3>Jumped heading</h3>
  <p>Some content for the page so it isn't empty.</p>
  <a href="/docs">click here</a>
  <a href="/menu"><span class="icon-menu"></span></a>
  <img src="photo.png">
  <p style="color:#bbbbbb;background:#ffffff">Low contrast text</p>
</body></html>
"""


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    # ---- the AI gate is structural: no requires_ai action is even allowed ----
    from app.models.accessibility import REMEDIATION_ACTIONS_BY_FLAG

    ai_codes = {
        a.action_code
        for actions in REMEDIATION_ACTIONS_BY_FLAG.values()
        for a in actions
        if a.requires_ai
    }
    check("AI actions exist to be excluded (sanity)", len(ai_codes) > 0)
    overlap = ai_codes & set(_SCAN_ACTION_CODES)
    check("scan policy contains NO requires_ai action (no AI spend on a free scan)",
          not overlap, str(overlap))

    tmp = Path(tempfile.mkdtemp(prefix="scanfix_"))
    src = tmp / "page.html"
    src.write_text(PAGE, encoding="utf-8")

    res = parse_to_tree(str(src))
    run_analyzers(res.tree)
    violations_before = sum(len(n.accessibility_flags) for n in _walk(res.tree.root))
    check("page produced findings to fix", violations_before > 0, str(violations_before))

    # The real gate: a scan must never reach a PAID provider. Some executors
    # (e.g. IMPROVE_LINK_TEXT, declared requires_ai=False) still call the
    # inference client, so the policy whitelist alone is not enough — the scan
    # pins every executor to an offline heuristic provider. Simulate a
    # production deployment where the default provider is a paid one and assert
    # it is never constructed or called.
    import app.ai.semantic_inference as _si

    paid_calls = {"n": 0}

    class _PaidProviderSpy(_si.HeuristicProvider):
        name = "paid-spy"

        def _spend(self, *a, **k):
            paid_calls["n"] += 1

        def alt_text(self, payload):
            self._spend()
            return super().alt_text(payload)

        def link_text(self, payload):
            self._spend()
            return super().link_text(payload)

        def document_title(self, payload):
            self._spend()
            return super().document_title(payload)

        def document_language(self, payload):
            self._spend()
            return super().document_language(payload)

    real_build = _si.build_default_provider
    _si.build_default_provider = lambda *a, **k: _PaidProviderSpy()
    try:
        fixes = derive_scan_fixes(src, res.tree)
    finally:
        _si.build_default_provider = real_build

    check("free scan never reaches the PAID provider (offline-pinned executors)",
          paid_calls["n"] == 0, f"paid provider calls={paid_calls['n']}")
    check("writer produced at least one real fix", len(fixes) > 0, str(fixes))

    # Every writer fix must be honest: a real before, and for in-place edits a
    # genuinely DIFFERENT after.
    for node_id, fx in fixes.items():
        check(f"fix[{node_id}] source=writer", fx.get("source") == "writer", str(fx))
        check(f"fix[{node_id}] names the action", bool(fx.get("action")), str(fx))
        if fx.get("kind") == "element":
            check(f"fix[{node_id}] has before AND after", bool(fx.get("before")) and bool(fx.get("after")))
            check(f"fix[{node_id}] after differs from before", fx.get("before") != fx.get("after"))
        elif fx.get("kind") == "structural":
            check(f"fix[{node_id}] structural has NO fabricated after", fx.get("after") is None, str(fx))

    # ---- guidance for the things we deliberately never auto-fix ----
    g_link = guidance_for("LINK_NAME_MISSING", {})
    check("guidance: nameless link covered", bool(g_link))
    check("guidance: source=guidance", g_link and g_link["source"] == "guidance")
    check("guidance: never invents a 'before'", g_link and g_link.get("before") is None, str(g_link))
    check("guidance: flagged for human verification", g_link and g_link["requiresHumanVerification"] is True)

    g_alt = guidance_for("MISSING_ALT_TEXT", {})
    check("guidance: missing alt covered (no vision AI on a live page)", bool(g_alt))

    # Contrast gets a REAL css value straight from the analyzer's evidence.
    g_con = guidance_for("LOW_CONTRAST_TEXT", {"fg": "bbbbbb", "bg": "ffffff", "ratio": 1.9, "suggested_fg": "767676"})
    check("guidance: contrast returns a concrete CSS fix", g_con and g_con["kind"] == "css", str(g_con))
    check("guidance: contrast cites the passing colour", g_con and "767676" in (g_con.get("after") or ""), str(g_con))

    check("guidance: unknown rule -> None (never fabricate)", guidance_for("NOT_A_REAL_RULE", {}) is None)

    # ---- PDF: never FABRICATE a table header row (regression lock) ----
    # PDF has no header semantics, so typing row 0 as /TH is an inference. It is
    # a reasonable default for a detected data grid, but asserting it over a
    # numeric matrix invents a relationship that isn't in the source.
    from app.pdf.ua_tagger import _cell_is_numericish, _row0_is_header

    def _grid(rows):
        return [[(0, 0, 0, c) for c in r] for r in rows]

    check("pdf: labels-over-numbers grid keeps its header",
          _row0_is_header(_grid([["Region", "Q1", "Q2"], ["North", "120", "140"], ["South", "90", "110"]])))
    check("pdf: text grid keeps its header (top-row labels are the convention)",
          _row0_is_header(_grid([["Region of operation", "Annual quota"], ["Northwest region", "Forty two units"]])))
    check("pdf: ALL-NUMERIC matrix gets NO fabricated header",
          not _row0_is_header(_grid([["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"]])))
    check("pdf: numeric first row is data, not a header",
          not _row0_is_header(_grid([["120", "140"], ["North", "South"]])))
    check("pdf: blank top row labels nothing",
          not _row0_is_header(_grid([["", ""], ["a", "b"]])))
    check("pdf: currency/percent/date cells read as numeric",
          all(_cell_is_numericish(t) for t in ["$12.50", "45%", "12/31/2025", "1,234", "(3)"]))

    # ---- CLI: analyze-only must not grade every document 'F' ----
    from app.cli.__main__ import _build_score_dict

    class _V2:
        def __init__(self, sev):
            self.severity = sev

    check("cli: clean document scores 100/A+", _build_score_dict([], [])["grade"] == "A+")
    one = _build_score_dict([_V2("warning")], [])
    check("cli: one warning is NOT graded 'F' (analyze-only)", one["grade"] != "F", str(one))
    check("cli: analyze-only never claims auto-fixes", one["fixedAutomatically"] == 0)

    # ---- the whole pass is failure-tolerant ----
    missing = derive_scan_fixes(tmp / "does-not-exist.html", res.tree)
    check("derive_scan_fixes degrades to {} on error (scan never 500s)", missing == {}, str(missing))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


def _walk(node):
    yield node
    for ch in node.children:
        yield from _walk(ch)


if __name__ == "__main__":
    sys.exit(main())
