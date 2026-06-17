"""Executor for giving unlabeled form controls an accessible name.

An unlabeled form control (a Word content control with no ``w:alias``) has no
accessible name, so a screen reader announces only its type ("edit", "combo
box") with no idea what to type (WCAG 3.3.2 / 4.1.2). The parser records how
many unlabeled controls it can *confidently* auto-label from adjacent text
(``form_fields_derivable``) — an inline "Name: [__]" prompt or a table label
cell. The rest stay manual (a wrong label is worse than none).

This executor turns the confident subset into an auto-fix. It is deterministic
(no AI): it flags the document for labeling; ``docx_writer`` then re-walks the
content controls with the SAME derivation logic and writes a ``w:alias`` on each
one whose label is unambiguous. Because parser-count and writer use one shared
``_derive_sdt_label``, the count we claim equals what gets written.
"""

from __future__ import annotations

from typing import Optional

from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    DocumentNode,
    iter_reading_order,
)
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


class FillFormFieldLabelsExecutor(RemediationExecutor):
    supported_actions = [ActionCode.FILL_FORM_FIELD_LABELS]

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
        target = _find_document_node(tree, plan.target_node_id)
        if target is None:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Target node not found or is not the document; no changes applied.",
            )
        if not _has_form_field_flag(plan, target):
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Document does not include a form-field flag; no changes applied.",
            )
        props = target.metadata.properties or {}
        try:
            derivable = int(props.get("form_fields_derivable") or 0)
            unlabeled = int(props.get("form_fields_unlabeled") or 0)
        except (TypeError, ValueError):
            derivable = unlabeled = 0
        if derivable <= 0:
            # No control has a confident nearby label — honest skip, the planner's
            # manual-review fallback handles these.
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="No form control has a confident nearby label; left for manual review.",
            )
        if target.metadata.properties is None:
            target.metadata.properties = {}
        target.metadata.properties["apply_form_field_labels"] = True
        remaining = max(0, unlabeled - derivable)
        suffix = f" ({remaining} need manual labels)" if remaining else ""
        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.SUCCESS,
            notes=f"Auto-labeled {derivable} of {unlabeled} unlabeled form control(s){suffix}.",
        )


def _find_document_node(tree: AccessibilityTree, target_node_id: str) -> Optional[DocumentNode]:
    for node in iter_reading_order(tree.root):
        if node.id == target_node_id and isinstance(node, DocumentNode):
            return node
    return None


def _has_form_field_flag(plan: RemediationPlan, target: DocumentNode) -> bool:
    if plan.flag.code == AccessibilityFlagCode.FORM_FIELD_UNLABELED:
        return True
    for flag in target.accessibility_flags:
        if flag.code == AccessibilityFlagCode.FORM_FIELD_UNLABELED:
            return True
    return False
