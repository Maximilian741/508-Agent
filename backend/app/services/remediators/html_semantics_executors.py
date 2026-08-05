"""Executors for the three HTML-only semantic fixes.

Both follow the deterministic, no-AI pattern proven by
``FillFormFieldLabelsExecutor``: the executor only MARKS the document, and
``html_writer`` re-walks the DOM with the SAME iterator the parser counted with,
so the number we claim equals the number written (the honesty invariant).

* ``SET_INPUT_AUTOCOMPLETE`` — WCAG 1.3.5. Only fields whose purpose is
  unambiguous get a token; password/payment-ish and unknown fields are left
  alone (a wrong autocomplete token makes a browser autofill the wrong value,
  which is worse than none).
* ``FIX_POSITIVE_TABINDEX`` — WCAG 2.4.3. ``tabindex="3"`` yanks an element to
  the front of the page's tab sequence; resetting to ``0`` keeps it focusable in
  natural DOM order. The fix is mechanical and always correct.
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


def _find_document_node(tree: AccessibilityTree, target_id: str) -> Optional[DocumentNode]:
    for node in iter_reading_order(tree.root):
        if node.id == target_id and isinstance(node, DocumentNode):
            return node
    return None


def _skip(action_code, plan, notes: str) -> ExecutionResult:
    return ExecutionResult(
        action_code=action_code,
        target_node_id=plan.target_node_id,
        status=ExecutionStatus.SKIPPED,
        notes=notes,
    )


class _RootMarkerExecutor(RemediationExecutor):
    """Shared plumbing: verify the flag, check the count, set the writer marker."""

    flag_code: AccessibilityFlagCode
    count_property: str
    marker_property: str
    noun: str

    def execute(self, plan: RemediationPlan, tree: Optional[AccessibilityTree] = None) -> ExecutionResult:
        action_code = self._first_action(plan)
        self._ensure_supported(action_code)
        if tree is None:
            return _skip(action_code, plan, "No accessibility tree provided; action not executed.")
        target = _find_document_node(tree, plan.target_node_id)
        if target is None:
            return _skip(action_code, plan, "Target node not found or is not the document; no changes applied.")

        has_flag = plan.flag.code == self.flag_code or any(
            f.code == self.flag_code for f in target.accessibility_flags
        )
        if not has_flag:
            return _skip(action_code, plan, "Document does not carry this flag; no changes applied.")

        props = target.metadata.properties or {}
        count = int(props.get(self.count_property) or 0)
        if count <= 0:
            return _skip(action_code, plan, f"No {self.noun} to fix; no changes applied.")

        if target.metadata.properties is None:
            target.metadata.properties = {}
        target.metadata.properties[self.marker_property] = True
        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.SUCCESS,
            notes=f"Marked {count} {self.noun} for correction (applied by the HTML writer).",
        )


class SetInputAutocompleteExecutor(_RootMarkerExecutor):
    supported_actions = [ActionCode.SET_INPUT_AUTOCOMPLETE]
    flag_code = AccessibilityFlagCode.INPUT_AUTOCOMPLETE_MISSING
    count_property = "inputs_missing_autocomplete"
    marker_property = "apply_input_autocomplete"
    noun = "form field(s) with an unambiguous purpose"


class FixPositiveTabindexExecutor(_RootMarkerExecutor):
    supported_actions = [ActionCode.FIX_POSITIVE_TABINDEX]
    flag_code = AccessibilityFlagCode.POSITIVE_TABINDEX
    count_property = "positive_tabindex_count"
    marker_property = "apply_tabindex_reset"
    noun = "element(s) with a positive tabindex"
