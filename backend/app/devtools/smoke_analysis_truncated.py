"""Smoke: a document longer than the page cap is DISCLOSED, not silently scored.

The PDF parser caps work at MAX_PDF_PAGES (default 400) and records the
truncation on the tree. Before ANALYSIS_TRUNCATED existed nothing read that
record: a 512-page PDF got 400 pages analyzed and the user saw a score and a
fix count exactly as if it covered the whole file. That is a silent overclaim
on the "large documents" axis — the thing the honesty invariant forbids.

Pinned here (with MAX_PDF_PAGES lowered to 5 so the test is fast):
  * a PDF OVER the cap raises ANALYSIS_TRUNCATED as an ERROR on the root
  * the parser records pages_processed == the cap
  * a PDF AT or UNDER the cap raises nothing and records no truncation
  * the flag has a catalog definition and a (manual-only) remediation entry,
    so it can never fall through to the UI's "unknown rule" info-level bucket

Usage:
    python -m app.devtools.smoke_analysis_truncated
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_trunc_')}/s.db")
# Must be set BEFORE the parser module is imported — it reads the env at import.
os.environ["MAX_PDF_PAGES"] = "5"

import io  # noqa: E402
from pathlib import Path  # noqa: E402

from pypdf import PdfWriter  # noqa: E402
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject  # noqa: E402

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.models.accessibility import (  # noqa: E402
    FLAG_DEFINITIONS,
    REMEDIATION_ACTIONS_BY_FLAG,
    AccessibilityFlagCode as F,
    Severity,
)
from app.parsers import parse_to_tree  # noqa: E402


def _pdf_with_pages(n: int) -> bytes:
    w = PdfWriter()
    font = DictionaryObject()
    font.update({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    fonts = DictionaryObject()
    fonts[NameObject("/F1")] = w._add_object(font)  # noqa: SLF001
    res = DictionaryObject()
    res[NameObject("/Font")] = fonts
    for i in range(n):
        page = w.add_blank_page(width=612, height=792)
        cs = DecodedStreamObject()
        cs.set_data(b"BT /F1 12 Tf 72 700 Td (Page %d body text for the analyzer) Tj ET" % (i + 1))
        page[NameObject("/Contents")] = w._add_object(cs)  # noqa: SLF001
        page[NameObject("/Resources")] = res
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _root_flags(tree) -> set:
    return {f.code for f in (tree.root.accessibility_flags or [])}


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="508_trunc_"))

    # --- over the cap -------------------------------------------------------
    over = tmp / "over.pdf"
    over.write_bytes(_pdf_with_pages(9))
    tree = run_analyzers(parse_to_tree(str(over)).tree)
    props = tree.root.metadata.properties or {}
    check("over-cap PDF: parser records the truncation", props.get("pages_truncated") is True, str(props))
    check("over-cap PDF: pages_processed equals the cap", int(props.get("pages_processed") or 0) == 5, str(props))
    check("over-cap PDF: ANALYSIS_TRUNCATED is raised on the root",
          F.ANALYSIS_TRUNCATED in _root_flags(tree), str(_root_flags(tree)))
    check("over-cap PDF: page_count still reports the TRUE total",
          int(props.get("page_count") or 0) == 9, str(props.get("page_count")))

    # --- at the cap ---------------------------------------------------------
    at = tmp / "at.pdf"
    at.write_bytes(_pdf_with_pages(5))
    tree = run_analyzers(parse_to_tree(str(at)).tree)
    props = tree.root.metadata.properties or {}
    check("at-cap PDF: no truncation recorded", not props.get("pages_truncated"), str(props))
    check("at-cap PDF: ANALYSIS_TRUNCATED is NOT raised",
          F.ANALYSIS_TRUNCATED not in _root_flags(tree), str(_root_flags(tree)))

    # --- the flag is fully wired, so it can never fall through ---------------
    defn = FLAG_DEFINITIONS.get(F.ANALYSIS_TRUNCATED)
    check("flag has a definition", defn is not None)
    check("flag is an ERROR (it invalidates the score's coverage)",
          defn is not None and defn.severity == Severity.ERROR)
    rem = REMEDIATION_ACTIONS_BY_FLAG.get(F.ANALYSIS_TRUNCATED) or []
    check("flag has a remediation entry", bool(rem))
    check("remediation is manual-only (no auto-fix can read pages we skipped)",
          bool(rem) and all(not r.is_auto_applicable for r in rem))

    # --- and the API summary exposes the count ------------------------------
    from app.api.pipeline import _pages_analyzed

    over_tree = run_analyzers(parse_to_tree(str(over)).tree)
    check("API summary reports pagesAnalyzed for a truncated doc", _pages_analyzed(over_tree) == 5,
          str(_pages_analyzed(over_tree)))
    at_tree = run_analyzers(parse_to_tree(str(at)).tree)
    check("API summary reports None when nothing was truncated", _pages_analyzed(at_tree) is None,
          str(_pages_analyzed(at_tree)))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
