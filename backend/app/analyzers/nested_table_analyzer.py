"""Nested-table analyzer.

Flags any table that is nested inside another table's cell. Nested tables are a
well-known accessibility problem (WCAG 1.3.1; Section 508 E205.2; PDF/UA 7.5):
a screen reader has to announce the inner table's row/column structure *while*
the user is mid-cell in the outer table, which is disorienting and frequently
unreadable. Manual auditors and tools (Acrobat, CommonLook) routinely flag it.

This is DETECTION-ONLY. Flattening or re-laying-out a nested table is a content
decision a human must make, so it maps to ``FLAG_FOR_MANUAL_REVIEW`` — never
auto-applied and never counted as an automated fix (honesty invariant).

We flag the *inner* table (the one that should not be there): each table whose
ancestor chain already contains another table gets exactly one flag, regardless
of nesting depth, so a doubly-nested table is reported once and the outer table
is never falsely flagged.
"""

from __future__ import annotations

from app.analyzers.base import Analyzer
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    Node,
    TableNode,
)
from app.analyzers.helpers import attach_flag


def _walk(root: Node, inside_table: bool) -> None:
    # Explicit stack, not recursion: an HTML page can nest lists/tables
    # hundreds deep, and a recursive walk raised RecursionError there.
    stack = [(root, inside_table)]
    while stack:
        node, inside = stack.pop()
        if isinstance(node, TableNode):
            if inside:
                attach_flag(node, AccessibilityFlagCode.TABLE_NESTED)
            # Everything beneath this table is "inside a table" for its descendants.
            inside = True
        for child in reversed(node.children):
            stack.append((child, inside))


class NestedTableAnalyzer(Analyzer):
    """Flags tables nested inside another table's cell (detection-only)."""

    name = "nested_table"

    def analyze(self, tree: AccessibilityTree) -> None:
        _walk(tree.root, False)
