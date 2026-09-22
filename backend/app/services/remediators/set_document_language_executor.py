"""Executor that sets the document language metadata."""

from __future__ import annotations

import re
from typing import Optional

from app.ai.semantic_inference import SemanticInferenceClient
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    DocumentNode,
    iter_reading_order,
)
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


# Below this the detector is guessing. The heuristic provider ABSTAINS (empty
# text) unless the evidence is clear — see offline_rules.detect_latin_language,
# which answers 0.6-0.85 when it answers at all — and 0.74-0.90 for a clear
# non-Latin script; the AI providers report their own confidence.
_MIN_LANGUAGE_CONFIDENCE = 0.4
# Whatever a provider answers, only a language tag is ever written.
_BCP47_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$")


class SetDocumentLanguageExecutor(RemediationExecutor):
    """Detect or default the document language and persist it on metadata."""

    supported_actions = [ActionCode.SET_DOCUMENT_LANGUAGE]

    def __init__(self, client: Optional[SemanticInferenceClient] = None) -> None:
        self._client = client or SemanticInferenceClient()

    def execute(
        self, plan: RemediationPlan, tree: Optional[AccessibilityTree] = None
    ) -> ExecutionResult:
        action_code = self._first_action(plan)
        self._ensure_supported(action_code)
        if tree is None:
            return _result(action_code, plan, ExecutionStatus.SKIPPED, "No tree provided.")

        target = _find_document(tree, plan.target_node_id)
        if target is None:
            return _result(action_code, plan, ExecutionStatus.SKIPPED, "Target document not found.")
        if not _has_flag(plan, target):
            return _result(
                action_code,
                plan,
                ExecutionStatus.SKIPPED,
                "Document not flagged as missing language; no changes applied.",
            )

        before = target.metadata.language
        if before:
            return _result(
                action_code,
                plan,
                ExecutionStatus.SKIPPED,
                f"Language already set to {before!r}.",
            )

        sample = _collect_sample_text(target)
        result = self._client.detect_document_language(sample=sample)
        language = (result.text or "").strip().lower()
        # No default, and a confidence floor. This used to fall back to "en"
        # whenever the detector had nothing — so an all-Japanese PDF got
        # /Lang en written into it at heuristic confidence 0.25, reported as
        # SUCCESS, and charged. A wrong language tag is worse than none: a
        # screen reader picks its pronunciation rules from it. Below the bar
        # the honest answer is "a human has to set this", so we SKIP and say
        # so; the document keeps its DOCUMENT_LANGUAGE_MISSING finding.
        if not language or result.confidence < _MIN_LANGUAGE_CONFIDENCE or not _BCP47_RE.match(language):
            return _result(
                action_code,
                plan,
                ExecutionStatus.SKIPPED,
                (
                    "We couldn't tell for sure which language this document is written in, so we "
                    "didn't guess (a wrong language makes screen readers mispronounce every word). "
                    "Left for you to set; nothing was written and you were not charged for it."
                ),
            )
        target.metadata.language = language
        if target.metadata.properties is None:
            target.metadata.properties = {}
        target.metadata.properties["language_provider"] = result.provider
        target.metadata.properties["language_confidence"] = round(result.confidence, 3)

        return _result(
            action_code,
            plan,
            ExecutionStatus.SUCCESS,
            f"Set language to {language!r} via {result.provider} ({result.confidence:.2f}).",
        )


def _find_document(tree: AccessibilityTree, node_id: str) -> Optional[DocumentNode]:
    for node in iter_reading_order(tree.root):
        if node.id == node_id and isinstance(node, DocumentNode):
            return node
    return None


def _has_flag(plan: RemediationPlan, target: DocumentNode) -> bool:
    if plan.flag.code == AccessibilityFlagCode.DOCUMENT_LANGUAGE_MISSING:
        return True
    return any(flag.code == AccessibilityFlagCode.DOCUMENT_LANGUAGE_MISSING for flag in target.accessibility_flags)


def _collect_sample_text(root: DocumentNode) -> str:
    chunks: list[str] = []
    for node in iter_reading_order(root):
        if node.content and node.content.text:
            chunks.append(node.content.text)
        if sum(len(c) for c in chunks) > 1500:
            break
    return " ".join(chunks)


def _result(action_code, plan, status, notes) -> ExecutionResult:
    return ExecutionResult(
        action_code=action_code,
        target_node_id=plan.target_node_id,
        status=status,
        notes=notes,
    )
