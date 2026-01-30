"""Executor for fixing list structure."""

from __future__ import annotations

from typing import Optional

from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    ListItemNode,
    ListNode,
    iter_reading_order,
)
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


class FixListStructureExecutor(RemediationExecutor):
    supported_actions = [ActionCode.FIX_LIST_STRUCTURE]

    def execute(self, plan: RemediationPlan, tree: Optional[AccessibilityTree] = None) -> ExecutionResult:
        action_code = self._first_action(plan)
        self._ensure_supported(action_code)
        if tree is None:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="No accessibility tree provided; action not executed.",
            )
        target = _find_list_node(tree, plan.target_node_id)
        if target is None:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Target node not found; no changes applied.",
            )
        if not isinstance(target, ListNode):
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Target node is not a list; no changes applied.",
            )
        if not _has_list_structure_flag(plan, target):
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="List does not include a list-structure flag; no changes applied.",
            )

        before_summary = _child_summary(target)
        new_children: list = []
        wrappers_created = 0
        for index, child in enumerate(target.children):
            if isinstance(child, ListItemNode):
                new_children.append(child)
                continue
            wrapper_id = f"{target.id}-li-{index}"
            wrapper = ListItemNode(
                id=wrapper_id,
                content=child.content,
                metadata=child.metadata,
                children=[child],
                accessibility_flags=[],
            )
            new_children.append(wrapper)
            wrappers_created += 1
        target.children = new_children
        after_summary = _child_summary(target)

        if wrappers_created == 0:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes=f"No change needed; list already valid. Before={before_summary} After={after_summary}.",
            )
        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.SUCCESS,
            notes=(
                f"Wrapped {wrappers_created} non-list-item children. "
                f"Before={before_summary} After={after_summary}."
            ),
        )


def _find_list_node(tree: AccessibilityTree, target_node_id: str) -> Optional[ListNode]:
    for node in iter_reading_order(tree.root):
        if node.id == target_node_id and isinstance(node, ListNode):
            return node
    return None


def _has_list_structure_flag(plan: RemediationPlan, target: ListNode) -> bool:
    if plan.flag.code == AccessibilityFlagCode.LIST_STRUCTURE_INVALID:
        return True
    for flag in target.accessibility_flags:
        if flag.code == AccessibilityFlagCode.LIST_STRUCTURE_INVALID:
            return True
    return False


def _child_summary(target: ListNode) -> str:
    return ",".join([f"{child.node_type.value}:{child.id}" for child in target.children])
