"""Executor for setting document title."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    DocumentNode,
    HeadingNode,
    iter_reading_order,
)
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


def _derive_title(tree: AccessibilityTree, target: DocumentNode) -> str:
    """Best real title we can derive — never a bare placebo when avoidable.

    Preference order:
    1. The first heading's text (H1 first, else the first heading of any
       level) — almost always the document's actual title.
    2. A humanized filename stem ("q3-financial_report v2" → "Q3 Financial
       Report V2").
    3. "Untitled Document" as the last resort.
    """
    first_any: Optional[str] = None
    for node in iter_reading_order(tree.root):
        if isinstance(node, HeadingNode):
            text = (node.content.text or "").strip() if node.content else ""
            if not text:
                continue
            if node.level == 1:
                return text[:200]
            if first_any is None:
                first_any = text
    if first_any:
        return first_any[:200]

    filename = (target.metadata.properties or {}).get("filename")
    if isinstance(filename, str) and filename.strip():
        stem = Path(filename).stem
        words = re.sub(r"[_\-\.]+", " ", stem).strip()
        words = re.sub(r"\s+", " ", words)
        if words:
            return words.title()[:200]
    return "Untitled Document"


class SetDocumentTitleExecutor(RemediationExecutor):
    supported_actions = [ActionCode.SET_DOCUMENT_TITLE]

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
                notes="Target node not found; no changes applied.",
            )
        if not isinstance(target, DocumentNode):
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Target node is not a document; no changes applied.",
            )
        if not _has_title_missing_flag(plan, target):
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Document does not include a title-missing flag; no changes applied.",
            )
        before_title = target.metadata.properties.get("title")
        after_title = _derive_title(tree, target)
        if isinstance(before_title, str) and before_title.strip():
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes=f"Title already set; no changes applied. Title={before_title!r}.",
            )
        if target.metadata.properties is None:
            target.metadata.properties = {}
        target.metadata.properties["title"] = after_title
        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.SUCCESS,
            notes=f"Set document title from {before_title!r} to {after_title!r}.",
        )


def _find_document_node(tree: AccessibilityTree, target_node_id: str) -> Optional[DocumentNode]:
    for node in iter_reading_order(tree.root):
        if node.id == target_node_id and isinstance(node, DocumentNode):
            return node
    return None


def _has_title_missing_flag(plan: RemediationPlan, target: DocumentNode) -> bool:
    if plan.flag.code == AccessibilityFlagCode.DOCUMENT_TITLE_MISSING:
        return True
    for flag in target.accessibility_flags:
        if flag.code == AccessibilityFlagCode.DOCUMENT_TITLE_MISSING:
            return True
    return False
