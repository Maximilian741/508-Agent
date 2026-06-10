"""Smoke: two detection false-negatives fixed (launch follow-ups).

1. DOCUMENT_NO_HEADINGS now fires on long PDFs. pypdf collapses a page into
   one or two big ParagraphNodes, so the DOCX paragraph-count threshold was
   unreachable for PDFs; PDFs are now gated on character count instead.
2. TABLE_MISSING_HEADERS can now fire on DOCX data tables. The parser used to
   type row 0 as a header unconditionally (so the check never fired); it now
   only does so when Word marks/styles the row as a header (w:tblHeader or a
   bold first row). A plain data grid with no header row is flagged, while
   small/ambiguous tables keep the old assumption (no false positives).

Usage:
    python -m app.devtools.smoke_detection_accuracy
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_detacc_')}/s.db"

from docx import Document  # noqa: E402
from docx.oxml import OxmlElement  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.models.accessibility import (  # noqa: E402
    AccessibilityFlagCode,
    AccessibilityTree,
    ContentKind,
    DocumentNode,
    HeadingNode,
    NodeContent,
    NodeMetadata,
    ParagraphNode,
)
from app.parsers import parse_to_tree  # noqa: E402


def _codes(tree: AccessibilityTree) -> set:
    found = set()

    def walk(node):
        for f in node.accessibility_flags:
            found.add(f.code.value)
        for ch in node.children:
            walk(ch)

    walk(tree.root)
    return found


def _pdf_tree(n_paras: int, chars_each: int, with_heading: bool) -> AccessibilityTree:
    """A constructed PDF tree: pypdf-style few-but-large paragraph nodes."""
    kids = []
    if with_heading:
        kids.append(HeadingNode(
            id="h1", level=1,
            content=NodeContent(kind=ContentKind.TEXT, text="A Heading"),
            metadata=NodeMetadata(source_format="pdf"),
        ))
    body = "x" * chars_each
    for i in range(n_paras):
        kids.append(ParagraphNode(
            id=f"p{i}",
            content=NodeContent(kind=ContentKind.TEXT, text=body),
            metadata=NodeMetadata(source_format="pdf"),
        ))
    root = DocumentNode(
        id="doc-1",
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(source_format="pdf"),
        children=kids,
    )
    return AccessibilityTree(root=root)


def main() -> int:
    failures = 0

    def check(name, cond):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp())
    NO_H = AccessibilityFlagCode.DOCUMENT_NO_HEADINGS.value
    NO_TH = AccessibilityFlagCode.TABLE_MISSING_HEADERS.value

    # --- 1. PDF no-headings (constructed trees mimic pypdf's collapsed output) ---
    # PDFs are now covered by PDF_UNTAGGED rather than the text-shape
    # DOCUMENT_NO_HEADINGS heuristic (which cannot see structure tags and
    # would contradict our own tagged output). Assert the new contract:
    UNTAGGED = AccessibilityFlagCode.PDF_UNTAGGED.value

    def _pdf_props(tree, *, tagged: bool, pages: int = 2, chars: int = 2400):
        tree.root.metadata.properties.update(
            {
                "pdf_tagged": tagged,
                "page_count": pages,
                "total_text_chars": chars,
                "image_only_pages": 0,
            }
        )
        return tree

    # Untagged text PDF -> PDF_UNTAGGED fires; the no-headings heuristic stays
    # quiet for PDFs (it is DOCX-only now).
    t = _pdf_props(_pdf_tree(2, 1200, with_heading=False), tagged=False)
    codes = _codes(run_analyzers(t))
    check("untagged text PDF -> PDF_UNTAGGED fires", UNTAGGED in codes)
    check("PDF never gets DOCUMENT_NO_HEADINGS (covered by PDF_UNTAGGED)", NO_H not in codes)

    # Tagged PDF (e.g. our own remediated output) -> neither flag.
    t = _pdf_props(_pdf_tree(2, 1200, with_heading=False), tagged=True)
    codes = _codes(run_analyzers(t))
    check("tagged PDF -> no PDF_UNTAGGED", UNTAGGED not in codes)
    check("tagged PDF -> no DOCUMENT_NO_HEADINGS", NO_H not in codes)

    # Near-empty PDF (not enough text to structure) -> no PDF_UNTAGGED noise.
    t = _pdf_props(_pdf_tree(1, 80, with_heading=False), tagged=False, chars=80)
    check("near-empty untagged PDF -> no PDF_UNTAGGED (below text floor)",
          UNTAGGED not in _codes(run_analyzers(t)))

    # --- 2. DOCX table headers ---
    def _save(doc) -> Path:
        p = tmp / f"t{len(list(tmp.iterdir()))}.docx"
        doc.save(str(p))
        return p

    def _fill(table, bold_first=False, mark_header=False):
        rows = len(table.rows)
        cols = len(table.columns)
        if mark_header:
            trPr = table.rows[0]._tr.get_or_add_trPr()
            trPr.append(OxmlElement("w:tblHeader"))
        for r in range(rows):
            for c in range(cols):
                cell = table.cell(r, c)
                cell.text = ""
                run = cell.paragraphs[0].add_run(f"r{r}c{c}")
                if r == 0 and bold_first:
                    run.bold = True

    # (a) bold first row, 3x3 -> header detected -> NOT flagged
    d = Document(); _fill(d.add_table(rows=3, cols=3), bold_first=True)
    check("DOCX 3x3 with bold header row -> no TABLE_MISSING_HEADERS",
          NO_TH not in _codes(run_analyzers(parse_to_tree(str(_save(d))).tree)))

    # (b) w:tblHeader marker, 3x3 -> header detected -> NOT flagged
    d = Document(); _fill(d.add_table(rows=3, cols=3), mark_header=True)
    check("DOCX 3x3 with w:tblHeader marker -> no TABLE_MISSING_HEADERS",
          NO_TH not in _codes(run_analyzers(parse_to_tree(str(_save(d))).tree)))

    # (c) plain first row, 3x3 data grid -> NO header -> NOW flagged (the fix)
    d = Document(); _fill(d.add_table(rows=3, cols=3))
    check("DOCX 3x3 plain data table -> TABLE_MISSING_HEADERS fires",
          NO_TH in _codes(run_analyzers(parse_to_tree(str(_save(d))).tree)))

    # (d) tiny/ambiguous 2x2 plain -> below data-grid bar -> NOT flagged (no false positive)
    d = Document(); _fill(d.add_table(rows=2, cols=2))
    check("DOCX 2x2 plain table -> no TABLE_MISSING_HEADERS (avoids layout-table FP)",
          NO_TH not in _codes(run_analyzers(parse_to_tree(str(_save(d))).tree)))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
