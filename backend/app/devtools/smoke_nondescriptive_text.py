"""Smoke: widened non-descriptive LINK-TEXT and generic TITLE detection.

Both feed auto-fixes — LINK_TEXT_NON_DESCRIPTIVE drives IMPROVE_LINK_TEXT and
DOCUMENT_TITLE_MISSING/placeholder drives SET_DOCUMENT_TITLE — so widening what
counts as "non-descriptive" means more gets fixed automatically. As with the
alt-text detector, the make-or-break property is ZERO false positives: a real
link label or a genuine document title must never be flagged.

Usage:
    python -m app.devtools.smoke_nondescriptive_text
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_ndt_')}/s.db")

from app.analyzers.helpers import is_placeholder_title  # noqa: E402
from app.analyzers.link_analyzer import (  # noqa: E402
    NON_DESCRIPTIVE_LINK_TEXT,
    LinkTextAnalyzer,
    _looks_like_url,
    _normalize_text,
)
from app.models.accessibility import (  # noqa: E402
    AccessibilityFlagCode,
    AccessibilityTree,
    ContentKind,
    DocumentNode,
    LinkNode,
    NodeContent,
    NodeMetadata,
)


def _link_flagged(text: str) -> bool:
    """Mirror LinkTextAnalyzer's exact decision."""
    norm = _normalize_text(text)
    return (not norm) or norm in NON_DESCRIPTIVE_LINK_TEXT or _looks_like_url(text)


# Link text that SHOULD be flagged (generic filler / bare URLs).
BAD_LINKS = [
    "Click here", "read more", "Learn More", "more info", "details",
    "this link", "This Article", "click the link", "Click below",
    "click for more", "Click to learn more", "tap here", "Press here",
    "open link", "more details", "Find out more", "see here", "view this",
    "check it out", "Check this out", "follow this link", "Read on",
    "Read here", "the link", "info", "Read", "full article",
    "https://example.com/page", "www.example.com", "example.com/a",
    ">>", "-->", "***",  # punctuation-only normalizes to empty
]

# Real, descriptive link text that MUST NOT be flagged.
GOOD_LINKS = [
    "Download the 2025 annual report", "Read the full quarterly report",
    "Contact our sales team", "Spring campaign brief", "Next page",
    "Email the support desk", "Jump to the methodology section",
    "View the WCAG 2.1 specification", "more affordable housing options",
    "details of the merger agreement", "click-through conversion guide",
    "Visit example.com for the schedule",  # mentions a domain, isn't only it
]

# Titles that SHOULD be flagged as placeholders.
BAD_TITLES = [
    "Untitled", "Untitled document", "Untitled Presentation", "Untitled Spreadsheet",
    "PowerPoint Presentation", "Microsoft Word Document", "New Document",
    "Blank", "Blank Presentation", "Document1", "Presentation 2", "Slide 1",
    "Sheet1", "Workbook3", "Click to add title", "Add title", "Title here",
    "Your title here", "Google Slides", "Form 4", "Copy 2", "Drawing1",
]

# Real titles that MUST NOT be flagged.
GOOD_TITLES = [
    "Annual Report 2025", "Q3 Sales Review", "Meeting Notes",
    "Project Phoenix Plan", "Report", "Draft Budget", "Memo to Staff",
    "2025 Benefits Guide", "Form 1040 Instructions", "Workbook for Algebra I",
    "Presentation Skills Handbook", "The Document That Changed History",
]


def main() -> int:
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    # --- link text ---
    for bad in BAD_LINKS:
        check(f"LINK BAD  {bad!r} flagged", _link_flagged(bad))
    for good in GOOD_LINKS:
        check(f"LINK GOOD {good!r} NOT flagged", not _link_flagged(good))

    # analyzer end-to-end (one bad, one good)
    def analyze_link(text: str) -> bool:
        link = LinkNode(
            id="l-1", target="https://x.com",
            content=NodeContent(kind=ContentKind.TEXT, text=text),
            metadata=NodeMetadata(source_format="html", properties={}),
            children=[], accessibility_flags=[],
        )
        root = DocumentNode(
            id="doc-1", content=NodeContent(kind=ContentKind.NONE),
            metadata=NodeMetadata(source_format="html", properties={}),
            children=[link], accessibility_flags=[],
        )
        LinkTextAnalyzer().analyze(AccessibilityTree(root=root, metadata={}))
        return any(f.code == AccessibilityFlagCode.LINK_TEXT_NON_DESCRIPTIVE for f in link.accessibility_flags)

    check("analyzer flags 'tap here'", analyze_link("tap here"))
    check("analyzer does NOT flag 'Download the 2025 annual report'",
          not analyze_link("Download the 2025 annual report"))

    # --- titles ---
    for bad in BAD_TITLES:
        check(f"TITLE BAD  {bad!r} flagged", is_placeholder_title(bad))
    for good in GOOD_TITLES:
        check(f"TITLE GOOD {good!r} NOT flagged", not is_placeholder_title(good))

    # filename-as-title still caught; empty is 'missing' not placeholder
    check("title equal to filename flagged", is_placeholder_title("report.docx", "report.docx"))
    check("empty title is not a placeholder (missing instead)", not is_placeholder_title(""))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
