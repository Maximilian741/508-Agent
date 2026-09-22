"""Executor for giving untitled slides a real title.

A slide with no title is the #1 PowerPoint accessibility failure: screen-reader
users navigate a deck by pulling up the list of slide titles, and an untitled
slide is invisible there (WCAG 2.4.2 / 1.3.1). The parser marks each untitled
slide's ``SectionNode`` with ``properties["missing_title"]``.

This executor turns that finding into an auto-fix ONLY when the slide already
shows its own title as ordinary text — deterministic, no AI. It picks the
slide's *topmost* title-like text shape (the parser records each shape's
``order_hint`` = (top, left)), which is almost always the de-facto title the
author typed into a text box instead of the title placeholder. It never
invents words:

  * only text SHAPES are candidates — never a table cell (a data table's first
    cell "North" is not a title), a link, or an image;
  * pure dates and page numbers are skipped so a footer can't hijack the title,
    and so is a typed list;
  * text repeated on several slides (a running "ACME Corp" / "Confidential"
    banner) is boilerplate, not this slide's title.

When no shape qualifies the slide is left for a person: a fabricated "Slide 7"
is what a screen reader already announces for an untitled slide, so writing it
would be charged for and fix nothing.

The chosen text goes on the section as ``set_slide_title`` and the shape's node
id as ``set_slide_title_source``. ``pptx_writer`` then makes that text the
slide's title WITHOUT adding a visible duplicate: it promotes the source shape
itself to the title placeholder (its look and position frozen), or — when the
shape can't be promoted (inside a group, several paragraphs) — adds a title
placeholder positioned OFF the slide, which is how PowerPoint's own guidance
hides a title. Re-parsing the output then sees a titled slide and the flag
clears.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    ParagraphNode,
    SectionNode,
    iter_reading_order,
)
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor

_MAX_TITLE_LEN = 120
# A title is a line, not a paragraph of body copy.
_MAX_TITLE_WORDS = 20
# Text on at least this many slides is a running header/footer, not a title.
_BOILERPLATE_MIN_SLIDES = 3

# A title candidate that is *only* a page number or a bare date is almost
# certainly a footer/header artifact, not the slide's title.
_PAGENUM_RE = re.compile(r"^(?:page|slide)?\s*\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?$", re.IGNORECASE)
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


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _norm(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _is_title_like(text: str) -> bool:
    t = text.strip()
    if not t or _PAGENUM_RE.match(t) or _DATE_RE.match(t):
        return False
    if not any(c.isalpha() for c in t):
        return False
    words = len(t.split())
    if t.endswith(".") and words > 8:
        return False  # a sentence of body copy, not a heading
    return words <= _MAX_TITLE_WORDS


def _truncate_title(text: str) -> str:
    first_line = _first_line(text)
    if len(first_line) <= _MAX_TITLE_LEN:
        return first_line
    head = first_line[:_MAX_TITLE_LEN].rsplit(" ", 1)[0].rstrip()
    if not head:  # a single very long word
        head = first_line[:_MAX_TITLE_LEN].rstrip()
    return head + "…"


def _slide_text_counts(tree: AccessibilityTree) -> Dict[str, int]:
    """How many slides each (normalised) first line of a text shape is on."""
    counts: Dict[str, int] = {}
    for section in tree.root.children:
        if not isinstance(section, SectionNode):
            continue
        seen = set()
        for node in iter_reading_order(section):
            if isinstance(node, ParagraphNode):
                key = _norm(_first_line((node.content.text if node.content else "") or ""))
                if key:
                    seen.add(key)
        for key in seen:
            counts[key] = counts.get(key, 0) + 1
    return counts


def _pick_title_source(
    section: SectionNode, boilerplate: Dict[str, int]
) -> Optional[Tuple[ParagraphNode, str]]:
    """The slide's own title text and the shape it is on, or None.

    The topmost (by geometry, not XML order) text shape whose first line reads
    as a title. Never a table cell/link/image, never a date, page number,
    typed list, or text repeated across the deck.
    """
    candidates: List[Tuple[Tuple[float, float], ParagraphNode, str]] = []
    for node in iter_reading_order(section):
        if not isinstance(node, ParagraphNode):
            continue
        props = node.metadata.properties or {}
        if props.get("fake_list_run_ids"):
            continue  # a typed "- item" list is body content
        line = _first_line((node.content.text if node.content else "") or "")
        if not _is_title_like(line):
            continue
        if boilerplate.get(_norm(line), 0) >= _BOILERPLATE_MIN_SLIDES:
            continue
        candidates.append((_order_key(node), node, line))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    _key, node, line = candidates[0]
    return node, _truncate_title(line)


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
        if target.metadata.properties is None:
            target.metadata.properties = {}
        slide_no = target.metadata.properties.get("slide_number")
        picked = _pick_title_source(target, _slide_text_counts(tree))
        if picked is None:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes=(
                    f"Slide {slide_no} has no text that reads as a title (only a table, "
                    "pictures, dates/page numbers or text repeated on other slides), so "
                    "we did not make one up. Add a title in PowerPoint (Home > Layout, or "
                    "type into the title box). Left for manual review; you were not "
                    "charged for it."
                ),
            )
        source, title = picked
        target.metadata.properties["set_slide_title"] = title
        target.metadata.properties["set_slide_title_source"] = source.id
        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.SUCCESS,
            notes=f"Made the slide's own text {title!r} the title of slide {slide_no}.",
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
