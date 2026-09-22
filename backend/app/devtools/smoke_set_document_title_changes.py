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
    written, note tells the user it is left for them
  * a document with a heading -> SUCCESS, the heading IS the title
  * a document with only a usable filename -> SUCCESS, humanized filename
    (version noise like "v2" dropped)
  * a heading that is a number ("1") or a section name ("Introduction"), or a
    camera / chat-app export filename ("IMG_2041", "DOC-20240912-WA0003") ->
    SKIPPED: "Img 2041" and "Doc 20240912 Wa0003" used to be written and
    charged

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
    check("empty document: note tells the user it is left for them",
          r is not None and "left for you" in (r.notes or "").lower(), getattr(r, "notes", None))

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
          (tree.root.metadata.properties or {}).get("title") == "Q3 Financial Report",
          repr((tree.root.metadata.properties or {}).get("title")))

    # 4. Junk sources are refused, not humanized into a fake title.
    for label, kwargs in (
        ("camera file name", {"filename": "IMG_2041.pdf"}),
        ("WhatsApp export name", {"filename": "DOC-20240912-WA0003.docx"}),
        ("heading '1'", {"children": [HeadingNode(
            id="h1", level=1, content=NodeContent(kind=ContentKind.TEXT, text="1"),
            metadata=NodeMetadata(page=1, source_format="pdf"), children=[], accessibility_flags=[])]}),
        ("heading 'Introduction' + file 'scan0001.pdf'", {"filename": "scan0001.pdf", "children": [HeadingNode(
            id="h1", level=1, content=NodeContent(kind=ContentKind.TEXT, text="Introduction"),
            metadata=NodeMetadata(page=1, source_format="pdf"), children=[], accessibility_flags=[])]}),
    ):
        tree = _doc(**kwargs)
        r = _run(tree)
        check(f"{label}: SKIPPED, no title written",
              r is not None and r.status.value == "skipped" and not (tree.root.metadata.properties or {}).get("title"),
              f"{getattr(r, 'notes', None)} {(tree.root.metadata.properties or {}).get('title')!r}")

    # 5. A heading that names one numbered part is not the document's title.
    tree = _doc(children=[HeadingNode(
        id="h1", level=1, content=NodeContent(kind=ContentKind.TEXT, text="Topic 1: Accessibility programme"),
        metadata=NodeMetadata(page=1, source_format="pdf"), children=[], accessibility_flags=[])])
    r = _run(tree)
    check("heading 'Topic 1: …': SKIPPED, says it names a section",
          r is not None and r.status.value == "skipped" and "names a section" in (r.notes or ""),
          getattr(r, "notes", None))

    # 6. The best title only repeats the file name: refused, and the note
    #    says THAT (it used to claim there was no usable file name at all).
    tree = _doc(filename="Benefits Enrollment Guide.pdf")
    r = _run(tree)
    check("title == file name: SKIPPED with the accurate reason",
          r is not None and r.status.value == "skipped" and "same as the file name" in (r.notes or "")
          and not (tree.root.metadata.properties or {}).get("title"),
          getattr(r, "notes", None))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
