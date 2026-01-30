"""Executor for normalizing heading levels."""

from __future__ import annotations

from typing import Optional

from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    HeadingNode,
    iter_reading_order,
)
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


class NormalizeHeadingLevelExecutor(RemediationExecutor):
    supported_actions = [ActionCode.NORMALIZE_HEADING_LEVEL]

    def execute(self, plan: RemediationPlan, tree: Optional[AccessibilityTree] = None) -> ExecutionResult:
        action_code = self._first_action(plan)
        self._ensure_supported(action_code)
        if tree is None:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="No accessibility tree provided; action not executed.",
            )
        target, previous_heading = _find_target_and_previous_heading(tree, plan.target_node_id)
        if target is None:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Target node not found; no changes applied.",
            )
        if not isinstance(target, HeadingNode):
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Target node is not a heading; no changes applied.",
            )

        flag_codes = {flag.code for flag in target.accessibility_flags}
        flag_code = plan.flag.code
        if flag_code not in (
            AccessibilityFlagCode.HEADING_LEVEL_JUMP,
            AccessibilityFlagCode.SKIPPED_HEADING_LEVEL,
        ) and not (
            AccessibilityFlagCode.HEADING_LEVEL_JUMP in flag_codes
            or AccessibilityFlagCode.SKIPPED_HEADING_LEVEL in flag_codes
        ):
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Heading does not include a heading-level flag; no changes applied.",
            )

        before_level = target.level
        if before_level is None or before_level < 1:
            target.level = 1
            rule = "clamped_to_min"
        elif before_level > 6:
            target.level = 6
            rule = "clamped_to_max"
        elif flag_code in (
            AccessibilityFlagCode.HEADING_LEVEL_JUMP,
            AccessibilityFlagCode.SKIPPED_HEADING_LEVEL,
        ):
            if previous_heading is None:
                target.level = 1
                rule = "normalized_without_previous_heading"
            else:
                target.level = min(previous_heading.level + 1, 6)
                rule = "normalized_relative_to_previous_heading"
        else:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="No normalization rule applicable; no changes applied.",
            )

        if target.level == before_level:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes=f"No change needed; level remains {before_level}. Rule={rule}.",
            )

        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.SUCCESS,
            notes=f"Normalized heading level from {before_level} to {target.level}. Rule={rule}.",
        )


def _find_target_and_previous_heading(
    tree: AccessibilityTree, target_node_id: str
) -> tuple[Optional[HeadingNode], Optional[HeadingNode]]:
    previous_heading: Optional[HeadingNode] = None
    for node in iter_reading_order(tree.root):
        if node.id == target_node_id:
            if isinstance(node, HeadingNode):
                return node, previous_heading
            return None, previous_heading
        if isinstance(node, HeadingNode):
            previous_heading = node
    return None, previous_heading
