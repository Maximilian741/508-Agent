"""Smoke: the pipeline score only counts fixes the writer ACTUALLY persists.

Before this guard, any executor that reported ``success`` in-memory inflated the
score and the conformance grade — even for actions (reading order, PDF
heading levels) that no writer persists to the downloaded file.
This test pins the honest behaviour: non-persisted "successes" count as pending
manual work, not as fixes.

Usage:
    python -m app.devtools.smoke_score_honesty
"""

from __future__ import annotations

import os
import sys
import tempfile
from types import SimpleNamespace

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_score_')}/s.db"

from app.api.pipeline import _action_persists, _build_score  # noqa: E402
from app.models.accessibility import Severity  # noqa: E402


def _ex(action: str, status: str = "success"):
    return SimpleNamespace(
        action_code=SimpleNamespace(value=action),
        status=SimpleNamespace(value=status),
    )


def _v(sev: str = Severity.ERROR.value):
    return SimpleNamespace(severity=sev)


def main() -> int:
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    viol = [_v(), _v()]

    # DOCX: a reading-order "success" is in-memory only (no writer) -> pending,
    # not fixed; the document title write does persist.
    s = _build_score(
        violations=viol,
        executions=[_ex("RESOLVE_READING_ORDER"), _ex("SET_DOCUMENT_TITLE")],
        source_format="docx",
    )
    check("docx: only persisted action counted as fixed", s.fixedAutomatically == 1)
    check("docx: non-persisted success counted as pending", s.pendingManual == 1)

    # PDF heading-LEVEL structure is not written (text blocks are tagged /P, not
    # H1/H2), so NORMALIZE_HEADING_LEVEL does not persist; metadata does.
    s2 = _build_score(
        violations=viol,
        executions=[_ex("NORMALIZE_HEADING_LEVEL"), _ex("SET_DOCUMENT_LANGUAGE")],
        source_format="pdf",
    )
    check("pdf: heading-level success not counted (not written as H1/H2)", s2.fixedAutomatically == 1)
    check("pdf: heading-level success counted pending", s2.pendingManual == 1)

    # A flag-for-manual-review "success" must never count as a fix.
    s3 = _build_score(
        violations=viol,
        executions=[_ex("FLAG_FOR_MANUAL_REVIEW")],
        source_format="docx",
    )
    check("manual-review success is not a fix", s3.fixedAutomatically == 0 and s3.pendingManual == 1)

    # _action_persists matrix sanity.
    check("docx alt text persists", _action_persists("GENERATE_ALT_TEXT", "docx"))
    check("pptx alt text persists", _action_persists("GENERATE_ALT_TEXT", "pptx"))
    check("pdf alt text NOW persists (tagged as /Figure with /Alt)", _action_persists("GENERATE_ALT_TEXT", "pdf"))
    check("pdf heading LEVELS still do not persist (no H1/H2 tagging yet)", not _action_persists("NORMALIZE_HEADING_LEVEL", "pdf"))
    check("docx link text NOW persists (link-text writer)", _action_persists("IMPROVE_LINK_TEXT", "docx"))
    check("pptx link text NOW persists (link-text writer)", _action_persists("IMPROVE_LINK_TEXT", "pptx"))
    check("pdf link text does NOT persist (no pdf link writer)", not _action_persists("IMPROVE_LINK_TEXT", "pdf"))
    check(
        "docx list structure NOW persists (w:numPr + numbering.xml writer; see smoke_fake_lists)",
        _action_persists("FIX_LIST_STRUCTURE", "docx"),
    )
    check(
        "pptx list structure NOW persists (a:buChar/a:buAutoNum writer; see smoke_fake_lists)",
        _action_persists("FIX_LIST_STRUCTURE", "pptx"),
    )
    check("pdf list structure does NOT persist via this action", not _action_persists("FIX_LIST_STRUCTURE", "pdf"))
    check("reading order does NOT persist", not _action_persists("RESOLVE_READING_ORDER", "docx"))
    check("docx title persists", _action_persists("SET_DOCUMENT_TITLE", "docx"))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
