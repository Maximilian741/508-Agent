"""Smoke: TABLE_NESTED detection (table inside another table's cell).

Verifies the nested-table analyzer:
  (a) a table placed inside an outer table's cell IS flagged (the inner one),
  (b) the OUTER container table is NOT flagged (no false positive),
  (c) a normal standalone table is NOT flagged,
  (d) triple nesting flags exactly the two inner tables (one flag each), never
      the outermost.

Detection-only: TABLE_NESTED maps to FLAG_FOR_MANUAL_REVIEW, so it can never be
counted as an automated fix or trigger a charge (honesty invariant).

Usage:
    python -m app.devtools.smoke_nested_table
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_nested_')}/s.db"

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
    iter_reading_order,
)

_n = 0


def _id(p: str) -> str:
    global _n
    _n += 1
    return f"{p}-{_n}"


def _cell(*, children=None) -> TableCellNode:
    return TableCellNode(
        id=_id("cell"),
        cell_type=TableCellType.DATA,
        content=NodeContent(kind=ContentKind.TEXT, text="x"),
        metadata=NodeMetadata(source_format="docx"),
        children=children or [],
        accessibility_flags=[],
    )


def _row(cells) -> TableRowNode:
    return TableRowNode(
        id=_id("row"),
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(source_format="docx"),
        children=cells,
        accessibility_flags=[],
    )


def _table(rows, *, tid=None) -> TableNode:
    return TableNode(
        id=tid or _id("table"),
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(source_format="docx"),
        children=rows,
        accessibility_flags=[],
    )


def _simple_table(tid: str) -> TableNode:
    return _table([_row([_cell(), _cell()]), _row([_cell(), _cell()])], tid=tid)


def _tree(table: TableNode) -> AccessibilityTree:
    return AccessibilityTree(
        root=DocumentNode(
            id=_id("doc"),
            content=NodeContent(kind=ContentKind.NONE),
            metadata=NodeMetadata(source_format="docx"),
            children=[table],
            accessibility_flags=[],
        )
    )


def _nested_ids(tree: AccessibilityTree) -> set:
    run_analyzers(tree)
    return {
        node.id
        for node in iter_reading_order(tree.root)
        if any(f.code == AccessibilityFlagCode.TABLE_NESTED for f in node.accessibility_flags)
    }


def main() -> int:
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    # Case A: outer table with one cell containing a nested table.
    inner = _simple_table("inner")
    outer = _table(
        [_row([_cell(), _cell(children=[inner])]), _row([_cell(), _cell()])],
        tid="outer",
    )
    flagged = _nested_ids(_tree(outer))
    check("A inner nested table flagged", "inner" in flagged)
    check("A outer container NOT flagged", "outer" not in flagged)

    # Case B: standalone simple table — no nesting.
    flagged_b = _nested_ids(_tree(_simple_table("solo")))
    check("B standalone table not flagged", flagged_b == set())

    # Case C: triple nesting t1 > t2 > t3.
    t3 = _simple_table("t3")
    t2 = _table([_row([_cell(children=[t3])])], tid="t2")
    t1 = _table([_row([_cell(children=[t2])])], tid="t1")
    flagged_c = _nested_ids(_tree(t1))
    check("C t2 and t3 flagged (both nested)", {"t2", "t3"}.issubset(flagged_c))
    check("C outermost t1 not flagged", "t1" not in flagged_c)
    check("C exactly two nested flags", len(flagged_c) == 2)

    print("SMOKE NESTED TABLE:", "PASS" if failures == 0 else f"FAIL ({failures})")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
