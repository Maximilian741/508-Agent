"""Smoke test for setting document title.

Run with:
python -m app.devtools.smoke_set_document_title_changes
"""

from __future__ import annotations

import sys

from app.analyzers import get_default_analyzers, run_analyzers
from app.models.accessibility import (
    AccessibilityFlag,
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    ContentKind,
    DocumentNode,
    NodeContent,
    NodeMetadata,
)
from app.services.remediation_planner import RemediationPolicy, plan_remediations
from app.services.remediators import execute_plans


def main() -> int:
    document = DocumentNode(
        id="doc-1",
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(language="en", source_format="pdf"),
        children=[],
        accessibility_flags=[AccessibilityFlag.from_code(AccessibilityFlagCode.DOCUMENT_TITLE_MISSING)],
    )
    tree = AccessibilityTree(root=document)

    before_title = document.metadata.properties.get("title")
    run_analyzers(tree, get_default_analyzers())
    policy = RemediationPolicy(
        allow_auto_actions=True,
        allow_ai_actions=False,
        require_human_review_for_all=False,
        allowed_action_codes=[ActionCode.SET_DOCUMENT_TITLE],
    )
    plans = [plan for plan in plan_remediations(tree, policy) if plan.target_node_id == "doc-1"]
    results = execute_plans(tree, plans)
    after_title = document.metadata.properties.get("title")

    print("Results:", results)
    print("Title before:", before_title)
    print("Title after:", after_title)

    if after_title != "Untitled Document":
        return 1
    for result in results:
        if result.action_code.value == "SET_DOCUMENT_TITLE" and result.status.value == "success":
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
