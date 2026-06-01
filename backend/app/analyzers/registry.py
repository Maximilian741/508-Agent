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
    SlideTitleAnalyzer,
)
from app.analyzers.heading_analyzer import HeadingLevelJumpAnalyzer, SkippedHeadingLevelAnalyzer
from app.analyzers.heading_text_analyzer import HeadingTextEmptyAnalyzer
from app.analyzers.image_analyzer import (
    DecorativeImageAltAnalyzer,
    MissingAltTextAnalyzer,
    NonDescriptiveAltTextAnalyzer,
)
from app.analyzers.link_analyzer import LinkTextAnalyzer
from app.analyzers.link_target_analyzer import LinkTargetBrokenAnalyzer
from app.analyzers.list_analyzer import ListStructureAnalyzer
from app.analyzers.reading_order_analyzer import ReadingOrderAnalyzer
from app.analyzers.table_analyzer import TableHeaderScopeAnalyzer, TableMissingHeadersAnalyzer
from app.analyzers.table_caption_analyzer import TableCaptionMissingAnalyzer
from app.models.accessibility import AccessibilityTree


def get_default_analyzers() -> List[Analyzer]:
    return [
        MissingAltTextAnalyzer(),
        DecorativeImageAltAnalyzer(),
        NonDescriptiveAltTextAnalyzer(),
        HeadingLevelJumpAnalyzer(),
        SkippedHeadingLevelAnalyzer(),
        HeadingTextEmptyAnalyzer(),
        TableMissingHeadersAnalyzer(),
        TableHeaderScopeAnalyzer(),
        TableCaptionMissingAnalyzer(),
        ListStructureAnalyzer(),
        LinkTextAnalyzer(),
        LinkTargetBrokenAnalyzer(),
        DocumentLanguageAnalyzer(),
        DocumentTitleAnalyzer(),
        DocumentHeadingsAnalyzer(),
        ReadingOrderAnalyzer(),
        ContrastAnalyzer(),
        FormFieldLabelAnalyzer(),
        SlideTitleAnalyzer(),
    ]


def run_analyzers(
    tree: AccessibilityTree, analyzers: Optional[Iterable[Analyzer]] = None
) -> AccessibilityTree:
    selected = list(analyzers) if analyzers is not None else get_default_analyzers()
    for analyzer in selected:
        analyzer.run(tree)
    return tree
