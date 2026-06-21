"""Colour-contrast analyzer (WCAG 2.1 SC 1.4.3).

Flags text whose explicitly-set colour does not meet the AA minimum contrast
ratio against its (best-known) background. The parser attaches, per text node:

    metadata.properties["explicit_text_colors"] = [{"c": "RRGGBB", "sz": pt, "b": bold}, ...]
    metadata.properties["bg_color"] = "RRGGBB"   # effective background, default white

HONEST SCOPE: we only evaluate runs whose colour is *explicitly* set to a
concrete sRGB value, against an explicit background where one is known (else a
white default). Theme-/inherited-colour runs and unknown backgrounds are left
unflagged rather than guessed — so this never produces a false "fails contrast"
for colours we couldn't actually determine. Because the background is sometimes
assumed, the finding is a WARNING ("may not meet"), not a hard error.
"""

from __future__ import annotations

from app.analyzers.base import Analyzer
from app.analyzers.contrast import contrast_ratio, required_ratio, suggest_passing_fg
from app.analyzers.helpers import attach_flag, iter_nodes
from app.models.accessibility import AccessibilityFlagCode, AccessibilityTree

# Small tolerance so a colour sitting exactly on the threshold (e.g. 4.50) is
# not flagged due to floating-point noise.
_TOLERANCE = 0.05
_DEFAULT_BG = "FFFFFF"


class ContrastAnalyzer(Analyzer):
    name = "low_contrast_text"

    def analyze(self, tree: AccessibilityTree) -> None:
        for node in iter_nodes(tree):
            props = node.metadata.properties or {}
            runs = props.get("explicit_text_colors")
            if not isinstance(runs, list) or not runs:
                continue
            bg = props.get("bg_color") or _DEFAULT_BG

            worst = None  # (ratio, fg, required)
            for run in runs:
                if not isinstance(run, dict):
                    continue
                fg = run.get("c")
                if not fg:
                    continue
                ratio = contrast_ratio(fg, bg)
                if ratio is None:
                    continue
                required = required_ratio(run.get("sz"), bool(run.get("b")))
                if ratio + _TOLERANCE < required:
                    if worst is None or ratio < worst[0]:
                        worst = (ratio, str(fg), required)

            if worst is not None:
                ratio, fg, required = worst
                # Record evidence on the node for the report / UI.
                finding = {
                    "fg": fg,
                    "bg": bg,
                    "ratio": round(ratio, 2),
                    "required": required,
                }
                # Hand the user the exact accessible colour to use, so the fix
                # is one decision instead of trial-and-error. We only ever
                # suggest a new TEXT colour (never silently recolour anything).
                suggested = suggest_passing_fg(fg, bg, required)
                if suggested:
                    finding["suggested_fg"] = suggested
                    sr = contrast_ratio(suggested, bg)
                    if sr is not None:
                        finding["suggested_ratio"] = round(sr, 2)
                props["contrast_finding"] = finding
                node.metadata.properties = props
                attach_flag(node, AccessibilityFlagCode.LOW_CONTRAST_TEXT)
