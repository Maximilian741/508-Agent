"""Spreadsheet (XLSX) analyzers.

Both rules key on signals the XLSX parser derives from STABLE shape — the
sheet's tab name, and whether a block of data has a header row we can
identify — never on anything our own writer changes as a side effect. The
writer's one structural fix (turning a clear header + data range into an
Excel table) moves a block from ``candidate`` to ``declared``, which clears
TABLE_MISSING_HEADERS and raises nothing new; an ``unclear`` block is never
touched, so it reads the same before and after "fix everything".
"""

from __future__ import annotations

from app.analyzers.base import Analyzer
from app.analyzers.helpers import attach_flag, iter_nodes
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    SectionNode,
    TableNode,
)

# Header states the parser records on a spreadsheet TableNode for which no
# header row can be declared automatically (see xlsx_parser.judge_header).
UNCLEAR_HEADER_STATES = frozenset({"unclear", "off"})


def _is_xlsx(tree: AccessibilityTree) -> bool:
    return (tree.root.metadata.source_format or "").lower() == "xlsx"


class SheetNameDefaultAnalyzer(Analyzer):
    """A visible, non-empty sheet whose tab still has its default name."""

    name = "sheet_name_default"

    def analyze(self, tree: AccessibilityTree) -> None:
        if not _is_xlsx(tree):
            return
        for section in tree.root.children:
            if not isinstance(section, SectionNode):
                continue
            props = section.metadata.properties or {}
            # An empty sheet has nothing to describe; naming it would not help.
            if props.get("default_sheet_name") and not props.get("empty_sheet"):
                attach_flag(section, AccessibilityFlagCode.SHEET_NAME_DEFAULT)


class DataRangeHeadersUnclearAnalyzer(Analyzer):
    """A block of data with no header row we could identify with confidence.

    The generic TableMissingHeadersAnalyzer skips these blocks: its fix
    (declare row 1 as the header) is exactly the guess this finding refuses
    to make, so the block is reported once, as work for a person, with the
    parser's plain-English reason in ``header_reason``.
    """

    name = "data_range_headers_unclear"

    def analyze(self, tree: AccessibilityTree) -> None:
        if not _is_xlsx(tree):
            return
        for node in iter_nodes(tree):
            if not isinstance(node, TableNode):
                continue
            if (node.metadata.properties or {}).get("header_detection") in UNCLEAR_HEADER_STATES:
                attach_flag(node, AccessibilityFlagCode.DATA_RANGE_HEADERS_UNCLEAR)
