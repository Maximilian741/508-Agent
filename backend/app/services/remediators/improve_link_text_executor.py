"""Executor that rewrites non-descriptive link labels."""

from __future__ import annotations

from typing import Optional

from app.ai.semantic_inference import SemanticInferenceClient
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    ContentKind,
    LinkNode,
    NodeContent,
    iter_reading_order,
)
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


GENERIC_LABELS = {
    "click here",
    "here",
    "read more",
    "learn more",
    "more",
    "link",
    "this",
    "click",
    "details",
    "info",
}


class ImproveLinkTextExecutor(RemediationExecutor):
    """Replace generic anchor text with a descriptive label.

    Uses :class:`SemanticInferenceClient` to derive a phrase from the link
    target.  Falls back to a deterministic rewrite (``Visit {host}``) when no
    AI provider is configured.  The executor refuses to rewrite link text that
    is already descriptive.
    """

    supported_actions = [ActionCode.IMPROVE_LINK_TEXT]

    def __init__(self, client: Optional[SemanticInferenceClient] = None) -> None:
        self._client = client or SemanticInferenceClient()

    def execute(
        self, plan: RemediationPlan, tree: Optional[AccessibilityTree] = None
    ) -> ExecutionResult:
        action_code = self._first_action(plan)
        self._ensure_supported(action_code)
        if tree is None:
            return _result(action_code, plan, ExecutionStatus.SKIPPED, "No tree provided.")

        target = _find_link(tree, plan.target_node_id)
        if target is None:
            return _result(action_code, plan, ExecutionStatus.SKIPPED, "Target link not found.")
        if not _has_flag(plan, target):
            return _result(
                action_code,
                plan,
                ExecutionStatus.SKIPPED,
                "Link not flagged as non-descriptive; no changes applied.",
            )

        original = (target.content.text or "").strip() if target.content else ""
        normalized = original.lower().strip(":.,!? ")
        if normalized and normalized not in GENERIC_LABELS:
            return _result(
                action_code,
                plan,
                ExecutionStatus.SKIPPED,
                f"Link text already descriptive: {original!r}.",
            )

        result = self._client.suggest_link_text(text=original, target=target.target or "")
        suggestion = (result.text or "").strip()
        if not suggestion or suggestion.lower() in GENERIC_LABELS:
            return _result(
                action_code,
                plan,
                ExecutionStatus.SKIPPED,
                "Provider did not return a descriptive suggestion; queued for manual review.",
            )

        before = original or "(empty)"
        target.content = NodeContent(kind=ContentKind.TEXT, text=suggestion)
        if target.metadata.properties is None:
            target.metadata.properties = {}
        target.metadata.properties["link_text_provider"] = result.provider
        target.metadata.properties["link_text_confidence"] = round(result.confidence, 3)
        target.metadata.properties["link_text_pending_review"] = result.confidence < 0.6

        return _result(
            action_code,
            plan,
            ExecutionStatus.SUCCESS,
            f"Rewrote link text {before!r} → {suggestion!r} via {result.provider} ({result.confidence:.2f}).",
        )


def _find_link(tree: AccessibilityTree, node_id: str) -> Optional[LinkNode]:
    for node in iter_reading_order(tree.root):
        if node.id == node_id and isinstance(node, LinkNode):
            return node
    return None


def _has_flag(plan: RemediationPlan, target: LinkNode) -> bool:
    if plan.flag.code == AccessibilityFlagCode.LINK_TEXT_NON_DESCRIPTIVE:
        return True
    return any(flag.code == AccessibilityFlagCode.LINK_TEXT_NON_DESCRIPTIVE for flag in target.accessibility_flags)


def _result(action_code, plan, status, notes) -> ExecutionResult:
    return ExecutionResult(
        action_code=action_code,
        target_node_id=plan.target_node_id,
        status=status,
        notes=notes,
    )
