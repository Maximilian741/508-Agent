"""Executor for removing decorative alt text."""

from __future__ import annotations

from typing import Optional

from app.models.accessibility import AccessibilityTree, ActionCode, ImageNode, iter_reading_order
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


class RemoveDecorativeAltTextExecutor(RemediationExecutor):
    supported_actions = [ActionCode.REMOVE_DECORATIVE_ALT_TEXT]

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
        target = _find_node(tree, plan.target_node_id)
        if target is None:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Target node not found; no changes applied.",
            )
        if not isinstance(target, ImageNode):
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Target node is not an image; no changes applied.",
            )
        if not target.is_decorative:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Image is not decorative; no changes applied.",
            )
        previous_alt = target.alt_text
        target.alt_text = None
        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.SUCCESS,
            notes=f"Removed decorative alt text. Previous alt_text={previous_alt!r}.",
        )


def _find_node(tree: AccessibilityTree, node_id: str) -> Optional[object]:
    for node in iter_reading_order(tree.root):
        if node.id == node_id:
            return node
    return None
