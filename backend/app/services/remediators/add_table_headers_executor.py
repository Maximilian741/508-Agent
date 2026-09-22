"""Executor that marks an existing header row on data tables that lack one."""

from __future__ import annotations

import re
from typing import List, Optional, Sequence

from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    TableCellNode,
    TableCellType,
    TableHeaderScope,
    TableNode,
    TableRowNode,
    iter_reading_order,
)
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


_NEEDS_A_PERSON = "Left for you to mark; nothing was changed and you were not charged for it."


class AddTableHeadersExecutor(RemediationExecutor):
    """Promotes the first row of a header-less table to header cells — only
    when that row really is a row of column names.

    It never invents header text. It used to: a table whose first row did not
    look like labels got a synthetic "Column 1 | Column 2" row inserted into
    the customer's file (visible, bold, repeated on every page) and was
    charged for it — and a first row of DATA ("2023 | 410 | 12%", "North |
    120 | 9%") was promoted to headers, so a screen reader announced "410" as
    a column name for every cell below it. Neither is better than no header.
    When the first row is not clearly a header row the table is left for a
    person with the reason.
    """

    supported_actions = [ActionCode.ADD_TABLE_HEADERS]

    def execute(
        self, plan: RemediationPlan, tree: Optional[AccessibilityTree] = None
    ) -> ExecutionResult:
        action_code = self._first_action(plan)
        self._ensure_supported(action_code)
        if tree is None:
            return _result(action_code, plan, ExecutionStatus.SKIPPED, "No tree provided.")

        target = _find_table(tree, plan.target_node_id)
        if target is None:
            return _result(action_code, plan, ExecutionStatus.SKIPPED, "Target table not found.")
        if not _has_flag(plan, target, AccessibilityFlagCode.TABLE_MISSING_HEADERS):
            return _result(
                action_code,
                plan,
                ExecutionStatus.SKIPPED,
                "Table is not flagged as missing headers; no changes applied.",
            )

        rows = [child for child in target.children if isinstance(child, TableRowNode)]
        if not rows:
            return _refused(
                action_code,
                plan,
                "This table has no rows we could read, so there is no header row to mark.",
            )

        first_row = rows[0]
        existing_cells = [c for c in first_row.children if isinstance(c, TableCellNode)]
        if existing_cells and all(c.cell_type == TableCellType.HEADER for c in existing_cells):
            return _result(
                action_code,
                plan,
                ExecutionStatus.SKIPPED,
                "First row already consists of header cells.",
            )

        body = [
            [c for c in r.children if isinstance(c, TableCellNode)]
            for r in rows[1:]
        ]
        problem = header_row_problem(existing_cells, body)
        if problem:
            return _refused(
                action_code,
                plan,
                f"We did not mark a header row because {problem}. "
                "A person needs to mark (or add) the row of column names.",
            )

        for cell in existing_cells:
            cell.cell_type = TableCellType.HEADER
            if cell.header_scope == TableHeaderScope.NONE:
                cell.header_scope = TableHeaderScope.COLUMN
        return _result(
            action_code,
            plan,
            ExecutionStatus.SUCCESS,
            f"Promoted {len(existing_cells)} cells in row 1 to TH/scope=col.",
        )


def _find_table(tree: AccessibilityTree, node_id: str) -> Optional[TableNode]:
    for node in iter_reading_order(tree.root):
        if node.id == node_id and isinstance(node, TableNode):
            return node
    return None


def _has_flag(plan: RemediationPlan, target, code: AccessibilityFlagCode) -> bool:
    if plan.flag.code == code:
        return True
    return any(flag.code == code for flag in target.accessibility_flags)


# A number, amount, percentage, measurement or date — DATA, not a column name.
# "12%", "$1,200", "(3.5)", "-4", "1/2/2024", "12:30", "3.2 kg".
_NUMERIC_RE = re.compile(
    r"^[\s(]*[-+−–]?\s*[$€£¥₹]?\s*\d[\d,.\s]*(?:%|[kmb]n?|bn|kg|g|km|m|cm|mm|lb|lbs|hrs?|h|min|x)?[)\s]*$"
    r"|^\d{1,4}[/.\-]\d{1,2}(?:[/.\-]\d{1,4})?$"
    r"|^\d{1,2}:\d{2}(?::\d{2})?(?:\s*[ap]\.?m\.?)?$",
    re.IGNORECASE,
)
# A year on its own. Years are legitimate COLUMN NAMES ("Region | 2023 | 2024")
# as long as the column under them is not itself a column of years.
_YEAR_RE = re.compile(r"^(?:FY\s?)?(?:19|20|21)\d\d$", re.IGNORECASE)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f�]")


def _cell_text(cell: TableCellNode) -> str:
    return " ".join(((cell.content.text or "") if cell.content else "").split())


def header_row_problem(cells: Sequence[TableCellNode], body: Sequence[Sequence[TableCellNode]]) -> Optional[str]:
    """Why ``cells`` is NOT a header row, or None when it clearly is.

    A header row names the columns: every cell is a short label, at least one
    of them is a word, and it does not read like the data rows beneath it. A
    row of numbers, amounts or dates is data; a label ending in ':' belongs to
    a label/value form ("Name: | ___"), not to a column; empty cells mean there
    is no complete set of names to promote.
    """
    if not cells:
        return "the first row has no cells"
    texts = [_cell_text(c) for c in cells]
    if any(_CONTROL_RE.search(t) for t in texts):
        return "the first row's text could not be read reliably"
    if any(not t for t in texts):
        return "the first row has empty cells, so it is not a complete row of column names"
    for t in texts:
        if len(t.split()) > 8:
            return "a cell in the first row is a sentence, not a column name"
        if t.endswith(("!", "?")) or (t.endswith(".") and len(t.split()) >= 2):
            # "No." / "Amt." are column names; "Revenue grew." is not.
            return "a cell in the first row is a sentence, not a column name"
        if t.endswith(":"):
            return "the first row is a label and its value (a form layout), not a row of column names"
    years = [bool(_YEAR_RE.match(t)) for t in texts]
    numeric = [bool(_NUMERIC_RE.match(t)) and not y for t, y in zip(texts, years)]
    if any(numeric):
        return "the first row holds numbers or dates (data), not column names"
    if all(years):
        # "2021 | 2022 | 2023" over non-year numbers is a header of years; over
        # more years it is a column of data. Decided per column below.
        pass
    elif not any(re.search(r"[^\W\d_]{2,}", t) for t in texts):
        return "the first row has no words in it"
    # A year in row 1 is a column name only when the column under it is not
    # a column of years too ("2023 | 410 | 12%" over "2024 | 455 | 11%").
    for col, is_year in enumerate(years):
        if not is_year:
            continue
        below = [_cell_text(r[col]) for r in body if col < len(r)]
        if any(_YEAR_RE.match(t) for t in below if t):
            return "the first row is a data row (its years continue in the rows below)"
    if not body:
        return "the table has only one row, so there is no data for a header row to label"
    return None


def _refused(action_code: ActionCode, plan: RemediationPlan, reason: str) -> ExecutionResult:
    return _result(action_code, plan, ExecutionStatus.SKIPPED, f"{reason.strip()} {_NEEDS_A_PERSON}")


def _looks_like_header_row(cells, body: Optional[List[List[TableCellNode]]] = None) -> bool:
    """Back-compat predicate: True when :func:`header_row_problem` finds none."""
    return header_row_problem(cells, body or [[]]) is None


def _result(
    action_code: ActionCode,
    plan: RemediationPlan,
    status: ExecutionStatus,
    notes: str,
) -> ExecutionResult:
    return ExecutionResult(
        action_code=action_code,
        target_node_id=plan.target_node_id,
        status=status,
        notes=notes,
    )
