"""Executor that fixes low-contrast text by recolouring it to the nearest
AA-passing shade.

The contrast analyzer already computes the exact replacement colour (the nearest
foreground to the original that meets the WCAG AA minimum against the known
background) and stores it on the flagged node as
``metadata.properties["contrast_finding"]["suggested_fg"]``. This executor's job
is purely to *authorise* applying it: it copies that colour into
``metadata.properties["contrast_fix_fg"]``, which the format writer reads and
persists into the output file.

Why a separate marker instead of letting the writer read the suggestion
directly: the suggestion is present on *every* flagged node, but only nodes
whose FIX_CONTRAST action was planned/approved get this executor run — so the
marker is what distinguishes "approved, apply it" from "merely detected". This
keeps the recolour opt-in and never silently changes a colour the user did not
approve.

Persistence is currently wired for HTML (html_writer). DOCX/PPTX writers don't
read the marker yet, so the action is only counted as a real fix for formats
whose writer applies it (see ``_PERSISTED_ACTIONS`` — the honesty invariant).
"""

from __future__ import annotations

from typing import Optional

from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    Node,
    iter_reading_order,
)
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


class FixContrastExecutor(RemediationExecutor):
    """Authorise recolouring low-contrast text to the analyzer's AA-passing colour."""

    supported_actions = [ActionCode.FIX_CONTRAST]

    def execute(
        self, plan: RemediationPlan, tree: Optional[AccessibilityTree] = None
    ) -> ExecutionResult:
        action_code = self._first_action(plan)
        self._ensure_supported(action_code)
        if tree is None:
            return _result(action_code, plan, ExecutionStatus.SKIPPED, "No tree provided.")

        node = _find(tree, plan.target_node_id)
        if node is None:
            return _result(action_code, plan, ExecutionStatus.SKIPPED, "Target node not found.")

        if not _has_flag(node):
            return _result(
                action_code, plan, ExecutionStatus.SKIPPED, "Node is not flagged low-contrast."
            )

        props = node.metadata.properties or {}
        finding = props.get("contrast_finding")
        suggested = finding.get("suggested_fg") if isinstance(finding, dict) else None
        if not suggested:
            # No accessible colour could be computed (e.g. unparseable colours);
            # leave it for manual review rather than guessing.
            return _result(
                action_code,
                plan,
                ExecutionStatus.SKIPPED,
                "No AA-passing colour available; left for manual review.",
            )

        props["contrast_fix_fg"] = str(suggested)
        node.metadata.properties = props
        return _result(
            action_code,
            plan,
            ExecutionStatus.SUCCESS,
            f"Recolour text to #{suggested} to meet WCAG AA contrast.",
        )


def _find(tree: AccessibilityTree, node_id: str) -> Optional[Node]:
    for node in iter_reading_order(tree.root):
        if node.id == node_id:
            return node
    return None


def _has_flag(node: Node) -> bool:
    return any(
        f.code == AccessibilityFlagCode.LOW_CONTRAST_TEXT for f in node.accessibility_flags
    )


def _result(action_code, plan, status, notes) -> ExecutionResult:
    return ExecutionResult(
        action_code=action_code,
        target_node_id=plan.target_node_id,
        status=status,
        notes=notes,
    )
