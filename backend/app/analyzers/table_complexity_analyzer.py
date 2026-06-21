"""Complex-table analyzer.

Flags structurally complex data tables that need a human-authored *summary*
describing their layout (WCAG techniques H43 — associating cells via
headers/scope — and H73 — the table summary). These are the tables a
screen-reader user cannot perceive from a row-by-row read-out alone:

  * tables with merged DATA cells (``row_span`` or ``col_span`` > 1), whose
    irregular shape breaks the simple "every row has the same columns" model, or
  * large grids, where the sheer volume means a user benefits from an overview
    before navigating cell by cell.

This is DETECTION-ONLY. It maps to ``FLAG_FOR_MANUAL_REVIEW`` exactly like
``TABLE_CAPTION_MISSING`` — writing a meaningful structural summary requires
human judgment, so it is never auto-applied and never counted as an automated
fix (the honesty invariant: we only ever charge for fixes that persist into the
downloaded file).

Relationship to ``TableCaptionMissingAnalyzer``: that rule asks "does this table
have *a label*?"; this one asks "is this table *complex enough to need a
structural summary*?". They are independent and can co-occur on a fully
undescribed complex table — that is intentional (such a table genuinely needs
both a label and a structural overview). We skip tables that already carry
explicit ``caption`` metadata so a described table is not nagged.

Thresholds are deliberately conservative to keep false positives near zero:
small/simple grids and single-column lists never fire.
"""

from __future__ import annotations

from app.analyzers.base import Analyzer
from app.analyzers.helpers import attach_flag, iter_nodes
from app.analyzers.table_caption_analyzer import _has_caption_metadata
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    TableCellNode,
    TableCellType,
    TableNode,
    TableRowNode,
)

# A table must have at least this many cells before a merged cell is treated as
# "complex" — a tiny 2x2 with one merge does not warrant a summary.
_MIN_CELLS_FOR_MERGE = 6
# Large-grid triggers (each additionally requires >= _MIN_COLS columns so a long
# single-column list is never mistaken for a complex data table).
_BIG_ROWS = 20
_BIG_CELLS = 120
_MIN_COLS = 3


def _table_metrics(table: TableNode) -> tuple[int, int, int, bool]:
    """Return (row_count, max_columns, total_cells, has_merged_data_cell).

    ``max_columns`` accounts for ``col_span`` so a row of 2 cells where one spans
    2 columns counts as width 3. Only direct row -> cell descendants are walked,
    so a nested table inside a cell is NOT folded into the outer table's metrics
    (the nested table is a TableNode of its own and is evaluated separately).
    """
    rows = [c for c in table.children if isinstance(c, TableRowNode)]
    total_cells = 0
    max_cols = 0
    merged_data = False
    for row in rows:
        width = 0
        for cell in row.children:
            if not isinstance(cell, TableCellNode):
                continue
            total_cells += 1
            width += max(1, cell.col_span)
            if cell.cell_type == TableCellType.DATA and (cell.row_span > 1 or cell.col_span > 1):
                merged_data = True
        max_cols = max(max_cols, width)
    return len(rows), max_cols, total_cells, merged_data


def _is_complex(table: TableNode) -> bool:
    n_rows, max_cols, total_cells, merged_data = _table_metrics(table)
    if merged_data and total_cells >= _MIN_CELLS_FOR_MERGE:
        return True
    if max_cols >= _MIN_COLS and (n_rows >= _BIG_ROWS or total_cells >= _BIG_CELLS):
        return True
    return False


class TableComplexityAnalyzer(Analyzer):
    """Flags complex data tables that need a structural summary (detection-only)."""

    name = "table_complexity"

    def analyze(self, tree: AccessibilityTree) -> None:
        for node in iter_nodes(tree):
            if not isinstance(node, TableNode):
                continue
            if _has_caption_metadata(node):
                continue
            if _is_complex(node):
                attach_flag(node, AccessibilityFlagCode.TABLE_COMPLEX_NEEDS_SUMMARY)
