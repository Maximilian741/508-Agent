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
# (DOCX-only: PDFs are covered by the stronger PDF_UNTAGGED check instead.)
_NO_HEADINGS_MIN_PARAGRAPHS = 12
_NO_HEADINGS_MIN_CHARS = 1200


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
        # DOCX only. PDFs are covered by PDF_UNTAGGED instead: an untagged PDF
        # gets that (stronger, more accurate) error, and a TAGGED PDF carries
        # its headings in the structure tree, which this text-shape heuristic
        # cannot see — firing here would contradict the struct tree, including
        # the one our own remediation just wrote.
        if fmt != "docx":
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
        # a reliable "this is a long document" signal.
        if paragraphs >= _NO_HEADINGS_MIN_PARAGRAPHS and chars >= _NO_HEADINGS_MIN_CHARS:
            attach_flag(tree.root, AccessibilityFlagCode.DOCUMENT_NO_HEADINGS)


class ScannedDocumentAnalyzer(Analyzer):
    """Flag PDFs that are scanned image(s) with little/no extractable text.

    This is the single most-embarrassing failure mode for a compliance tool: a
    user uploads a scanned PDF (government forms, contracts), the engine sees
    zero text → zero violations → reports "accessible" — but the document is
    100% unreadable to screen readers. We flag it as an ERROR with a clear
    "OCR required first" message, so the user knows nothing else we report is
    meaningful until they OCR the file.

    Detection signals (populated by ``pdf_parser``):
    * ``image_only_pages`` — pages that have image content but <50 chars of text
    * ``total_text_chars`` — characters extracted across all pages
    * ``page_count``

    Conservative thresholds: flag when >=80% of pages are image-only AND average
    text per page is below 50 chars. A short non-scanned PDF (a 1-page memo) is
    excluded by the per-page text count.
    """

    name = "scanned_document_no_text"

    def analyze(self, tree: AccessibilityTree) -> None:
        fmt = (tree.root.metadata.source_format or "").lower()
        if fmt != "pdf":
            return
        props = tree.root.metadata.properties or {}
        try:
            pages = int(props.get("page_count") or 0)
            image_pages = int(props.get("image_only_pages") or 0)
            total_chars = int(props.get("total_text_chars") or 0)
        except (TypeError, ValueError):
            return
        if pages == 0:
            return
        if image_pages >= pages * 0.8 and total_chars / pages < 50:
            attach_flag(tree.root, AccessibilityFlagCode.SCANNED_DOCUMENT_NO_TEXT)


class UntaggedPdfAnalyzer(Analyzer):
    """Flag text PDFs that have NO structure tree (untagged PDFs).

    This is the single most common real-world PDF accessibility failure: the
    text is extractable, so naive checkers report "0 issues" — but a screen
    reader gets an undifferentiated text stream with no headings, lists,
    tables, or reading structure (WCAG 1.3.1 / PDF-UA 7.1-2). It is also the
    failure our remediation genuinely repairs: the PDF writer reconstructs a
    full structure tree (headings, lists, tables, figures, artifacts), so a
    re-uploaded remediated file no longer carries this flag.

    Scanned PDFs (no extractable text) are excluded — they get the stronger
    SCANNED_DOCUMENT_NO_TEXT error and need OCR first.
    """

    name = "pdf_untagged"

    def analyze(self, tree: AccessibilityTree) -> None:
        fmt = (tree.root.metadata.source_format or "").lower()
        if fmt != "pdf":
            return
        props = tree.root.metadata.properties or {}
        if props.get("pdf_tagged"):
            return
        try:
            pages = int(props.get("page_count") or 0)
            image_pages = int(props.get("image_only_pages") or 0)
            total_chars = int(props.get("total_text_chars") or 0)
        except (TypeError, ValueError):
            return
        if pages == 0:
            return
        # Scanned docs are handled by ScannedDocumentAnalyzer; don't double-flag.
        if image_pages >= pages * 0.8 and total_chars / pages < 50:
            return
        # Needs to actually have text content worth structuring.
        if total_chars < 200:
            return
        attach_flag(tree.root, AccessibilityFlagCode.PDF_UNTAGGED)
