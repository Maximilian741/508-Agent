"""Executor that sets the document language metadata."""

from __future__ import annotations

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
        if not language:
            language = "en"
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
