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
from app.analyzers.helpers import is_placeholder_title
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

    # PDF: the parser's page-1 largest-font line (see pdf_parser's
    # _title_candidate_from_page). Only present when it clearly stood out.
    cand = (target.metadata.properties or {}).get("title_candidate")
    if isinstance(cand, str) and cand.strip():
        return cand.strip()[:200]

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
        filename = (target.metadata.properties or {}).get("filename")
        filename = filename if isinstance(filename, str) else None
        before_set = isinstance(before_title, str) and bool(before_title.strip())
        before_placeholder = before_set and is_placeholder_title(before_title, filename)
        # No title AND nothing better than the placeholder could be derived:
        # SKIP. "Untitled Document" is a string our own DocumentTitleAnalyzer
        # flags; writing it as the /Title (with DisplayDocTitle on, so viewers
        # show it in the title bar) and crediting it as a fix was the exact
        # overclaim the honesty invariant forbids. The document keeps its
        # DOCUMENT_TITLE_MISSING finding and a human names it.
        if not before_set and is_placeholder_title(after_title, filename):
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes=(
                    "No confident title could be derived (no heading, no distinct "
                    "title line, no usable filename); refusing to write a placeholder. "
                    "Set the title manually."
                ),
            )
        # A real, non-placeholder title is left alone.
        if before_set and not before_placeholder:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes=f"Title already set; no changes applied. Title={before_title!r}.",
            )
        # A placeholder title is only replaced if we can derive something better
        # — never swap one junk title for an equivalent one (keeps the score honest).
        if before_placeholder and (
            not after_title
            or after_title.strip().lower() == str(before_title).strip().lower()
            or is_placeholder_title(after_title, filename)
        ):
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes=f"Title {before_title!r} is a placeholder but no better title could be derived.",
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
