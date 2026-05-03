"""Executor that re-orders ambiguous siblings by inferred page-position.

For PDF/PPTX content the parsers attach ``metadata.properties['order_hint']``
(a tuple of ``(top, left)`` floats) to nodes whose source coordinates were
captured.  When a sibling group's order_hints are all populated and they
disagree with the existing tree order, the executor reorders them.

When hints are missing or fully consistent, the executor records a
no-op success with a diagnostic note so downstream UIs can show "no
change required" instead of an error.
"""

from __future__ import annotations

from typing import List, Optional

from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    iter_reading_order,
)
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


class ResolveReadingOrderExecutor(RemediationExecutor):
    supported_actions = [ActionCode.RESOLVE_READING_ORDER]

    def execute(
        self, plan: RemediationPlan, tree: Optional[AccessibilityTree] = None
    ) -> ExecutionResult:
        action_code = self._first_action(plan)
        self._ensure_supported(action_code)
        if tree is None:
            return _result(action_code, plan, ExecutionStatus.SKIPPED, "No tree provided.")

        target = _find_node(tree, plan.target_node_id)
        if target is None:
            return _result(action_code, plan, ExecutionStatus.SKIPPED, "Target node not found.")
        if not _has_flag(plan, target):
            return _result(
                action_code,
                plan,
                ExecutionStatus.SKIPPED,
                "Target not flagged with reading-order ambiguity; no changes applied.",
            )

        reordered = 0
        for node in iter_reading_order(target):
            if not getattr(node, "children", None):
                continue
            ordered, changed = _reorder_siblings(node.children)
            if changed:
                node.children = list(ordered)
                reordered += 1

        if reordered == 0:
            return _result(
                action_code,
                plan,
                ExecutionStatus.SUCCESS,
                "Reading order already deterministic; no reorderings necessary.",
            )
        return _result(
            action_code,
            plan,
            ExecutionStatus.SUCCESS,
            f"Reordered children of {reordered} node(s) using positional hints.",
        )


def _find_node(tree, node_id):
    for node in iter_reading_order(tree.root):
        if node.id == node_id:
            return node
    return None


def _has_flag(plan: RemediationPlan, target) -> bool:
    if plan.flag.code == AccessibilityFlagCode.READING_ORDER_AMBIGUOUS:
        return True
    return any(flag.code == AccessibilityFlagCode.READING_ORDER_AMBIGUOUS for flag in target.accessibility_flags)


def _reorder_siblings(children: List) -> tuple[List, bool]:
    """Return ``(reordered, changed)``.

    Children with a positional hint are sorted by ``(top, left)``; children
    without hints retain their relative position interleaved at their original
    index so static content (e.g. document-level sections) is preserved.
    """

    indexed = list(enumerate(children))
    keyed = [(i, c, _order_hint(c)) for i, c in indexed]
    if not any(hint is not None for _, _, hint in keyed):
        return children, False

    def sort_key(item):
        index, _node, hint = item
        if hint is None:
            return (1, index, 0.0, 0.0)
        return (0, index, *hint)

    sorted_keyed = sorted(keyed, key=sort_key)
    reordered = [item[1] for item in sorted_keyed]
    if [c.id for c in reordered] == [c.id for c in children]:
        return children, False
    return reordered, True


def _order_hint(node) -> Optional[tuple[float, float]]:
    properties = getattr(node.metadata, "properties", None) or {}
    raw = properties.get("order_hint")
    if not raw:
        return None
    try:
        top, left = raw  # tuple/list of two floats
        return float(top), float(left)
    except Exception:
        return None


def _result(action_code, plan, status, notes) -> ExecutionResult:
    return ExecutionResult(
        action_code=action_code,
        target_node_id=plan.target_node_id,
        status=status,
        notes=notes,
    )
