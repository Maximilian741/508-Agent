"""Remediation API routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.analyzers.registry import get_default_analyzers, run_analyzers
from app.api import state
from app.models.accessibility import AccessibilityTree, ActionCode
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus
from app.services.remediators.registry import execute_plans
from app.repositories.factory import get_repository

router = APIRouter()
REPO = get_repository()


class RemediateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issueId: Optional[str] = None
    targetNodeId: str
    actionCode: str


class RemediateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actionCode: str
    targetNodeId: str
    status: str
    notes: str


class RemediateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: List[RemediateResult]


def _find_node(tree: AccessibilityTree, node_id: str) -> Optional[Any]:
    stack = [tree.root]
    while stack:
        node = stack.pop(0)
        if node.id == node_id:
            return node
        for child in getattr(node, "children", []) or []:
            stack.append(child)
    return None


def _to_result(result: ExecutionResult) -> RemediateResult:
    return RemediateResult(
        actionCode=result.action_code.value,
        targetNodeId=result.target_node_id,
        status=result.status.value,
        notes=result.notes,
    )


def _manual_review_item(
    issue_id: Optional[str],
    target_node_id: str,
    reason: str,
    notes: str,
) -> Dict[str, Any]:
    return {
        "id": f"mr-{target_node_id}-{int(datetime.utcnow().timestamp())}",
        "issueId": issue_id or "unknown",
        "targetNodeId": target_node_id,
        "reason": reason,
        "notes": notes,
        "createdAt": datetime.utcnow().isoformat() + "Z",
    }


@router.post("/remediate", response_model=RemediateResponse)
async def remediate(request: RemediateRequest) -> RemediateResponse:
    print(f"[api] POST /remediate targetNodeId={request.targetNodeId} actionCode={request.actionCode}")
    if state.last_tree is None:
        return RemediateResponse(
            results=[
                RemediateResult(
                    actionCode=request.actionCode,
                    targetNodeId=request.targetNodeId,
                    status=ExecutionStatus.NOT_IMPLEMENTED.value,
                    notes="No scan data available for remediation.",
                )
            ]
        )

    tree = state.last_tree
    run_analyzers(tree, get_default_analyzers())

    try:
        action_code = ActionCode(request.actionCode)
    except ValueError:
        return RemediateResponse(
            results=[
                RemediateResult(
                    actionCode=request.actionCode,
                    targetNodeId=request.targetNodeId,
                    status=ExecutionStatus.NOT_IMPLEMENTED.value,
                    notes="Requested action code is not supported.",
                )
            ]
        )

    target_node = _find_node(tree, request.targetNodeId)
    if target_node is None:
        return RemediateResponse(
            results=[
                RemediateResult(
                    actionCode=action_code.value,
                    targetNodeId=request.targetNodeId,
                    status=ExecutionStatus.NOT_IMPLEMENTED.value,
                    notes="Target node not found in last scan.",
                )
            ]
        )

    plan: Optional[RemediationPlan] = None
    for flag in target_node.accessibility_flags:
        for action in flag.recommended_actions():
            if action.action_code == action_code:
                plan = RemediationPlan(
                    flag=flag,
                    target_node_id=target_node.id,
                    actions=[action],
                    execution_allowed=True,
                )
                break
        if plan is not None:
            break

    if plan is None:
        return RemediateResponse(
            results=[
                RemediateResult(
                    actionCode=action_code.value,
                    targetNodeId=request.targetNodeId,
                    status=ExecutionStatus.NOT_IMPLEMENTED.value,
                    notes="Requested action not available for target node.",
                )
            ]
        )

    results = execute_plans(tree, [plan])
    api_results = [_to_result(result) for result in results]

    for result in api_results:
        if result.status != ExecutionStatus.SUCCESS.value:
            item = _manual_review_item(
                request.issueId,
                result.targetNodeId,
                "Execution did not complete automatically.",
                result.notes,
            )
            state.manual_review_queue.append(item)
            REPO.add_manual_review_items(state.last_document_id or "doc-1", [item])

    return RemediateResponse(results=api_results)
