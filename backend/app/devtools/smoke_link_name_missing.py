"""Smoke: nameless-link detection (LINK_NAME_MISSING, WCAG 2.4.4 / 4.1.2).

A link with NO accessible name — an icon-font link, an inline-SVG icon with no
title, an empty-element link — announces to a screen reader as just "link" with
no hint of its destination. LinkTextAnalyzer only covers links that HAVE text;
this closes the element-only gap. It is detect-only (we can't invent a link's
purpose), so the test pins DETECTION precision (zero false positives) and the
no-double-flag contract with the alt-text detector.

Usage:
    python -m app.devtools.smoke_link_name_missing
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_lnm_')}/s.db")

from pathlib import Path  # noqa: E402

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.api.pipeline import _action_persists  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402

NAME_MISSING = "LINK_NAME_MISSING"
NON_DESC = "LINK_TEXT_NON_DESCRIPTIVE"
ALT_MISSING = "MISSING_ALT_TEXT"


def _doc(body: str) -> str:
    return f"<!DOCTYPE html><html lang=\"en\"><head><title>t</title></head><body>{body}</body></html>"


# Each fixture: (name, body, expected flag counts dict)
CASES = [
    ("icon-font link (no name)",
     '<a href="/home"><span class="icon-home"></span></a>',
     {NAME_MISSING: 1}),
    ("inline-SVG icon link (no title)",
     '<a href="/menu"><svg viewBox="0 0 10 10"><path d="M0 0h10v10H0z"/></svg></a>',
     {NAME_MISSING: 1}),
    ("decorative-img-only link (alt='')",
     '<a href="/x"><img src="i.png" alt=""></a>',
     {NAME_MISSING: 1}),
    ("aria-label icon link (named)",
     '<a href="/home" aria-label="Home"><span class="icon-home"></span></a>',
     {NAME_MISSING: 0}),
    ("title icon link (named)",
     '<a href="/home" title="Home"><span class="icon"></span></a>',
     {NAME_MISSING: 0}),
    ("aria-labelledby icon link (named)",
     '<span id="lbl">Home</span><a href="/home" aria-labelledby="lbl"><span class="icon"></span></a>',
     {NAME_MISSING: 0}),
    ("SVG-with-title link (named)",
     '<a href="/menu"><svg><title>Open menu</title><path d="M0 0h1v1z"/></svg></a>',
     {NAME_MISSING: 0}),
    ("descendant aria-label link (named)",
     '<a href="/menu"><span aria-label="Open menu" class="icon"></span></a>',
     {NAME_MISSING: 0}),
    ("img-with-alt link (named)",
     '<a href="/home"><img src="h.png" alt="Home"></a>',
     {NAME_MISSING: 0}),
    # Missing-alt image link: deferred to the alt-text detector — the LINK is NOT
    # flagged (no double-flag); the IMAGE is flagged MISSING_ALT_TEXT instead.
    ("img-missing-alt link (defer to alt)",
     '<a href="/home"><img src="h.png"></a>',
     {NAME_MISSING: 0, ALT_MISSING: 1}),
    # Text links are LinkTextAnalyzer's job, never LINK_NAME_MISSING.
    ("good text link",
     '<a href="/home">Return to the homepage</a>',
     {NAME_MISSING: 0, NON_DESC: 0}),
    ("non-descriptive text link",
     '<a href="/home">click here</a>',
     {NAME_MISSING: 0, NON_DESC: 1}),
    # Removed from the a11y tree -> not a real defect (no false positive).
    ("aria-hidden icon link (out of a11y tree)",
     '<a href="/home" aria-hidden="true" tabindex="-1"><span class="icon"></span></a>',
     {NAME_MISSING: 0}),
    ("icon link inside aria-hidden container",
     '<div aria-hidden="true"><a href="/home"><span class="icon"></span></a></div>',
     {NAME_MISSING: 0}),
    ("hidden icon link",
     '<a href="/home" hidden><span class="icon"></span></a>',
     {NAME_MISSING: 0}),
    ("role=presentation icon link",
     '<a href="/home" role="presentation"><span class="icon"></span></a>',
     {NAME_MISSING: 0}),
]


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

    # Honesty: LINK_NAME_MISSING is manual-only — never credited as a persisted fix.
    for fmt in ("html", "docx", "pptx", "pdf"):
        check(f"honesty: LINK_NAME_MISSING never persisted for {fmt}",
              not _action_persists("LINK_NAME_MISSING", fmt))

    tmp = Path(tempfile.mkdtemp(prefix="lnm_smoke_"))
    for i, (label, body, expected) in enumerate(CASES):
        src = tmp / f"case{i}.html"
        src.write_text(_doc(body), encoding="utf-8")
        tree = parse_to_tree(str(src)).tree
        run_analyzers(tree)
        for code, want in expected.items():
            got = _flag_count(tree, code)
            check(f"{label}: {code} == {want}", got == want, f"got {got}")

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
