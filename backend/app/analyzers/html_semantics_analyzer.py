"""HTML-only analyzers for three common WCAG failures with no prior coverage.

All three are counted by the parser using the SAME iterators the writer applies,
so the number reported is exactly the number that gets fixed (the project's
honesty invariant — see html_parser.iter_autocomplete_candidates /
iter_positive_tabindex / count_untitled_iframes).

* IFRAME_TITLE_MISSING (4.1.2 / 2.4.1) — an embedded map, video or widget with
  no accessible name is announced only as "frame". Manual: naming it requires
  knowing what is inside, which we can't read across the frame boundary.
* INPUT_AUTOCOMPLETE_MISSING (1.3.5, AA) — fields collecting a user's own data
  must identify their purpose so browsers/AT can autofill them. Auto-fixable,
  but only for fields whose purpose is UNAMBIGUOUS.
* POSITIVE_TABINDEX (2.4.3) — ``tabindex="3"`` pulls an element to the front of
  the page's tab sequence, so keyboard focus jumps unpredictably. Auto-fixable:
  ``tabindex="0"`` keeps it focusable in natural order.
"""

from __future__ import annotations

from app.analyzers.base import Analyzer
from app.analyzers.helpers import attach_flag
from app.models.accessibility import AccessibilityFlagCode, AccessibilityTree


def _props(tree: AccessibilityTree) -> dict:
    return (tree.root.metadata.properties or {}) if tree.root.metadata else {}


class IframeTitleAnalyzer(Analyzer):
    """Flags frames with no accessible name (HTML only)."""

    name = "iframe_title_missing"

    def analyze(self, tree: AccessibilityTree) -> None:
        if int(_props(tree).get("iframes_missing_title") or 0) > 0:
            attach_flag(tree.root, AccessibilityFlagCode.IFRAME_TITLE_MISSING)


class InputAutocompleteAnalyzer(Analyzer):
    """Flags inputs whose purpose is unambiguous but undeclared (WCAG 1.3.5)."""

    name = "input_autocomplete_missing"

    def analyze(self, tree: AccessibilityTree) -> None:
        if int(_props(tree).get("inputs_missing_autocomplete") or 0) > 0:
            attach_flag(tree.root, AccessibilityFlagCode.INPUT_AUTOCOMPLETE_MISSING)


class PositiveTabindexAnalyzer(Analyzer):
    """Flags positive tabindex values that scramble keyboard focus order."""

    name = "positive_tabindex"

    def analyze(self, tree: AccessibilityTree) -> None:
        if int(_props(tree).get("positive_tabindex_count") or 0) > 0:
            attach_flag(tree.root, AccessibilityFlagCode.POSITIVE_TABINDEX)
