"""Traversal and flag helpers."""

from __future__ import annotations

import re
from typing import Iterable, Optional

from app.models.accessibility import (
    AccessibilityFlag,
    AccessibilityFlagCode,
    AccessibilityTree,
    Node,
    iter_reading_order,
)


def iter_nodes(tree: AccessibilityTree) -> Iterable[Node]:
    return iter_reading_order(tree.root)


# Exact (case-insensitive) titles that authoring tools leave as defaults — a
# screen reader announcing "Document1" is no better off than with no title.
_GENERIC_TITLES = {
    "untitled",
    "untitled document",
    "untitled presentation",
    "untitled spreadsheet",
    "untitled form",
    "title",
    "document",
    "presentation",
    "powerpoint presentation",
    "microsoft word document",
    "microsoft powerpoint presentation",
    "microsoft excel worksheet",
    "new microsoft word document",
    "new microsoft powerpoint presentation",
    "new microsoft excel worksheet",
    "new document",
    "new presentation",
    "new spreadsheet",
    "blank",
    "blank document",
    "blank presentation",
    "blank spreadsheet",
    "slide 1",
    "spreadsheet",
    "workbook",
    "no title",
    "document title",
    "presentation title",
    # Placeholder prompts authoring tools pre-fill into the title box.
    "click to add title",
    "add title",
    "title here",
    "your title here",
    "type a title",
    "enter title here",
    "google docs",
    "google slides",
    "google sheets",
}
_NUMBERED_DEFAULT_RE = re.compile(
    r"^(document|presentation|doc|slide|untitled|book|workbook|sheet|spreadsheet|"
    r"drawing|form|page|copy)\s*\d+$"
)
_PRINTED_FROM_RE = re.compile(r"^microsoft (word|powerpoint|excel)\s*[-–]\s*")


def is_placeholder_title(title: Optional[str], filename: Optional[str] = None) -> bool:
    """True when a NON-EMPTY title is a generic placeholder, not a real title.

    Shared by the detector (DocumentTitleAnalyzer) and the fixer
    (SetDocumentTitleExecutor) so they always agree on what counts as junk —
    otherwise we'd flag a title the fixer then refuses to overwrite. Empty
    titles are handled separately as "missing"; this only judges set-but-useless
    ones. Conservative on purpose (a real title must never be called a
    placeholder): only known software defaults, "<name><digits>", the bare
    filename, and "Microsoft Word - …" print titles.
    """
    t = (title or "").strip()
    if not t:
        return False  # empty is "missing", handled by the caller
    low = t.lower()
    if low in _GENERIC_TITLES:
        return True
    if _NUMBERED_DEFAULT_RE.match(low):
        return True
    if _PRINTED_FROM_RE.match(low):
        return True
    if filename:
        fn = str(filename).strip().lower()
        if fn:
            stem = fn.rsplit(".", 1)[0]
            if low == fn or low == stem:
                return True
    return False


def attach_flag(node: Node, code: AccessibilityFlagCode) -> bool:
    for flag in node.accessibility_flags:
        if flag.code == code:
            return False
    node.accessibility_flags.append(AccessibilityFlag.from_code(code))
    return True
