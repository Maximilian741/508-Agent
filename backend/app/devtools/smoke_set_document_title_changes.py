"""Smoke: SET_DOCUMENT_TITLE writes a REAL title or nothing — never a placeholder.

This smoke used to assert the opposite: its fixture is an empty document (no
headings, no text, no filename), and it demanded that the executor write
"Untitled Document" and report SUCCESS. That string is one our own
DocumentTitleAnalyzer flags as a placeholder; writing it as the /Title with
DisplayDocTitle on (so viewers show it in the title bar), reporting it as a
fix, and charging for it was the exact overclaim the honesty invariant
forbids. The test was codifying the bug.

Now pinned:
  * a document with NOTHING to derive a title from -> SKIPPED, no title
    written, note tells the user to set it manually
  * a document with a heading -> SUCCESS, the heading IS the title
  * a document with only a usable filename -> SUCCESS, humanized filename

Run with:
    python -m app.devtools.smoke_set_document_title_changes
"""

from __future__ import annotations

import sys

from app.analyzers import get_default_analyzers, run_analyzers
from app.devtools.test_fixtures import document_title_missing_tree
from app.models.accessibility import (
    AccessibilityFlag,
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    ContentKind,
    DocumentNode,
    HeadingNode,
    NodeContent,
    NodeMetadata,
)
from app.services.remediation_planner import RemediationPolicy, plan_remediations
from app.services.remediators import execute_plans

_POLICY = RemediationPolicy(
    allow_auto_actions=True,
    allow_ai_actions=False,
    require_human_review_for_all=False,
    allowed_action_codes=None,
)


def _run(tree: AccessibilityTree):
    run_analyzers(tree, get_default_analyzers())
    plans = [p for p in plan_remediations(tree, _POLICY) if p.target_node_id == "doc-1"]
    results = execute_plans(tree, plans)
    return next((r for r in results if r.action_code == ActionCode.SET_DOCUMENT_TITLE), None)


def _doc(children=None, filename=None) -> AccessibilityTree:
    props = {"filename": filename} if filename else {}
    doc = DocumentNode(
        id="doc-1",
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(language="en", source_format="pdf", properties=props),
        children=children or [],
        accessibility_flags=[AccessibilityFlag.from_code(AccessibilityFlagCode.DOCUMENT_TITLE_MISSING)],
    )
    return AccessibilityTree(root=doc)


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    # 1. Nothing to derive from -> refuse the placeholder.
    tree, document = document_title_missing_tree()
    r = _run(tree)
    check("empty document: executor SKIPS", r is not None and r.status.value == "skipped", getattr(r, "notes", None))
    check("empty document: NO title written (not 'Untitled Document')",
          not (document.metadata.properties or {}).get("title"), repr((document.metadata.properties or {}).get("title")))
    check("empty document: note tells the user to set it manually",
          r is not None and "manually" in (r.notes or "").lower(), getattr(r, "notes", None))

    # 2. A heading exists -> it becomes the title.
    h = HeadingNode(id="h1", level=1, content=NodeContent(kind=ContentKind.TEXT, text="Regional Budget Review"),
                    metadata=NodeMetadata(page=1, source_format="pdf"), children=[], accessibility_flags=[])
    tree = _doc(children=[h])
    r = _run(tree)
    check("document with a heading: executor SUCCEEDS", r is not None and r.status.value == "success", getattr(r, "notes", None))
    check("document with a heading: the heading IS the title",
          (tree.root.metadata.properties or {}).get("title") == "Regional Budget Review",
          repr((tree.root.metadata.properties or {}).get("title")))

    # 3. Only a filename -> humanized filename (a real signal, unlike the placeholder).
    tree = _doc(filename="q3-financial_report v2.pdf")
    r = _run(tree)
    check("document with only a filename: executor SUCCEEDS", r is not None and r.status.value == "success", getattr(r, "notes", None))
    check("document with only a filename: title is the humanized stem",
          (tree.root.metadata.properties or {}).get("title") == "Q3 Financial Report V2",
          repr((tree.root.metadata.properties or {}).get("title")))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
