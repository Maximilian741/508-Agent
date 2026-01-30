"""Stub executor for removing decorative alt text."""

from __future__ import annotations

from app.models.accessibility import ActionCode, RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


class RemoveDecorativeAltTextExecutor(RemediationExecutor):
    supported_actions = [ActionCode.REMOVE_DECORATIVE_ALT_TEXT]

    def execute(self, plan: RemediationPlan) -> ExecutionResult:
        action_code = self._first_action(plan)
        self._ensure_supported(action_code)
        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.NOT_IMPLEMENTED,
            notes="Would remove alt text from decorative images.",
        )
