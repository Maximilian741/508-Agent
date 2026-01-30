"""Analyzer registry and runner."""

from __future__ import annotations

from typing import Iterable, List, Optional

from app.analyzers.base import Analyzer
from app.analyzers.document_analyzer import DocumentLanguageAnalyzer, DocumentTitleAnalyzer
from app.analyzers.heading_analyzer import HeadingLevelJumpAnalyzer, SkippedHeadingLevelAnalyzer
from app.analyzers.image_analyzer import DecorativeImageAltAnalyzer, MissingAltTextAnalyzer
from app.analyzers.link_analyzer import LinkTextAnalyzer
from app.analyzers.list_analyzer import ListStructureAnalyzer
from app.analyzers.reading_order_analyzer import ReadingOrderAnalyzer
from app.analyzers.table_analyzer import TableHeaderScopeAnalyzer, TableMissingHeadersAnalyzer
from app.models.accessibility import AccessibilityTree


def get_default_analyzers() -> List[Analyzer]:
    return [
        MissingAltTextAnalyzer(),
        DecorativeImageAltAnalyzer(),
        HeadingLevelJumpAnalyzer(),
        SkippedHeadingLevelAnalyzer(),
        TableMissingHeadersAnalyzer(),
        TableHeaderScopeAnalyzer(),
        ListStructureAnalyzer(),
        LinkTextAnalyzer(),
        DocumentLanguageAnalyzer(),
        DocumentTitleAnalyzer(),
        ReadingOrderAnalyzer(),
    ]


def run_analyzers(
    tree: AccessibilityTree, analyzers: Optional[Iterable[Analyzer]] = None
) -> AccessibilityTree:
    selected = list(analyzers) if analyzers is not None else get_default_analyzers()
    for analyzer in selected:
        analyzer.run(tree)
    return tree
