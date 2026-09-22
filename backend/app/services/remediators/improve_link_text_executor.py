"""Executor that rewrites non-descriptive link labels."""

from __future__ import annotations

from typing import Optional

from app.ai.offline_rules import vet_link_text
from app.ai.semantic_inference import SemanticInferenceClient, refusal_reason
from app.analyzers.link_analyzer import (
    NON_DESCRIPTIVE_LINK_TEXT,
    _looks_like_url,
    _normalize_text,
)
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


# Single source of truth: the same phrase set the ANALYZER flags with. The
# executor previously kept its own narrower 11-phrase list, so bare-URL link
# text (and several generic phrases) were flagged by detection but then
# refused by the approved fix — a customer-visible detect/fix disagreement.
GENERIC_LABELS = NON_DESCRIPTIVE_LINK_TEXT


def _is_non_descriptive(text: str) -> bool:
    """Mirror LinkTextAnalyzer's predicate exactly (phrases OR bare URL)."""
    normalized = _normalize_text(text or "")
    return (not normalized) or normalized in NON_DESCRIPTIVE_LINK_TEXT or _looks_like_url(text or "")


class ImproveLinkTextExecutor(RemediationExecutor):
    """Replace generic anchor text with a descriptive label.

    Uses :class:`SemanticInferenceClient` to derive a phrase from the link
    target. Without an AI provider the only source of words is the address
    itself ("/docs/benefits-guide.pdf" -> "Benefits guide (PDF)"); anchors,
    email/phone links, home pages and code-like slugs are refused and left for
    a person. Every suggestion, from any provider, passes
    :func:`app.ai.offline_rules.vet_link_text`. The executor never rewrites
    link text that is already descriptive.
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
        if not _is_non_descriptive(original):
            # The text was edited since the flag was attached and now reads
            # fine — don't clobber a human's words.
            return _result(
                action_code,
                plan,
                ExecutionStatus.SKIPPED,
                f"Link text already descriptive: {original!r}.",
            )

        link_target = (target.target or "").strip()
        # Without an AI provider this is app.ai.offline_rules.link_text_from_target:
        # a link whose address carries no words (an anchor, an email, a phone
        # number, a home page, "f1040") comes back empty with the reason.
        result = self._client.suggest_link_text(text=original, target=link_target)
        suggestion = (result.text or "").strip()
        if not suggestion:
            return _refused(
                action_code,
                plan,
                refusal_reason(result) or "We could not produce a better name for this link.",
            )
        # Final gate for ANY provider. "Read more about click here", "Read more
        # about #section-2", "Read more about hr@example.com" and "Visit
        # example.com" all used to be written into customers' files and
        # charged; none of them tells a screen-reader user more than the
        # original did.
        problem = vet_link_text(suggestion, original, link_target)
        if problem:
            return _refused(
                action_code,
                plan,
                f"We did not rename this link because {problem}. A person needs to write what it links to.",
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


def _refused(action_code, plan, reason: str) -> ExecutionResult:
    reason = (reason or "").strip()
    if reason and not reason.endswith((".", "!", "?")):
        reason += "."
    return _result(
        action_code,
        plan,
        ExecutionStatus.SKIPPED,
        f"{reason} Left for you to name; nothing was written and you were not charged for it.".strip(),
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
