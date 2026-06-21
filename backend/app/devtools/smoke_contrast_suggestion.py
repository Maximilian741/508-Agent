"""Smoke: contrast remediation suggestion (the nearest AA-passing text colour).

LOW_CONTRAST_TEXT stays detect-only (we never silently recolour a document), but
the analyzer now computes the nearest accessible *text* colour and records it on
the finding, and the remediation engine surfaces it in the violation evidence so
the UI/report can show the exact fix. This pins:

  * suggest_passing_fg(): returns a colour that actually meets the target ratio,
    is the minimal change (nearest direction), respects the large-text threshold,
    leaves the background untouched, and returns None when nothing to fix.
  * ContrastAnalyzer enriches contrast_finding with suggested_fg/suggested_ratio.
  * _collect_evidence() copies fg/bg/ratio/required/suggested_fg/suggested_ratio
    onto the LOW_CONTRAST_TEXT violation that reaches the API/frontend.

Format-independent (drives the shared analyzer directly), so it holds regardless
of which document formats populate contrast.

Usage:
    python -m app.devtools.smoke_contrast_suggestion
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_csg_')}/s.db")

from app.analyzers.contrast import (  # noqa: E402
    AA_LARGE,
    AA_NORMAL,
    contrast_ratio,
    parse_hex,
    relative_luminance,
    suggest_passing_fg,
)
from app.analyzers.contrast_analyzer import ContrastAnalyzer  # noqa: E402
from app.models.accessibility import (  # noqa: E402
    AccessibilityFlag,
    AccessibilityFlagCode,
    AccessibilityTree,
    ContentKind,
    DocumentNode,
    NodeContent,
    NodeMetadata,
    ParagraphNode,
)
from app.services.remediation_engine import _collect_evidence  # noqa: E402


def _para(props):
    return ParagraphNode(
        id="p-1",
        content=NodeContent(kind=ContentKind.TEXT, text="some text"),
        metadata=NodeMetadata(source_format="docx", properties=props),
        children=[],
        accessibility_flags=[],
    )


def _tree(node):
    root = DocumentNode(
        id="doc-1",
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(source_format="docx", properties={}),
        children=[node],
        accessibility_flags=[],
    )
    return AccessibilityTree(root=root, metadata={})


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    # ----------------------------------------------------- suggest_passing_fg
    s = suggest_passing_fg("888888", "FFFFFF", AA_NORMAL)
    check("grey-on-white suggestion exists", s is not None, str(s))
    if s:
        r = contrast_ratio(s, "FFFFFF")
        check("suggestion actually passes AA normal", r is not None and r >= AA_NORMAL, f"{s} -> {r}")
        check("suggestion darkened the text (toward higher contrast on white)",
              relative_luminance(parse_hex(s)) < relative_luminance(parse_hex("888888")),
              f"lum {s} vs 888888")

    check("already-passing colour yields no suggestion (black on white)",
          suggest_passing_fg("000000", "FFFFFF", AA_NORMAL) is None)

    # Light grey on BLACK fails; nearest fix is to LIGHTEN (toward white), not darken.
    s2 = suggest_passing_fg("555555", "000000", AA_NORMAL)
    check("grey-on-black suggestion exists", s2 is not None, str(s2))
    if s2:
        r2 = contrast_ratio(s2, "000000")
        check("grey-on-black suggestion passes AA", r2 is not None and r2 >= AA_NORMAL, f"{s2} -> {r2}")
        check("grey-on-black suggestion lightened the text",
              relative_luminance(parse_hex(s2)) > relative_luminance(parse_hex("555555")),
              f"lum {s2} vs 555555")

    # Large-text threshold: #888 on white is ~3.5:1 -> passes AA_LARGE (3.0),
    # so as large text there is nothing to suggest; as normal text there is.
    check("large-text (3:1) needs no suggestion for #888 on white",
          suggest_passing_fg("888888", "FFFFFF", AA_LARGE) is None)
    check("normal-text (4.5:1) does need a suggestion for #888 on white",
          suggest_passing_fg("888888", "FFFFFF", AA_NORMAL) is not None)

    check("unparseable inputs yield no suggestion",
          suggest_passing_fg("nope", "FFFFFF", AA_NORMAL) is None
          and suggest_passing_fg("888888", "zzz", AA_NORMAL) is None)

    # ------------------------------------------------ analyzer enrichment
    node = _para({"explicit_text_colors": [{"c": "888888", "sz": None, "b": False}], "bg_color": "FFFFFF"})
    tree = _tree(node)
    ContrastAnalyzer().analyze(tree)
    flagged = any(f.code == AccessibilityFlagCode.LOW_CONTRAST_TEXT for f in node.accessibility_flags)
    check("analyzer flags the low-contrast paragraph", flagged)
    cf = node.metadata.properties.get("contrast_finding")
    check("contrast_finding recorded", isinstance(cf, dict), str(cf))
    if isinstance(cf, dict):
        check("finding carries suggested_fg", isinstance(cf.get("suggested_fg"), str), str(cf))
        check("finding carries suggested_ratio >= required",
              isinstance(cf.get("suggested_ratio"), (int, float)) and cf["suggested_ratio"] >= cf["required"],
              str(cf))
        check("suggestion keeps the SAME background (we never recolour the page)",
              cf.get("bg") == "FFFFFF", str(cf))

    # ------------------------------------------------ evidence bridge -> API
    flag = AccessibilityFlag.from_code(AccessibilityFlagCode.LOW_CONTRAST_TEXT)
    ev = _collect_evidence(node, flag)
    for key in ("fg", "bg", "ratio", "required", "suggested_fg", "suggested_ratio"):
        check(f"evidence surfaces '{key}' to the API/frontend", key in ev, str(ev))

    # A good-contrast node: no flag, no suggestion in evidence.
    good = _para({"explicit_text_colors": [{"c": "000000", "sz": None, "b": False}], "bg_color": "FFFFFF"})
    gtree = _tree(good)
    ContrastAnalyzer().analyze(gtree)
    check("good-contrast node is not flagged",
          not any(f.code == AccessibilityFlagCode.LOW_CONTRAST_TEXT for f in good.accessibility_flags))
    check("good-contrast node records no contrast_finding",
          "contrast_finding" not in (good.metadata.properties or {}))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
