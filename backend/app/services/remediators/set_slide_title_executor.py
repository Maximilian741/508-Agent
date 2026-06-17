"""Executor for giving untitled slides a real title.

A slide with no title is the #1 PowerPoint accessibility failure: screen-reader
users navigate a deck by pulling up the list of slide titles, and an untitled
slide is invisible there (WCAG 2.4.2 / 1.3.1). The parser marks each untitled
slide's ``SectionNode`` with ``properties["missing_title"]``.

This executor turns that detect-only finding into an auto-fix. It is
deterministic (no AI): it derives a title from the slide's *topmost* text — the
shape highest on the slide by geometry (the parser records each shape's
``order_hint`` = (top, left)), which is almost always the de-facto title the
author typed as plain text instead of into the title placeholder. Pure dates and
page numbers are skipped so a footer can't hijack the title, and over-long lines
are truncated on a word boundary. It falls back to ``"Slide N"`` only when the
slide has no usable text. The chosen title is stashed on the section as
``set_slide_title``; ``pptx_writer`` then inserts a real *title placeholder*
(cloned from the slide layout, or built from scratch) carrying that text, so the
slide enters the title-navigation list. Re-parsing the output then sees a titled
slide and the flag clears.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    SectionNode,
    iter_reading_order,
)
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor

_MAX_TITLE_LEN = 120

# A title candidate that is *only* a page number or a bare date is almost
# certainly a footer/header artifact, not the slide's title.
_PAGENUM_RE = re.compile(r"^(?:page|slide)?\s*\d{1,4}$", re.IGNORECASE)
_DATE_RE = re.compile(
    r"^(?:"
    r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}"  # 6/16/2026, 16-06-26
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s*\d{0,4}"  # June 16, 2026
    r"|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s*\d{0,4}"  # 16 June 2026
    r")$",
    re.IGNORECASE,
)


def _order_key(node) -> Tuple[float, float]:
    """Sort key from the parser's recorded geometry; unknown sorts last."""
    hint = (node.metadata.properties or {}).get("order_hint")
    if isinstance(hint, (list, tuple)) and len(hint) >= 2:
        try:
            return (float(hint[0]), float(hint[1]))
        except (TypeError, ValueError):
            pass
    return (float("inf"), float("inf"))


def _is_title_like(text: str) -> bool:
    t = text.strip()
    return bool(t) and not _PAGENUM_RE.match(t) and not _DATE_RE.match(t)


def _truncate_title(text: str) -> str:
    first_line = text.splitlines()[0].strip()
    if len(first_line) <= _MAX_TITLE_LEN:
        return first_line
    head = first_line[:_MAX_TITLE_LEN].rsplit(" ", 1)[0].rstrip()
    if not head:  # a single very long word
        head = first_line[:_MAX_TITLE_LEN].rstrip()
    return head + "…"


def _derive_slide_title(section: SectionNode) -> str:
    """Best title we can derive from the slide's content (never empty).

    Picks the topmost text on the slide (by geometry, not XML order), skipping
    pure dates/page numbers so footers can't hijack the title.
    """
    slide_no = (section.metadata.properties or {}).get("slide_number")
    candidates: List[Tuple[Tuple[float, float], str]] = []
    for node in iter_reading_order(section):
        if node is section:
            # The section's own content text is the placeholder "Slide N".
            continue
        text = ((node.content.text if node.content else "") or "").strip()
        if text:
            candidates.append((_order_key(node), text))
    candidates.sort(key=lambda c: c[0])
    for _key, text in candidates:
        if _is_title_like(text):
            return _truncate_title(text)
    # Nothing title-like (e.g. the only text is a date) — use the topmost text
    # rather than a generic placeholder, if any.
    if candidates:
        return _truncate_title(candidates[0][1])
    return f"Slide {slide_no}" if slide_no else "Slide"


class SetSlideTitleExecutor(RemediationExecutor):
    supported_actions = [ActionCode.SET_SLIDE_TITLE]

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
        target = _find_section(tree, plan.target_node_id)
        if target is None:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Target node not found or is not a slide section; no changes applied.",
            )
        if not _has_missing_title_flag(plan, target):
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Slide is not flagged as missing a title; no changes applied.",
            )
        title = _derive_slide_title(target)
        if target.metadata.properties is None:
            target.metadata.properties = {}
        target.metadata.properties["set_slide_title"] = title
        slide_no = target.metadata.properties.get("slide_number")
        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.SUCCESS,
            notes=f"Set title of slide {slide_no} to {title!r}.",
        )


def _find_section(tree: AccessibilityTree, target_node_id: str) -> Optional[SectionNode]:
    for node in iter_reading_order(tree.root):
        if node.id == target_node_id and isinstance(node, SectionNode):
            return node
    return None


def _has_missing_title_flag(plan: RemediationPlan, target: SectionNode) -> bool:
    if plan.flag.code == AccessibilityFlagCode.SLIDE_TITLE_MISSING:
        return True
    for flag in target.accessibility_flags:
        if flag.code == AccessibilityFlagCode.SLIDE_TITLE_MISSING:
            return True
    return False
