"""Link target validity analyzer."""

from __future__ import annotations

from app.analyzers.base import Analyzer
from app.analyzers.helpers import attach_flag, iter_nodes
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    LinkNode,
)


_BROKEN_EXACT_TARGETS = {"", "#", "http://", "https://"}
_BROKEN_PREFIXES = ("javascript:",)


def _is_broken_target(target: str | None) -> bool:
    if target is None:
        return True
    normalized = target.strip()
    if normalized.lower() in _BROKEN_EXACT_TARGETS:
        return True
    lowered = normalized.lower()
    return any(lowered.startswith(prefix) for prefix in _BROKEN_PREFIXES)


class LinkTargetBrokenAnalyzer(Analyzer):
    """Flags links whose target is missing, empty, a placeholder, or unsafe.

    A link with no destination (or a `javascript:` pseudo-target) leaves
    keyboard and screen reader users on a control that does nothing useful.
    """

    name = "link_target_broken"

    def analyze(self, tree: AccessibilityTree) -> None:
        for node in iter_nodes(tree):
            if not isinstance(node, LinkNode):
                continue
            if _is_broken_target(node.target):
                attach_flag(node, AccessibilityFlagCode.LINK_TARGET_BROKEN)
