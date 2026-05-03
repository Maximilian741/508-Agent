"""Executor that sets the ``scope`` attribute on table header cells."""

from __future__ import annotations

from typing import Optional

from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    TableCellNode,
    TableCellType,
    TableHeaderScope,
    TableNode,
    TableRowNode,
    iter_reading_order,
)
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


class SetTableHeaderScopeExecutor(RemediationExecutor):
    """Repair invalid/missing ``scope`` on a header cell.

    The executor accepts plans whose ``target_node_id`` is either a single
    :class:`TableCellNode` or the parent :class:`TableNode`.  When the parent
    table is targeted, every header cell that has ``header_scope=NONE`` is
    repaired in one pass.
    """

    supported_actions = [ActionCode.SET_TABLE_HEADER_SCOPE]

    def execute(
        self, plan: RemediationPlan, tree: Optional[AccessibilityTree] = None
    ) -> ExecutionResult:
        action_code = self._first_action(plan)
        self._ensure_supported(action_code)
        if tree is None:
            return _result(action_code, plan, ExecutionStatus.SKIPPED, "No tree provided.")

        target = _find_node(tree, plan.target_node_id)
        if target is None:
            return _result(action_code, plan, ExecutionStatus.SKIPPED, "Target node not found.")

        if isinstance(target, TableCellNode):
            return self._repair_cell(target, plan, action_code)
        if isinstance(target, TableNode):
            return self._repair_table(target, plan, action_code)
        return _result(action_code, plan, ExecutionStatus.SKIPPED, "Target is neither a table nor a cell.")

    def _repair_cell(
        self, cell: TableCellNode, plan: RemediationPlan, action_code: ActionCode
    ) -> ExecutionResult:
        if cell.cell_type != TableCellType.HEADER:
            return _result(
                action_code,
                plan,
                ExecutionStatus.SKIPPED,
                "Target cell is not a header; no changes applied.",
            )
        before = cell.header_scope
        cell.header_scope = _infer_scope_for_cell(cell)
        if cell.header_scope == before:
            return _result(
                action_code,
                plan,
                ExecutionStatus.SKIPPED,
                f"No change needed; scope remains {before.value!r}.",
            )
        return _result(
            action_code,
            plan,
            ExecutionStatus.SUCCESS,
            f"Set scope from {before.value!r} → {cell.header_scope.value!r}.",
        )

    def _repair_table(
        self, table: TableNode, plan: RemediationPlan, action_code: ActionCode
    ) -> ExecutionResult:
        if not _has_flag(plan, table, AccessibilityFlagCode.TABLE_HEADER_SCOPE_INVALID):
            return _result(
                action_code,
                plan,
                ExecutionStatus.SKIPPED,
                "Table is not flagged with invalid header scope; no changes applied.",
            )
        repaired = 0
        rows = [row for row in table.children if isinstance(row, TableRowNode)]
        for row_index, row in enumerate(rows):
            for cell_index, cell in enumerate(row.children):
                if not isinstance(cell, TableCellNode):
                    continue
                if cell.cell_type != TableCellType.HEADER:
                    continue
                inferred = _infer_scope_in_table(row_index, cell_index, rows)
                if cell.header_scope != inferred:
                    cell.header_scope = inferred
                    repaired += 1
        if repaired == 0:
            return _result(
                action_code,
                plan,
                ExecutionStatus.SKIPPED,
                "All header cells already have a valid scope.",
            )
        return _result(
            action_code,
            plan,
            ExecutionStatus.SUCCESS,
            f"Repaired scope on {repaired} header cell(s).",
        )


def _find_node(tree, node_id):
    for node in iter_reading_order(tree.root):
        if node.id == node_id:
            return node
    return None


def _has_flag(plan, target, code) -> bool:
    if plan.flag.code == code:
        return True
    return any(flag.code == code for flag in target.accessibility_flags)


def _infer_scope_for_cell(cell: TableCellNode) -> TableHeaderScope:
    """If the cell only has row context we use ROW, else COLUMN."""

    return TableHeaderScope.COLUMN if cell.row_span == 1 else TableHeaderScope.ROW


def _infer_scope_in_table(row_index, cell_index, rows) -> TableHeaderScope:
    if row_index == 0:
        return TableHeaderScope.COLUMN
    if cell_index == 0:
        return TableHeaderScope.ROW
    return TableHeaderScope.COLUMN


def _result(action_code: ActionCode, plan: RemediationPlan, status: ExecutionStatus, notes: str) -> ExecutionResult:
    return ExecutionResult(
        action_code=action_code,
        target_node_id=plan.target_node_id,
        status=status,
        notes=notes,
    )
