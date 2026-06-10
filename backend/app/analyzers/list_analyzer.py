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
    """Flag invalid list structure.

    Two failure modes:
    1. A ListNode with missing/non-ListItem children (malformed tree).
    2. A FAKE list — consecutive plain paragraphs typed as "- item" or
       "1. item" with no real numbering. The docx parser groups these runs
       and records ``fake_list_run_ids`` on the run's first paragraph; the
       flag lands there (one issue per typed list). FIX_LIST_STRUCTURE then
       converts the run into a real Word list (w:numPr + numbering.xml).
    """

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
                continue
            props = node.metadata.properties or {}
            if props.get("fake_list_run_ids"):
                attach_flag(node, AccessibilityFlagCode.LIST_STRUCTURE_INVALID)
