"""Table-related analyzers."""

from __future__ import annotations

from app.analyzers.base import Analyzer
from app.analyzers.helpers import attach_flag, iter_nodes
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    TableCellNode,
    TableCellType,
    TableHeaderScope,
    TableNode,
    TableRowNode,
)


class TableMissingHeadersAnalyzer(Analyzer):
    name = "table_missing_headers"

    def analyze(self, tree: AccessibilityTree) -> None:
        for node in iter_nodes(tree):
            if isinstance(node, TableNode):
                header_found = False
                for row in node.children:
                    if not isinstance(row, TableRowNode):
                        continue
                    for cell in row.children:
                        if isinstance(cell, TableCellNode) and cell.cell_type == TableCellType.HEADER:
                            header_found = True
                            break
                    if header_found:
                        break
                if not header_found:
                    attach_flag(node, AccessibilityFlagCode.TABLE_MISSING_HEADERS)


class TableHeaderScopeAnalyzer(Analyzer):
    name = "table_header_scope_invalid"

    def analyze(self, tree: AccessibilityTree) -> None:
        for node in iter_nodes(tree):
            if isinstance(node, TableCellNode) and node.cell_type == TableCellType.HEADER:
                if node.header_scope == TableHeaderScope.NONE:
                    attach_flag(node, AccessibilityFlagCode.TABLE_HEADER_SCOPE_INVALID)
