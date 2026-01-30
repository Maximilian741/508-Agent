"""Deterministic remediation policy and planning."""

from __future__ import annotations

from enum import Enum
from typing import Iterable, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.accessibility import (
    AccessibilityFlag,
    ActionCode,
    AccessibilityTree,
    RemediationAction,
    iter_reading_order,
)


class BlockReason(str, Enum):
    NO_RECOMMENDED_ACTIONS = "no_recommended_actions"
    ACTIONS_DISALLOWED_BY_POLICY = "actions_disallowed_by_policy"


class RemediationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_auto_actions: bool = True
    allow_ai_actions: bool = False
    require_human_review_for_all: bool = True
    allowed_action_codes: Optional[List[ActionCode]] = None


class RemediationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flag: AccessibilityFlag
    target_node_id: str
    actions: List[RemediationAction] = Field(default_factory=list)
    execution_allowed: bool
    blocked_reason: Optional[BlockReason] = None


def _filter_actions(
    actions: Iterable[RemediationAction], policy: RemediationPolicy
) -> List[RemediationAction]:
    selected: List[RemediationAction] = []
    for action in actions:
        if policy.allowed_action_codes is not None and action.action_code not in policy.allowed_action_codes:
            continue
        if not policy.allow_ai_actions and action.requires_ai:
            continue
        if not policy.allow_auto_actions and action.is_auto_applicable:
            continue
        if policy.require_human_review_for_all and not action.requires_human_review:
            continue
        selected.append(action)
    return selected


def plan_remediations(
    tree: AccessibilityTree, policy: RemediationPolicy
) -> List[RemediationPlan]:
    plans: List[RemediationPlan] = []
    for node in iter_reading_order(tree.root):
        if not node.accessibility_flags:
            continue
        for flag in node.accessibility_flags:
            recommended = flag.recommended_actions()
            if not recommended:
                plans.append(
                    RemediationPlan(
                        flag=flag,
                        target_node_id=node.id,
                        actions=[],
                        execution_allowed=False,
                        blocked_reason=BlockReason.NO_RECOMMENDED_ACTIONS,
                    )
                )
                continue
            filtered = _filter_actions(recommended, policy)
            if not filtered:
                plans.append(
                    RemediationPlan(
                        flag=flag,
                        target_node_id=node.id,
                        actions=[],
                        execution_allowed=False,
                        blocked_reason=BlockReason.ACTIONS_DISALLOWED_BY_POLICY,
                    )
                )
                continue
            plans.append(
                RemediationPlan(
                    flag=flag,
                    target_node_id=node.id,
                    actions=filtered,
                    execution_allowed=True,
                    blocked_reason=None,
                )
            )
    return plans
