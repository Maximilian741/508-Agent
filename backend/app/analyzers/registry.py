"""Analyzer registry and runner."""

from __future__ import annotations

from typing import Iterable, List, Optional

from app.analyzers.base import Analyzer
from app.analyzers.contrast_analyzer import ContrastAnalyzer
from app.analyzers.document_analyzer import (
    DocumentHeadingsAnalyzer,
    DocumentLanguageAnalyzer,
    DocumentTitleAnalyzer,
    FormFieldLabelAnalyzer,
    ScannedDocumentAnalyzer,
    SlideTitleAnalyzer,
    UntaggedPdfAnalyzer,
)
from app.analyzers.heading_analyzer import HeadingLevelJumpAnalyzer, TextStyledAsHeadingAnalyzer
from app.analyzers.heading_text_analyzer import HeadingTextEmptyAnalyzer
from app.analyzers.image_analyzer import (
    DecorativeImageAltAnalyzer,
    MissingAltTextAnalyzer,
    NonDescriptiveAltTextAnalyzer,
)
from app.analyzers.link_analyzer import LinkNameMissingAnalyzer, LinkTextAnalyzer
from app.analyzers.link_target_analyzer import LinkTargetBrokenAnalyzer
from app.analyzers.list_analyzer import ListStructureAnalyzer
from app.analyzers.reading_order_analyzer import ReadingOrderAnalyzer
from app.analyzers.table_analyzer import TableMissingHeadersAnalyzer
from app.analyzers.table_caption_analyzer import TableCaptionMissingAnalyzer
from app.analyzers.table_complexity_analyzer import TableComplexityAnalyzer
from app.analyzers.nested_table_analyzer import NestedTableAnalyzer
from app.models.accessibility import AccessibilityTree


def get_default_analyzers() -> List[Analyzer]:
    return [
        MissingAltTextAnalyzer(),
        DecorativeImageAltAnalyzer(),
        NonDescriptiveAltTextAnalyzer(),
        HeadingLevelJumpAnalyzer(),
        # SkippedHeadingLevelAnalyzer deliberately NOT registered: it was a
        # condition-identical twin of HeadingLevelJumpAnalyzer, so every jump
        # double-counted (one fix + one phantom "pending manual" duplicate).
        TextStyledAsHeadingAnalyzer(),
        HeadingTextEmptyAnalyzer(),
        TableMissingHeadersAnalyzer(),
        # TableHeaderScopeAnalyzer NOT registered: both OOXML parsers always
        # assign COLUMN scope to header cells, so the NONE-scope condition is
        # unreachable from parsed documents — the rule could never fire.
        TableCaptionMissingAnalyzer(),
        TableComplexityAnalyzer(),
        NestedTableAnalyzer(),
        ListStructureAnalyzer(),
        LinkTextAnalyzer(),
        LinkNameMissingAnalyzer(),
        LinkTargetBrokenAnalyzer(),
        DocumentLanguageAnalyzer(),
        DocumentTitleAnalyzer(),
        DocumentHeadingsAnalyzer(),
        # Re-registered: the PPTX parser now marks slides whose text shapes
        # are stacked bottom-before-top (reading_order_inverted), so the
        # analyzer has a real signal to fire on.
        ReadingOrderAnalyzer(),
        ContrastAnalyzer(),
        FormFieldLabelAnalyzer(),
        SlideTitleAnalyzer(),
        ScannedDocumentAnalyzer(),
        UntaggedPdfAnalyzer(),
    ]


def run_analyzers(
    tree: AccessibilityTree, analyzers: Optional[Iterable[Analyzer]] = None
) -> AccessibilityTree:
    selected = list(analyzers) if analyzers is not None else get_default_analyzers()
    for analyzer in selected:
        analyzer.run(tree)
    return tree
