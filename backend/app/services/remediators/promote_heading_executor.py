"""Executor for promoting fake (visually-styled) headings into real headings.

The ``TextStyledAsHeadingAnalyzer`` flags plain paragraphs whose presentation
screams "heading" (Word Title/Subtitle styles, or short all-bold >=14pt text)
but which carry no real Heading style — so screen-reader users navigating by
heading never find them. This is the single most common real-world failure:
the title-page / section-header typed as big bold text.

This executor turns that detect-only finding into a genuine auto-fix. It is
deterministic (no AI): it marks the paragraph for promotion and picks a heading
level that provably never *introduces* a new heading-level jump:

    level = previous_heading.level                if a heading precedes it
          = max(1, next_heading.level - 1)         elif a heading follows it
          = 1                                       otherwise (no headings)

Why this is jump-safe:
  * Sibling of the previous heading: inserting a heading at the same level as
    its predecessor can never create a jump — the pair (prev, promoted) is a
    same-level step, and the pair (promoted, next) was already (prev, next)
    before, so no NEW jump appears.
  * No previous but a following heading at level L: the promoted heading becomes
    the document's first heading. Choosing ``max(1, L-1)`` guarantees the step
    to the following heading is at most +1 (no jump), while still preferring the
    shallowest level — a normal deck whose next heading is H1/H2 yields ``H1``.
  * No headings at all: ``H1`` is the natural choice for a document's title.

(Compare ``normalize_heading_level_executor`` which uses ``previous + 1`` —
that's *child*-of-previous, the right call when fixing an existing heading's
level; for promoting fresh text, *sibling*-of-previous is safer, so the two
deliberately differ.)

The actual byte-level change is performed by ``docx_writer`` which reads the
``promote_to_heading_level`` property off the paragraph and sets
``w:pStyle = "Heading {level}"`` on the source ``<w:p>`` (re-using the same
machinery that persists real headings). Re-analysis of the output then sees a
real ``HeadingNode`` and the flag clears.
"""

from __future__ import annotations

from typing import Optional

from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    HeadingNode,
    ParagraphNode,
    iter_reading_order,
)
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


class PromoteHeadingExecutor(RemediationExecutor):
    supported_actions = [ActionCode.PROMOTE_HEADING]

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
        target, previous_heading, next_heading = _find_target_with_neighbors(tree, plan.target_node_id)
        if target is None:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Target node not found; no changes applied.",
            )
        if not isinstance(target, ParagraphNode):
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Target node is not a paragraph; no changes applied.",
            )
        if not _has_fake_heading_flag(plan, target):
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Paragraph is not flagged as a styled-but-fake heading; no changes applied.",
            )

        # Pick a hierarchy-safe level that never introduces a new jump (see the
        # module docstring for the proof): sibling of the nearest preceding
        # heading; else one shallower than the following heading; else H1.
        if previous_heading is not None:
            level = previous_heading.level
        elif next_heading is not None:
            level = max(1, next_heading.level - 1)
        else:
            level = 1
        level = max(1, min(6, int(level)))

        if target.metadata.properties is None:
            target.metadata.properties = {}
        target.metadata.properties["promote_to_heading_level"] = level

        text = (target.content.text if target.content else "") or ""
        snippet = text.strip()[:60]
        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.SUCCESS,
            notes=f"Promoted styled text to Heading {level}. Text={snippet!r}.",
        )


def _find_target_with_neighbors(
    tree: AccessibilityTree, target_node_id: str
) -> tuple[Optional[ParagraphNode], Optional[HeadingNode], Optional[HeadingNode]]:
    """Return (target paragraph, nearest preceding heading, nearest following heading)."""
    order = list(iter_reading_order(tree.root))
    target_index: Optional[int] = None
    target: Optional[ParagraphNode] = None
    previous_heading: Optional[HeadingNode] = None
    for i, node in enumerate(order):
        if node.id == target_node_id:
            if isinstance(node, ParagraphNode):
                target, target_index = node, i
            else:
                return None, previous_heading, None
            break
        if isinstance(node, HeadingNode):
            previous_heading = node
    if target is None or target_index is None:
        return None, previous_heading, None
    next_heading: Optional[HeadingNode] = None
    for node in order[target_index + 1:]:
        if isinstance(node, HeadingNode):
            next_heading = node
            break
    return target, previous_heading, next_heading


def _has_fake_heading_flag(plan: RemediationPlan, target: ParagraphNode) -> bool:
    if plan.flag.code == AccessibilityFlagCode.TEXT_STYLED_AS_HEADING:
        return True
    for flag in target.accessibility_flags:
        if flag.code == AccessibilityFlagCode.TEXT_STYLED_AS_HEADING:
            return True
    return False
