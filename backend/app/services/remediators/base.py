"""Remediation executor interfaces and shared result models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Iterable, List, Optional

from pydantic import BaseModel, ConfigDict

from app.models.accessibility import AccessibilityTree, ActionCode
from app.services.remediation_planner import RemediationPlan


class ExecutionStatus(str, Enum):
    SKIPPED = "skipped"
    READY = "ready"
    NOT_IMPLEMENTED = "not_implemented"
    SUCCESS = "success"


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_code: ActionCode
    target_node_id: str
    status: ExecutionStatus
    notes: str


class RemediationExecutor(ABC):
    supported_actions: List[ActionCode] = []

    def can_handle(self, action_code: ActionCode) -> bool:
        return action_code in self.supported_actions

    @abstractmethod
    def execute(self, plan: RemediationPlan, tree: Optional[AccessibilityTree] = None) -> ExecutionResult:
        raise NotImplementedError

    def _ensure_supported(self, action_code: ActionCode) -> None:
        if not self.can_handle(action_code):
            raise ValueError(f"Unsupported action code: {action_code}")

    def _first_action(self, plan: RemediationPlan) -> ActionCode:
        if not plan.actions:
            raise ValueError("Remediation plan has no actions")
        return plan.actions[0].action_code

    def _filter_supported(self, plan: RemediationPlan) -> Iterable[ActionCode]:
        return [action.action_code for action in plan.actions if self.can_handle(action.action_code)]
