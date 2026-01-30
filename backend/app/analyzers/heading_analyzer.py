"""Heading hierarchy analyzers."""

from __future__ import annotations

from app.analyzers.base import Analyzer
from app.analyzers.helpers import attach_flag, iter_nodes
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    HeadingNode,
)


class HeadingLevelJumpAnalyzer(Analyzer):
    name = "heading_level_jump"

    def analyze(self, tree: AccessibilityTree) -> None:
        previous_level: int | None = None
        for node in iter_nodes(tree):
            if isinstance(node, HeadingNode):
                if previous_level is not None and node.level > previous_level + 1:
                    attach_flag(node, AccessibilityFlagCode.HEADING_LEVEL_JUMP)
                previous_level = node.level


class SkippedHeadingLevelAnalyzer(Analyzer):
    name = "skipped_heading_level"

    def analyze(self, tree: AccessibilityTree) -> None:
        previous_level: int | None = None
        for node in iter_nodes(tree):
            if isinstance(node, HeadingNode):
                if previous_level is not None and node.level > previous_level + 1:
                    attach_flag(node, AccessibilityFlagCode.SKIPPED_HEADING_LEVEL)
                previous_level = node.level
