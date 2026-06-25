"""Table caption analyzer."""

from __future__ import annotations

from app.analyzers.base import Analyzer
from app.analyzers.helpers import attach_flag, iter_nodes
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ContentKind,
    HeadingNode,
    Node,
    ParagraphNode,
    TableCellNode,
    TableNode,
    TableRowNode,
)


_DATA_GRID_MIN_ROWS = 3
_DATA_GRID_MIN_COLS = 2


def _looks_like_data_table(table: TableNode) -> bool:
    """Conservative data-table signal: a 2-D data grid that isn't a layout table.

    A ``<caption>`` describes the *data* a table conveys, so we only flag tables
    that are clearly data tables and never hand a layout table to the caption
    generator (which would assert a tabular meaning that doesn't exist).

    Two guards:
      * Explicit author signal — ``role="presentation"``/``"none"`` marks a
        layout table; never flag it.
      * Shape — require a real grid (``>=3`` rows and ``>=2`` columns). This is
        the SAME data-grid definition TABLE_MISSING_HEADERS uses, and crucially
        it is STABLE across remediation: it does not depend on header cells, so
        adding headers (ADD_TABLE_HEADERS) can never flip a table from
        unflagged-in-the-input to flagged-in-the-output and surface a brand-new
        finding after the user already "fixed everything".
    """
    role = str((table.metadata.properties or {}).get("role") or "").strip().lower()
    if role in {"presentation", "none"}:
        return False
    rows = [r for r in table.children if isinstance(r, TableRowNode)]
    if len(rows) < _DATA_GRID_MIN_ROWS:
        return False
    max_cols = max(
        (sum(1 for c in r.children if isinstance(c, TableCellNode)) for r in rows),
        default=0,
    )
    return max_cols >= _DATA_GRID_MIN_COLS


def _has_caption_metadata(node: TableNode) -> bool:
    caption = node.metadata.properties.get("caption")
    if not isinstance(caption, str):
        return False
    return bool(caption.strip())


def _has_preceding_label(parent: Node, table: TableNode) -> bool:
    """True if the table has a Paragraph/Heading sibling within 1 slot before it."""
    children = list(parent.children)
    try:
        index = children.index(table)
    except ValueError:
        return False
    if index == 0:
        return False
    candidate = children[index - 1]
    if not isinstance(candidate, (ParagraphNode, HeadingNode)):
        return False
    if candidate.content.kind != ContentKind.TEXT:
        return False
    text = (candidate.content.text or "").strip()
    return bool(text)


class TableCaptionMissingAnalyzer(Analyzer):
    """Flags data tables that lack both a caption metadata property and a
    preceding ParagraphNode/HeadingNode label within one sibling slot.

    Only genuine data tables (a >=3x2 grid that isn't role="presentation") are
    considered — see :func:`_looks_like_data_table` — so layout tables are
    never handed to the caption generator. A short caption/label just above a
    data table gives screen reader users context before the row-by-row
    read-out begins.
    """

    name = "table_caption_missing"

    def analyze(self, tree: AccessibilityTree) -> None:
        for parent in iter_nodes(tree):
            for child in parent.children:
                if not isinstance(child, TableNode):
                    continue
                if not _looks_like_data_table(child):
                    continue
                if _has_caption_metadata(child):
                    continue
                if _has_preceding_label(parent, child):
                    continue
                attach_flag(child, AccessibilityFlagCode.TABLE_CAPTION_MISSING)
