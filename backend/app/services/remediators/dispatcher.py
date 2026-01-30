"""Deterministic remediation dispatcher."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

from app.models.accessibility import ActionCode, RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor
from app.services.remediators.registry import get_default_executors


@dataclass(frozen=True)
class _Candidate:
    action_code: ActionCode
    is_auto_applicable: bool
    requires_ai: bool
    requires_human_review: bool


class RemediationDispatcher:
    def __init__(self, executors: Optional[Iterable[RemediationExecutor]] = None) -> None:
        self._executors = list(executors) if executors is not None else get_default_executors()

    def dispatch(self, plans: List[RemediationPlan]) -> List[ExecutionResult]:
        results: List[ExecutionResult] = []
        for plan in plans:
            results.append(self._dispatch_plan(plan))
        return results

    def _dispatch_plan(self, plan: RemediationPlan) -> ExecutionResult:
        if not plan.actions:
            return self._fallback_result(
                plan,
                "No recommended actions; falling back to manual review.",
            )
        if not plan.execution_allowed:
            return self._fallback_result(
                plan,
                "Execution blocked by policy; falling back to manual review.",
            )

        candidates = [
            _Candidate(
                action_code=action.action_code,
                is_auto_applicable=action.is_auto_applicable,
                requires_ai=action.requires_ai,
                requires_human_review=action.requires_human_review,
            )
            for action in plan.actions
        ]
        selected = self._select_action(candidates)
        executor = self._resolve_executor(selected.action_code)
        if executor is None:
            return self._fallback_result(
                plan,
                "No executor registered for selected action; falling back to manual review.",
            )
        return ExecutionResult(
            action_code=selected.action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.READY,
            notes=f"Selected {selected.action_code} using {executor.__class__.__name__}.",
        )

    def _select_action(self, candidates: List[_Candidate]) -> _Candidate:
        def sort_key(candidate: _Candidate) -> tuple[int, int, int, str]:
            return (
                0 if candidate.is_auto_applicable else 1,
                0 if not candidate.requires_ai else 1,
                0 if not candidate.requires_human_review else 1,
                candidate.action_code.value,
            )

        return sorted(candidates, key=sort_key)[0]

    def _resolve_executor(self, action_code: ActionCode) -> Optional[RemediationExecutor]:
        for executor in self._executors:
            if executor.can_handle(action_code):
                return executor
        return None

    def _fallback_result(self, plan: RemediationPlan, reason: str) -> ExecutionResult:
        executor = self._resolve_executor(ActionCode.FLAG_FOR_MANUAL_REVIEW)
        executor_name = executor.__class__.__name__ if executor else "None"
        return ExecutionResult(
            action_code=ActionCode.FLAG_FOR_MANUAL_REVIEW,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.SKIPPED,
            notes=f"{reason} Executor={executor_name}.",
        )
