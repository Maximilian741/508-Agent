"""Executor for flagging manual review."""

from __future__ import annotations

from typing import Optional

from app.models.accessibility import AccessibilityTree, ActionCode
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


class FlagForManualReviewExecutor(RemediationExecutor):
    supported_actions = [ActionCode.FLAG_FOR_MANUAL_REVIEW]

    def execute(self, plan: RemediationPlan, tree: Optional[AccessibilityTree] = None) -> ExecutionResult:
        has_manual_action = any(
            action.action_code == ActionCode.FLAG_FOR_MANUAL_REVIEW for action in plan.actions
        )
        if plan.actions and not has_manual_action:
            return ExecutionResult(
                action_code=ActionCode.FLAG_FOR_MANUAL_REVIEW,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Manual review action not present on plan; no changes applied.",
            )
        self._ensure_supported(ActionCode.FLAG_FOR_MANUAL_REVIEW)
        return ExecutionResult(
            action_code=ActionCode.FLAG_FOR_MANUAL_REVIEW,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.SUCCESS,
            notes="Manual review required; queued for human review.",
        )
