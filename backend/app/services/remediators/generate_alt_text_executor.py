"""Stub executor for generating alternative text."""

from __future__ import annotations

from typing import Optional

from app.models.accessibility import AccessibilityTree, ActionCode
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


class GenerateAltTextExecutor(RemediationExecutor):
    supported_actions = [ActionCode.GENERATE_ALT_TEXT]

    def execute(self, plan: RemediationPlan, tree: Optional[AccessibilityTree] = None) -> ExecutionResult:
        action_code = self._first_action(plan)
        self._ensure_supported(action_code)
        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.NOT_IMPLEMENTED,
            notes="Would generate alternative text via approved process.",
        )
