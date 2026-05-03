"""Heading text content analyzer."""

from __future__ import annotations

from app.analyzers.base import Analyzer
from app.analyzers.helpers import attach_flag, iter_nodes
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ContentKind,
    HeadingNode,
)


class HeadingTextEmptyAnalyzer(Analyzer):
    """Flags headings whose text is empty or whitespace-only.

    Screen readers announce a heading with its text. An empty heading is
    announced as "Heading: " with nothing afterward, leaving the user with
    a navigation landmark and no content.
    """

    name = "heading_text_empty"

    def analyze(self, tree: AccessibilityTree) -> None:
        for node in iter_nodes(tree):
            if not isinstance(node, HeadingNode):
                continue
            if node.content.kind != ContentKind.TEXT:
                attach_flag(node, AccessibilityFlagCode.HEADING_TEXT_EMPTY)
                continue
            text = (node.content.text or "").strip()
            if not text:
                attach_flag(node, AccessibilityFlagCode.HEADING_TEXT_EMPTY)
