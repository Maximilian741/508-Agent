"""Smoke: TABLE_COMPLEX_NEEDS_SUMMARY detection (new detection-only flag).

Verifies the complex-table analyzer:
  (a) FIRES on a table with a merged DATA cell (col_span=2) in a non-trivial grid,
  (b) FIRES on a large plain grid (>= 20 rows, >= 3 cols),
  (c) does NOT fire on a small simple grid (false-positive guard),
  (d) does NOT fire on a tiny table whose only merge is below the size floor,
  (e) does NOT fire on a complex table that already carries caption metadata.

Detection-only: the flag maps to FLAG_FOR_MANUAL_REVIEW, so it can never be
counted as an automated fix or trigger a charge (honesty invariant).

Usage:
    python -m app.devtools.smoke_table_complexity
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_tblcx_')}/s.db"

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.models.accessibility import (  # noqa: E402
    AccessibilityFlagCode,
    AccessibilityTree,
    ContentKind,
    DocumentNode,
    NodeContent,
    NodeMetadata,
    TableCellNode,
    TableCellType,
    TableNode,
    TableRowNode,
)

_n = 0


def _id(prefix: str) -> str:
    global _n
    _n += 1
    return f"{prefix}-{_n}"


def _cell(text: str, *, header: bool = False, row_span: int = 1, col_span: int = 1) -> TableCellNode:
    return TableCellNode(
        id=_id("cell"),
        cell_type=TableCellType.HEADER if header else TableCellType.DATA,
        row_span=row_span,
        col_span=col_span,
        content=NodeContent(kind=ContentKind.TEXT, text=text or " "),
        metadata=NodeMetadata(source_format="docx"),
        children=[],
        accessibility_flags=[],
    )


def _row(cells: list[TableCellNode]) -> TableRowNode:
    return TableRowNode(
        id=_id("row"),
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(source_format="docx"),
        children=cells,
        accessibility_flags=[],
    )


def _table(rows: list[TableRowNode], *, caption: str | None = None) -> TableNode:
    meta = NodeMetadata(source_format="docx")
    if caption is not None:
        meta.properties["caption"] = caption
    return TableNode(
        id=_id("table"),
        content=NodeContent(kind=ContentKind.NONE),
        metadata=meta,
        children=rows,
        accessibility_flags=[],
    )


def _tree(table: TableNode) -> AccessibilityTree:
    root = DocumentNode(
        id=_id("doc"),
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(source_format="docx"),
        children=[table],
        accessibility_flags=[],
    )
    return AccessibilityTree(root=root)


def _fires(table: TableNode) -> bool:
    tree = _tree(table)
    run_analyzers(tree)
    return any(
        f.code == AccessibilityFlagCode.TABLE_COMPLEX_NEEDS_SUMMARY
        for f in table.accessibility_flags
    )


def _merged_table() -> TableNode:
    # 3 columns; rows 1-2 have a DATA cell spanning 2 columns -> merged data.
    # total cells = 3 (header) + 2 + 2 = 7 (>= 6 floor).
    header = _row([_cell("A", header=True), _cell("B", header=True), _cell("C", header=True)])
    r1 = _row([_cell("spans two", col_span=2), _cell("x")])
    r2 = _row([_cell("spans two", col_span=2), _cell("y")])
    return _table([header, r1, r2])


def _big_grid() -> TableNode:
    # 21 rows x 3 cols simple grid -> large.
    rows = [_row([_cell("h1", header=True), _cell("h2", header=True), _cell("h3", header=True)])]
    for i in range(20):
        rows.append(_row([_cell(f"a{i}"), _cell(f"b{i}"), _cell(f"c{i}")]))
    return _table(rows)


def _small_simple() -> TableNode:
    # 3x3 plain grid, no merges -> NOT complex.
    rows = [_row([_cell("h1", header=True), _cell("h2", header=True), _cell("h3", header=True)])]
    for i in range(2):
        rows.append(_row([_cell(f"a{i}"), _cell(f"b{i}"), _cell(f"c{i}")]))
    return _table(rows)


def _tiny_merge() -> TableNode:
    # 2x2-ish with one merged DATA cell but only 3 cells total (< 6 floor) -> NOT complex.
    header = _row([_cell("A", header=True), _cell("B", header=True)])
    r1 = _row([_cell("spans two", col_span=2)])
    return _table([header, r1])


def _captioned_complex() -> TableNode:
    # Same shape as _merged_table but with explicit caption metadata -> skipped.
    t = _merged_table()
    t.metadata.properties["caption"] = "Revenue by region, with merged regional totals."
    return t


def main() -> int:
    cases = [
        ("merged DATA cell fires", _merged_table(), True),
        ("large grid fires", _big_grid(), True),
        ("small simple grid does not fire", _small_simple(), False),
        ("tiny merge below size floor does not fire", _tiny_merge(), False),
        ("captioned complex table does not fire", _captioned_complex(), False),
    ]
    ok = True
    for name, table, expected in cases:
        got = _fires(table)
        status = "PASS" if got == expected else "FAIL"
        if got != expected:
            ok = False
        print(f"[{status}] {name}: fired={got} expected={expected}")

    print("SMOKE TABLE COMPLEXITY:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
