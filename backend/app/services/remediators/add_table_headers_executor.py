"""Executor that adds table header cells to data tables that lack them."""

from __future__ import annotations

from collections import Counter
from typing import Optional

from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    ContentKind,
    NodeContent,
    TableCellNode,
    TableCellType,
    TableHeaderScope,
    TableNode,
    TableRowNode,
    iter_reading_order,
)
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


class AddTableHeadersExecutor(RemediationExecutor):
    """Promotes the first row of a header-less table to header cells.

    The strategy is:

    1. If the first row contains exclusively non-empty cells whose text looks
       like a label (no trailing punctuation, ≤ 8 words), convert each cell to a
       :class:`TableCellNode` of ``cell_type=HEADER`` and ``header_scope=COLUMN``.
    2. Otherwise insert a synthetic header row with placeholder labels
       (``Column 1`` … ``Column N``) so screen readers always have anchors.
    """

    supported_actions = [ActionCode.ADD_TABLE_HEADERS]

    def execute(
        self, plan: RemediationPlan, tree: Optional[AccessibilityTree] = None
    ) -> ExecutionResult:
        action_code = self._first_action(plan)
        self._ensure_supported(action_code)
        if tree is None:
            return _result(action_code, plan, ExecutionStatus.SKIPPED, "No tree provided.")

        target = _find_table(tree, plan.target_node_id)
        if target is None:
            return _result(action_code, plan, ExecutionStatus.SKIPPED, "Target table not found.")
        if not _has_flag(plan, target, AccessibilityFlagCode.TABLE_MISSING_HEADERS):
            return _result(
                action_code,
                plan,
                ExecutionStatus.SKIPPED,
                "Table is not flagged as missing headers; no changes applied.",
            )

        rows = [child for child in target.children if isinstance(child, TableRowNode)]
        if not rows:
            row, columns = _synthesize_header_row(target.id, column_count=2)
            target.children.insert(0, row)
            return _result(
                action_code,
                plan,
                ExecutionStatus.SUCCESS,
                f"Inserted synthetic header row with {columns} columns (table was empty).",
            )

        first_row = rows[0]
        existing_cells = [c for c in first_row.children if isinstance(c, TableCellNode)]
        if existing_cells and all(c.cell_type == TableCellType.HEADER for c in existing_cells):
            return _result(
                action_code,
                plan,
                ExecutionStatus.SKIPPED,
                "First row already consists of header cells.",
            )

        if existing_cells and _looks_like_header_row(existing_cells):
            for cell in existing_cells:
                cell.cell_type = TableCellType.HEADER
                if cell.header_scope == TableHeaderScope.NONE:
                    cell.header_scope = TableHeaderScope.COLUMN
            return _result(
                action_code,
                plan,
                ExecutionStatus.SUCCESS,
                f"Promoted {len(existing_cells)} cells in row 1 to TH/scope=col.",
            )

        # Use the MODAL (most-common) row width, not max(): a jagged table whose
        # rows have differing cell counts would otherwise get a header wider than
        # the table grid, which a real Office engine reflows (mangling the grid).
        # The dominant row width matches the declared <w:tblGrid> column count.
        width_counts = Counter(len(r.children) for r in rows)
        column_count = width_counts.most_common(1)[0][0] if width_counts else 2
        synthetic_row, columns = _synthesize_header_row(target.id, column_count=column_count)
        target.children.insert(0, synthetic_row)
        return _result(
            action_code,
            plan,
            ExecutionStatus.SUCCESS,
            f"Inserted synthetic header row with {columns} placeholder columns.",
        )


def _find_table(tree: AccessibilityTree, node_id: str) -> Optional[TableNode]:
    for node in iter_reading_order(tree.root):
        if node.id == node_id and isinstance(node, TableNode):
            return node
    return None


def _has_flag(plan: RemediationPlan, target, code: AccessibilityFlagCode) -> bool:
    if plan.flag.code == code:
        return True
    return any(flag.code == code for flag in target.accessibility_flags)


def _looks_like_header_row(cells) -> bool:
    if not cells:
        return False
    for cell in cells:
        text = (cell.content.text or "").strip() if cell.content else ""
        if not text:
            return False
        if len(text.split()) > 8:
            return False
        if text.endswith((".", "!", "?")):
            return False
    return True


def _synthesize_header_row(table_id: str, *, column_count: int) -> tuple[TableRowNode, int]:
    column_count = max(1, column_count)
    cells = []
    for i in range(column_count):
        cells.append(
            TableCellNode(
                id=f"{table_id}-th-{i + 1}",
                cell_type=TableCellType.HEADER,
                header_scope=TableHeaderScope.COLUMN,
                content=NodeContent(kind=ContentKind.TEXT, text=f"Column {i + 1}"),
                metadata=_passthrough_metadata(),
                children=[],
                accessibility_flags=[],
            )
        )
    row = TableRowNode(
        id=f"{table_id}-thead",
        content=NodeContent(kind=ContentKind.NONE),
        metadata=_passthrough_metadata(),
        children=cells,
        accessibility_flags=[],
    )
    return row, column_count


def _passthrough_metadata():
    from app.models.accessibility import NodeMetadata

    return NodeMetadata(properties={"synthesized": True})


def _result(
    action_code: ActionCode,
    plan: RemediationPlan,
    status: ExecutionStatus,
    notes: str,
) -> ExecutionResult:
    return ExecutionResult(
        action_code=action_code,
        target_node_id=plan.target_node_id,
        status=status,
        notes=notes,
    )
