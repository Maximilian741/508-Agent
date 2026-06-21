"""Smoke: HTML inline-style colour-contrast detection (WCAG 2.1 SC 1.4.3).

The HTML parser now populates ``explicit_text_colors`` + ``bg_color`` from INLINE
styles so the shared :class:`ContrastAnalyzer` flags LOW_CONTRAST_TEXT on web
pages — but ONLY when BOTH the text colour and an effective background resolve to
a concrete sRGB value (the element's own ``background`` or the nearest inline-
styled ancestor's). This pins both halves of that contract:

  * REAL failures are caught — hex / rgb() / named, with CSS inheritance of
    colour and of the visual background, and the WCAG large-text exemption.
  * ZERO false positives — colour-with-no-known-background, translucent colours,
    hsl(), and class/stylesheet colours are all left UNflagged (we never assume
    a white page, so a light-on-dark themed page is never wrongly flagged).

Usage:
    python -m app.devtools.smoke_html_contrast
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_htmlc_')}/s.db")

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402

CONTRAST = "LOW_CONTRAST_TEXT"


def _flag_count(tree, code: str) -> int:
    n = 0

    def walk(node):
        nonlocal n
        n += sum(1 for f in node.accessibility_flags if f.code.value == code)
        for ch in node.children:
            walk(ch)

    walk(tree.root)
    return n


def _first_finding(tree):
    found = {}

    def walk(node):
        props = node.metadata.properties or {}
        if not found and props.get("contrast_finding"):
            found.update(props["contrast_finding"])
        for ch in node.children:
            walk(ch)

    walk(tree.root)
    return found


def _doc(inner: str, body_attrs: str = "") -> str:
    return f"<!DOCTYPE html><html lang=\"en\"><head><title>T</title></head><body{body_attrs}>{inner}</body></html>"


# (name, html, expected LOW_CONTRAST_TEXT count)
CASES = [
    # --- REAL failures (must flag) ---------------------------------------
    ("hex grey-on-white fails AA normal",
     _doc('<p style="color:#888888;background:#ffffff">grey on white ~3.5:1</p>'), 1),
    ("3-digit hex expands and fails",
     _doc('<p style="color:#888;background:#fff">short hex grey on white</p>'), 1),
    ("rgb() grey-on-white fails",
     _doc('<p style="color:rgb(140,140,140);background:rgb(255,255,255)">rgb grey</p>'), 1),
    ("named colours (gray on white) fail",
     _doc('<p style="color:gray;background:white">named gray on white</p>'), 1),
    ("background-color resolves the same as background shorthand",
     _doc('<p style="color:#999999;background-color:#ffffff">grey, bg-color longhand</p>'), 1),
    ("CSS colour inheritance: bg on ancestor div applies to child text",
     _doc('<div style="background:#ffffff"><p style="color:#999999">grey inherits white bg</p></div>'), 1),
    ("CSS colour inheritance: colour on ancestor, bg on the text element",
     _doc('<div style="color:#888888"><p style="background:#ffffff">dark grey inherited onto white</p></div>'), 1),
    ("page-level <body> background is used (NOT assumed white)",
     _doc('<p style="color:#555555">dark text on a dark page</p>', body_attrs=' style="background:#444444"'), 1),
    ("background shorthand with image+colour still finds the colour",
     _doc('<p style="color:#888888;background:#ffffff url(x.png) no-repeat">grey on shorthand</p>'), 1),

    # --- WCAG large-text exemption ---------------------------------------
    ("large text (20pt) passes at 3:1 and is NOT flagged",
     _doc('<p style="color:#888888;background:#ffffff;font-size:20pt">large grey ~3.5:1 passes</p>'), 0),
    ("bold 16pt grey counts as large text and is NOT flagged",
     _doc('<p style="color:#888888;background:#ffffff;font-size:16pt;font-weight:bold">bold large grey</p>'), 0),
    ("small grey text (10pt) still fails normal threshold",
     _doc('<p style="color:#888888;background:#ffffff;font-size:10pt">small grey fails</p>'), 1),

    # --- ZERO false positives (must NOT flag) ----------------------------
    ("good contrast (black on white) not flagged",
     _doc('<p style="color:#000000;background:#ffffff">black on white</p>'), 0),
    ("colour set but NO known background -> never assume white -> not flagged",
     _doc('<p style="color:#cccccc">light grey, unknown background (could be dark theme)</p>'), 0),
    ("translucent rgba colour is unresolvable -> not flagged",
     _doc('<p style="color:rgba(0,0,0,0.3);background:#ffffff">translucent black</p>'), 0),
    ("8-digit hex with alpha < ff is unresolvable -> not flagged",
     _doc('<p style="color:#0000004d;background:#ffffff">hex8 translucent</p>'), 0),
    ("hsl() colour is unresolvable -> not flagged",
     _doc('<p style="color:hsl(0,0%,40%);background:#ffffff">hsl grey</p>'), 0),
    ("transparent background does not count as a known background -> not flagged",
     _doc('<p style="color:#888888;background:transparent">grey on transparent</p>'), 0),
    ("class/stylesheet colours are not read -> not flagged",
     '<!DOCTYPE html><html lang="en"><head><title>T</title>'
     '<style>.g{color:#888;background:#fff}</style></head>'
     '<body><p class="g">class-styled grey, no inline style</p></body></html>', 0),
    ("good contrast via inheritance not flagged",
     _doc('<div style="background:#ffffff;color:#222222"><p>dark on white inherited</p></div>'), 0),
    ("child overrides colour to a good value -> ambiguous block -> not flagged",
     _doc('<p style="color:#cccccc;background:#ffffff"><span style="color:#000000">all text is actually black</span></p>'), 0),
    ("url() fragment id that looks like a hex is NOT treated as a background",
     _doc('<p style="color:#888888;background:url(sprite.svg#a0f0c0) no-repeat">no resolvable bg</p>'), 0),

    # --- robustness extras ----------------------------------------------
    ("inert inline children (<b>/<i>) do not block a real finding",
     _doc('<p style="color:#888888;background:#ffffff">grey with <b>bold</b> and <i>italic</i> runs</p>'), 1),
    ("!important on colour is still parsed and flagged",
     _doc('<p style="color:#888888 !important;background:#ffffff">grey important</p>'), 1),
]


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="html_contrast_"))
    for i, (name, html, expected) in enumerate(CASES):
        p = tmp / f"case_{i}.html"
        p.write_text(html, encoding="utf-8")
        res = parse_to_tree(str(p))
        run_analyzers(res.tree)
        got = _flag_count(res.tree, CONTRAST)
        check(name, got == expected, f"expected {expected} got {got}")

    # Evidence shape: a flagged node records fg/bg/ratio/required for the report.
    p = tmp / "evidence.html"
    p.write_text(_doc('<p style="color:#888888;background:#ffffff">grey</p>'), encoding="utf-8")
    res = parse_to_tree(str(p))
    run_analyzers(res.tree)
    f = _first_finding(res.tree)
    check("finding records fg/bg/ratio/required evidence",
          f.get("fg") == "888888" and f.get("bg") == "FFFFFF"
          and isinstance(f.get("ratio"), (int, float)) and f.get("required") == 4.5,
          str(f))
    check("recorded ratio is ~3.5:1 (real WCAG math, not a placeholder)",
          3.3 <= float(f.get("ratio", 0)) <= 3.7, str(f.get("ratio")))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
