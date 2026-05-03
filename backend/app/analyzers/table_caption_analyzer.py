"""Table caption analyzer."""

from __future__ import annotations

from app.analyzers.base import Analyzer
from app.analyzers.helpers import attach_flag, iter_nodes
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ContentKind,
    HeadingNode,
    Node,
    ParagraphNode,
    TableNode,
)


def _has_caption_metadata(node: TableNode) -> bool:
    caption = node.metadata.properties.get("caption")
    if not isinstance(caption, str):
        return False
    return bool(caption.strip())


def _has_preceding_label(parent: Node, table: TableNode) -> bool:
    """True if the table has a Paragraph/Heading sibling within 1 slot before it."""
    children = list(parent.children)
    try:
        index = children.index(table)
    except ValueError:
        return False
    if index == 0:
        return False
    candidate = children[index - 1]
    if not isinstance(candidate, (ParagraphNode, HeadingNode)):
        return False
    if candidate.content.kind != ContentKind.TEXT:
        return False
    text = (candidate.content.text or "").strip()
    return bool(text)


class TableCaptionMissingAnalyzer(Analyzer):
    """Flags tables that lack both a caption metadata property and a
    preceding ParagraphNode/HeadingNode label within one sibling slot.

    A short labeling sentence/heading just above a data table gives screen
    reader users context before the row-by-row read-out begins.
    """

    name = "table_caption_missing"

    def analyze(self, tree: AccessibilityTree) -> None:
        for parent in iter_nodes(tree):
            for child in parent.children:
                if not isinstance(child, TableNode):
                    continue
                if _has_caption_metadata(child):
                    continue
                if _has_preceding_label(parent, child):
                    continue
                attach_flag(child, AccessibilityFlagCode.TABLE_CAPTION_MISSING)
