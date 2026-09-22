"""Executor for promoting fake (visually-styled) headings into real headings.

The ``TextStyledAsHeadingAnalyzer`` flags plain paragraphs whose presentation
screams "heading" (Word Title/Subtitle styles, or short all-bold >=14pt text)
but which carry no real Heading style — so screen-reader users navigating by
heading never find them. This is the single most common real-world failure:
the title-page / section-header typed as big bold text.

This executor turns that detect-only finding into a genuine auto-fix. It is
deterministic (no AI). It used to make every fake heading a *sibling* of the
heading before it, which in a manual of "Part N" (Heading 1) sections with 500
bold 14pt "N. Procedure N" lines produced 526 Heading 1s and no Heading 2: a
flat outline nobody can navigate by level. The level now comes from the
document's own visual ladder.

How the level is chosen
-----------------------
The parser records, for every real heading and every fake one, what it LOOKS
like (``properties["heading_visual"]``: effective size, whether it is bold,
and any outline cue in the text — "Part 3" / "Chapter 2" outrank plain
numbering, "2.1" is deeper than "2").

1. Rebuild the outline open at this point: every real heading before the
   target plus every fake heading already promoted in this run, kept as a
   stack of (look, level) — a heading closes everything at its level or
   below.
2. Walk that stack from the innermost heading outwards, comparing looks:
   * less prominent than it  -> one level below it (a child);
   * equally prominent       -> the same level (a sibling);
   * more prominent          -> keep walking outwards;
   * nothing outranks it     -> Heading 1.
   Prominence: clearly bigger (>= 2pt) wins; otherwise a size difference and
   a weight difference must agree; then outline cues decide.
3. Never skip a level: the level is capped at previous+1, and it must not
   open a NEW gap before the next heading (floor next-1). If the headings
   around it ALREADY skip (H2 then H4), any level from the previous one down
   keeps that gap exactly as it was, so the look decides — the floor is not
   used to push a line that looks like an H2 underneath the H2 before it.

When the look genuinely does not say (a line one point bigger than its
neighbour heading but not bold, where the neighbour is bold), or when the
look and the no-skip rule disagree (a 20pt line between an H3 and an H4),
the executor DECLINES with a plain-English reason and the line stays in the
manual queue — a guessed level is not clearly better than no level.

The byte-level change is performed by ``docx_writer``, which reads the
``promote_to_heading_level`` property off the paragraph, makes sure the
``Heading N`` style exists, applies it, and keeps the paragraph looking
exactly as it did. The writer confirms each promotion it placed; the pipeline
counts only confirmed ones.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

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

# Sizes within this many points read as "the same size" on the page.
_SAME_SIZE_PT = 0.5
# A difference this large decides prominence on its own, whatever the weight.
_CLEARLY_BIGGER_PT = 2.0

_AMBIGUOUS = "ambiguous"


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
        context = _outline_context(tree, plan.target_node_id)
        if context is None:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Target node not found; no changes applied.",
            )
        target, stack, next_level = context
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

        text = (target.content.text if target.content else "") or ""
        snippet = text.strip()[:60]
        decided = _choose_level(_visual_of(target), stack, next_level)
        if decided[0] is None:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes=f"Left for you: {decided[1]} Text={snippet!r}.",
            )
        level, reason = decided
        level = max(1, min(6, int(level)))

        if target.metadata.properties is None:
            target.metadata.properties = {}
        target.metadata.properties["promote_to_heading_level"] = level

        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.SUCCESS,
            notes=f"Promoted styled text to Heading {level} — {reason} Text={snippet!r}.",
        )


# ---------------------------------------------------------------------------
# Outline reconstruction
# ---------------------------------------------------------------------------

_Entry = Tuple[Optional[Dict[str, Any]], int, str]  # (look, level, text)


def _visual_of(node: Any) -> Optional[Dict[str, Any]]:
    props = (node.metadata.properties if node.metadata else None) or {}
    vis = props.get("heading_visual")
    if not isinstance(vis, dict) or vis.get("size_pt") is None:
        return None
    return vis


def _entry_level(node: Any) -> Optional[int]:
    if isinstance(node, HeadingNode):
        return int(node.level)
    if isinstance(node, ParagraphNode):
        lvl = (node.metadata.properties or {}).get("promote_to_heading_level")
        if lvl:
            try:
                return int(lvl)
            except (TypeError, ValueError):
                return None
    return None


def _outline_context(
    tree: AccessibilityTree, target_node_id: str
) -> Optional[Tuple[Any, List[_Entry], Optional[int]]]:
    """(target, open-outline stack before it, level of the next heading after it)."""
    stack: List[_Entry] = []
    target = None
    next_level: Optional[int] = None
    for node in iter_reading_order(tree.root):
        if target is None:
            if node.id == target_node_id:
                target = node
                continue
            level = _entry_level(node)
            if level is None:
                continue
            while stack and stack[-1][1] >= level:
                stack.pop()
            stack.append((_visual_of(node), level, ((node.content.text if node.content else "") or "").strip()))
        else:
            level = _entry_level(node)
            if level is not None:
                next_level = level
                break
    if target is None:
        return None
    return target, stack, next_level


def _compare(a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]):
    """+1 if ``a`` looks more prominent than ``b``, -1 if less, 0 if the same,
    ``_AMBIGUOUS`` when the signals disagree, None when either is unknown."""
    if a is None or b is None:
        return None
    try:
        ds = float(a.get("size_pt")) - float(b.get("size_pt"))
    except (TypeError, ValueError):
        return None
    if abs(ds) >= _CLEARLY_BIGGER_PT:
        return 1 if ds > 0 else -1
    size_sign = 0 if abs(ds) < _SAME_SIZE_PT else (1 if ds > 0 else -1)
    bold_sign = int(bool(a.get("bold"))) - int(bool(b.get("bold")))
    if size_sign and bold_sign and size_sign != bold_sign:
        return _AMBIGUOUS
    if size_sign:
        return size_sign
    if bold_sign:
        return bold_sign
    # Same size and weight: outline cues in the text. "Part 3" / "Chapter 2"
    # outrank a plain numbered line; "2.1" sits below "2".
    ka, kb = int(a.get("keyword_rank") or 0), int(b.get("keyword_rank") or 0)
    if ka != kb:
        return 1 if ka > kb else -1
    na, nb = int(a.get("number_depth") or 0), int(b.get("number_depth") or 0)
    if na and nb and na != nb:
        return 1 if na < nb else -1
    return 0


def _quote(text: str) -> str:
    t = (text or "").strip()
    return repr(t if len(t) <= 50 else t[:49] + "…")


def _choose_level(
    look: Optional[Dict[str, Any]], stack: List[_Entry], next_level: Optional[int]
) -> Tuple[Optional[int], str]:
    """(level, plain-English reason) — or (None, reason to leave it for a person)."""
    prev_level = stack[-1][1] if stack else None

    if look is None:
        # No measurement of how it looks (not produced by the DOCX parser):
        # the old jump-safe rule, sibling of the previous heading.
        if prev_level is not None:
            level, reason = prev_level, "placed alongside the heading before it."
        else:
            level, reason = 1, "it is the first heading in the document."
    elif not stack:
        level, reason = 1, "it is the first heading in the document."
    else:
        level = None
        reason = ""
        for s_look, s_level, s_text in reversed(stack):
            cmp = _compare(look, s_look)
            if cmp is None:
                level, reason = s_level, f"placed alongside {_quote(s_text)} (Heading {s_level}) above it."
                break
            if cmp == _AMBIGUOUS:
                return None, (
                    f"it is set larger than {_quote(s_text)} (Heading {s_level}) but not in the same "
                    "weight, so its level in the outline is unclear. Choose a heading level for it "
                    "in Word (Home > Styles)."
                )
            if cmp < 0:
                level = s_level + 1
                reason = f"it is set smaller than {_quote(s_text)}, the Heading {s_level} above it."
                if int(look.get("keyword_rank") or 0) < int((s_look or {}).get("keyword_rank") or 0):
                    reason = f"it sits under {_quote(s_text)}, the Heading {s_level} above it."
                break
            if cmp == 0:
                level = s_level
                reason = f"it looks the same as {_quote(s_text)}, the Heading {s_level} above it."
                break
        if level is None:
            level, reason = 1, "it is set larger than every heading above it."

    # Never skip a level: at most one deeper than the heading before it.
    hi = (prev_level + 1) if prev_level is not None else 6
    if level > hi:
        level, reason = hi, reason + f" Capped at Heading {hi} so no level is skipped."

    # ...and never open a NEW gap before the heading after it. The floor is
    # the next heading's level minus one — unless the headings around it
    # ALREADY skip (Heading 2 straight to Heading 4): then any level from the
    # previous one down keeps that gap exactly as it was, so the look decides.
    # Forcing the floor there pushed a line that is visibly LARGER than the
    # Heading 2 before it underneath that heading, a structure the page does
    # not show.
    if next_level is not None:
        lo = max(1, next_level - 1)
        if prev_level is not None:
            lo = min(lo, prev_level)
        if level < lo:
            if look is not None and stack:
                # The look places it higher in the outline than the heading
                # after it allows: any level either skips one or contradicts
                # how the line looks on the page. A person should choose.
                return None, (
                    f"it looks like a Heading {level}, but the heading right after it is "
                    f"Heading {next_level}, so any level we chose would either skip a level "
                    "or misstate how it looks. Choose a heading level for it in Word "
                    "(Home > Styles)."
                )
            level, reason = lo, reason + (
                f" Set to Heading {lo} because the next heading is Heading {next_level}, "
                "so no level is skipped."
            )
    return level, reason


def _has_fake_heading_flag(plan: RemediationPlan, target: ParagraphNode) -> bool:
    if plan.flag.code == AccessibilityFlagCode.TEXT_STYLED_AS_HEADING:
        return True
    for flag in target.accessibility_flags:
        if flag.code == AccessibilityFlagCode.TEXT_STYLED_AS_HEADING:
            return True
    return False
