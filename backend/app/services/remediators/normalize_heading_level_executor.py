"""Stub executor for normalizing heading levels."""

from __future__ import annotations

from app.models.accessibility import ActionCode, RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


class NormalizeHeadingLevelExecutor(RemediationExecutor):
    supported_actions = [ActionCode.NORMALIZE_HEADING_LEVEL]

    def execute(self, plan: RemediationPlan) -> ExecutionResult:
        action_code = self._first_action(plan)
        self._ensure_supported(action_code)
        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.NOT_IMPLEMENTED,
            notes="Would normalize heading level to preserve hierarchy.",
        )
