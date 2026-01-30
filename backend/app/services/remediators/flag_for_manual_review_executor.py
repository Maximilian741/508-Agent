"""Stub executor for flagging manual review."""

from __future__ import annotations

from app.models.accessibility import ActionCode, RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


class FlagForManualReviewExecutor(RemediationExecutor):
    supported_actions = [ActionCode.FLAG_FOR_MANUAL_REVIEW]

    def execute(self, plan: RemediationPlan) -> ExecutionResult:
        action_code = self._first_action(plan)
        self._ensure_supported(action_code)
        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.SKIPPED,
            notes="Manual review required; no automated action taken.",
        )
