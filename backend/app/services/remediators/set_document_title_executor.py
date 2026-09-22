"""Executor for setting document title."""

from __future__ import annotations

from typing import List, Optional, Tuple

from app.ai.offline_rules import title_from_filename, title_from_heading
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    DocumentNode,
    HeadingNode,
    ParagraphNode,
    SectionNode,
    iter_reading_order,
)
from app.analyzers.helpers import is_placeholder_title
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


def _derive_title_verdict(tree: AccessibilityTree, target: DocumentNode) -> Tuple[str, str]:
    """``(title, why_not)``: the best real title, or "" with the reasons.

    Preference order, each gated by :mod:`app.ai.offline_rules`:
    1. The first heading's text (the first H1, else the first heading of any
       level) — unless it is a number or label ("1", "I.") or a section name
       ("Introduction", "Contents"), which name a part, not the document.
    2. The PDF parser's page-1 title line (only present when one line clearly
       stood out), gated the same way.
    3. A humanized filename ("q3-financial_report v2.docx" → "Q3 Financial
       Report") — never a camera, scanner, chat-app or export name
       ("IMG_2041", "DOC-20240912-WA0003", "final_FINAL_v3", "2026-09-12"),
       which used to be written as "Img 2041" / "Doc 20240912 Wa0003".
    """
    why_not: List[str] = []
    first_h1: Optional[str] = None
    first_any: Optional[str] = None
    # A document that opens with a big bold line which is only STYLED as a
    # heading (no Heading style; flagged TEXT_STYLED_AS_HEADING) is showing
    # its title — the Word memo / report pattern. It outranks a later
    # "Background" H1, which names a section.
    top_visual_title: Optional[str] = None
    seen_text = False
    for node in iter_reading_order(tree.root):
        if isinstance(node, (DocumentNode, SectionNode)):
            continue
        text = (node.content.text or "").strip() if node.content else ""
        if not text:
            continue
        if not seen_text and isinstance(node, ParagraphNode) and any(
            f.code == AccessibilityFlagCode.TEXT_STYLED_AS_HEADING for f in node.accessibility_flags
        ):
            top_visual_title = text
        seen_text = True
        if isinstance(node, HeadingNode):
            if node.level == 1 and first_h1 is None:
                first_h1 = text
            if first_any is None:
                first_any = text
    if top_visual_title:
        verdict = title_from_heading(top_visual_title)
        if verdict.ok:
            return verdict.text or "", ""
    heading = first_h1 or first_any
    if heading:
        verdict = title_from_heading(heading)
        if verdict.ok:
            return verdict.text or "", ""
        why_not.append(verdict.reason)

    # PDF: the parser's page-1 largest-font line (see pdf_parser's
    # _title_candidate_from_page). Only present when it clearly stood out.
    cand = (target.metadata.properties or {}).get("title_candidate")
    if isinstance(cand, str) and cand.strip():
        verdict = title_from_heading(cand)
        if verdict.ok:
            return verdict.text or "", ""
        why_not.append(verdict.reason.replace("the first heading", "the largest line on page 1"))

    filename = (target.metadata.properties or {}).get("filename")
    if isinstance(filename, str) and filename.strip():
        verdict = title_from_filename(filename)
        if verdict.ok:
            return verdict.text or "", ""
        why_not.append(verdict.reason)
    return "", "; ".join(why_not)


def _derive_title(tree: AccessibilityTree, target: DocumentNode) -> str:
    """Back-compat: the derived title, or "Untitled Document" (a placeholder
    the executor refuses to write)."""
    title, _why = _derive_title_verdict(tree, target)
    return title or "Untitled Document"


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
        derived, why_not = _derive_title_verdict(tree, target)
        after_title = derived or "Untitled Document"
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
        if not before_set and (not derived or is_placeholder_title(after_title, filename)):
            reason = why_not or "there is no heading, title line or usable file name to name it from"
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes=(
                    f"We did not give this document a title because {reason}. "
                    "Left for you to name; nothing was written and you were not charged for it."
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
