"""List structure analyzers."""

from __future__ import annotations

from app.analyzers.base import Analyzer
from app.analyzers.helpers import attach_flag, iter_nodes
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ListItemNode,
    ListNode,
)


class ListStructureAnalyzer(Analyzer):
    name = "list_structure_invalid"

    def analyze(self, tree: AccessibilityTree) -> None:
        for node in iter_nodes(tree):
            if isinstance(node, ListNode):
                if not node.children:
                    attach_flag(node, AccessibilityFlagCode.LIST_STRUCTURE_INVALID)
                    continue
                for child in node.children:
                    if not isinstance(child, ListItemNode):
                        attach_flag(node, AccessibilityFlagCode.LIST_STRUCTURE_INVALID)
                        break
