"""Document metadata analyzers."""

from __future__ import annotations

from app.analyzers.base import Analyzer
from app.analyzers.helpers import attach_flag
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
)


class DocumentLanguageAnalyzer(Analyzer):
    name = "document_language_missing"

    def analyze(self, tree: AccessibilityTree) -> None:
        language = (tree.root.metadata.language or "").strip()
        if not language:
            attach_flag(tree.root, AccessibilityFlagCode.DOCUMENT_LANGUAGE_MISSING)


class DocumentTitleAnalyzer(Analyzer):
    name = "document_title_missing"

    def analyze(self, tree: AccessibilityTree) -> None:
        title = tree.root.metadata.properties.get("title")
        if not isinstance(title, str) or not title.strip():
            attach_flag(tree.root, AccessibilityFlagCode.DOCUMENT_TITLE_MISSING)
