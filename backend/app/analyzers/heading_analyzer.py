"""Heading hierarchy analyzers."""

from __future__ import annotations

from app.analyzers.base import Analyzer
from app.analyzers.helpers import attach_flag, iter_nodes
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    HeadingNode,
    ParagraphNode,
)


class TextStyledAsHeadingAnalyzer(Analyzer):
    """Flag paragraphs that LOOK like headings but aren't (WCAG 1.3.1).

    The parser marks plain paragraphs whose presentation screams "heading" —
    Word's Title/Subtitle styles, or short all-bold text with an explicit
    >=14pt size — with ``properties["looks_like_heading"]``. Screen-reader
    users navigating by heading never find these sections. The classic
    title-page failure, present in most real customer documents.
    """

    name = "text_styled_as_heading"

    def analyze(self, tree: AccessibilityTree) -> None:
        for node in iter_nodes(tree):
            if isinstance(node, ParagraphNode):
                props = (node.metadata.properties if node.metadata else None) or {}
                if props.get("looks_like_heading"):
                    attach_flag(node, AccessibilityFlagCode.TEXT_STYLED_AS_HEADING)


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
    """DEPRECATED — condition-identical twin of :class:`HeadingLevelJumpAnalyzer`.

    Historically both ran, so every single heading jump produced TWO violations
    on the same node: one fixed, one suppressed as a duplicate and mis-counted
    as "pending manual" — inflating initialIssues and deflating the score with
    a phantom item. The class is kept for import compatibility but it is no
    longer registered in the default analyzer set.
    """

    name = "skipped_heading_level"

    def analyze(self, tree: AccessibilityTree) -> None:
        previous_level: int | None = None
        for node in iter_nodes(tree):
            if isinstance(node, HeadingNode):
                if previous_level is not None and node.level > previous_level + 1:
                    attach_flag(node, AccessibilityFlagCode.SKIPPED_HEADING_LEVEL)
                previous_level = node.level
