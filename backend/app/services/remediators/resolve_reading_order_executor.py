"""Stub executor for resolving reading order."""

from __future__ import annotations

from typing import Optional

from app.models.accessibility import AccessibilityTree, ActionCode
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


class ResolveReadingOrderExecutor(RemediationExecutor):
    supported_actions = [ActionCode.RESOLVE_READING_ORDER]

    def execute(self, plan: RemediationPlan, tree: Optional[AccessibilityTree] = None) -> ExecutionResult:
        action_code = self._first_action(plan)
        self._ensure_supported(action_code)
        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.NOT_IMPLEMENTED,
            notes="Would resolve ambiguous reading order.",
        )
