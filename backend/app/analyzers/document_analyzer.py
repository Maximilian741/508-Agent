"""Document metadata analyzers."""

from __future__ import annotations

from app.analyzers.base import Analyzer
from app.analyzers.helpers import attach_flag, iter_nodes
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    HeadingNode,
    ParagraphNode,
)


# A document needs at least this much running text before "no headings at all"
# becomes a navigation problem worth flagging — keeps short notes/letters quiet.
_NO_HEADINGS_MIN_PARAGRAPHS = 12
_NO_HEADINGS_MIN_CHARS = 1200
# PDFs are gated on characters only (pypdf collapses paragraphs), so use a
# higher character bar to be sure it's genuinely a substantial document.
_NO_HEADINGS_MIN_CHARS_PDF = 2000


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


class FormFieldLabelAnalyzer(Analyzer):
    """Flag documents whose form fields lack an accessible label (WCAG 3.3.2 /
    4.1.2). The parser records ``form_fields_unlabeled`` on the document root."""

    name = "form_field_unlabeled"

    def analyze(self, tree: AccessibilityTree) -> None:
        props = tree.root.metadata.properties or {}
        try:
            unlabeled = int(props.get("form_fields_unlabeled") or 0)
        except (TypeError, ValueError):
            unlabeled = 0
        if unlabeled > 0:
            attach_flag(tree.root, AccessibilityFlagCode.FORM_FIELD_UNLABELED)


class SlideTitleAnalyzer(Analyzer):
    """Flag presentations where one or more slides lack a title (the #1
    PowerPoint accessibility failure). The parser records the count on the
    document root."""

    name = "slide_title_missing"

    def analyze(self, tree: AccessibilityTree) -> None:
        props = tree.root.metadata.properties or {}
        try:
            missing = int(props.get("slides_missing_titles") or 0)
        except (TypeError, ValueError):
            missing = 0
        if missing > 0:
            attach_flag(tree.root, AccessibilityFlagCode.SLIDE_TITLE_MISSING)


class DocumentHeadingsAnalyzer(Analyzer):
    """Flag long documents that have no headings at all (WCAG 2.4.6 / 1.3.1).

    Headings are how screen-reader and keyboard users skim and navigate; a
    multi-page document with zero headings forces linear reading. Scoped to
    page-flow formats (DOCX/PDF) — slide decks organise by slide title, not
    headings, so PPTX is excluded to avoid false positives. Conservative
    thresholds keep short letters/notes from being flagged."""

    name = "document_no_headings"

    def analyze(self, tree: AccessibilityTree) -> None:
        fmt = (tree.root.metadata.source_format or "").lower()
        if fmt not in ("docx", "pdf"):
            return

        paragraphs = 0
        chars = 0
        for node in iter_nodes(tree):
            if isinstance(node, HeadingNode):
                return  # has at least one heading — nothing to flag
            if isinstance(node, ParagraphNode):
                text = (node.content.text or "").strip() if node.content else ""
                if text:
                    paragraphs += 1
                    chars += len(text)

        # DOCX yields one ParagraphNode per paragraph, so the paragraph count is
        # a reliable "this is a long document" signal. PDF text extraction
        # (pypdf) collapses a whole page into one or two big ParagraphNodes, so
        # the paragraph count is unreliable there — gate PDFs on character count
        # alone (with a higher bar) so a long, heading-less PDF still flags.
        if fmt == "pdf":
            enough = chars >= _NO_HEADINGS_MIN_CHARS_PDF
        else:  # docx
            enough = paragraphs >= _NO_HEADINGS_MIN_PARAGRAPHS and chars >= _NO_HEADINGS_MIN_CHARS
        if enough:
            attach_flag(tree.root, AccessibilityFlagCode.DOCUMENT_NO_HEADINGS)
