"""Smoke test for dispatcher preferring real action over manual review.

Run with:
python -m app.devtools.smoke_dispatcher_prefers_real_action_over_manual
"""

from __future__ import annotations

import sys

from app.analyzers import get_default_analyzers, run_analyzers
from app.devtools.test_fixtures import document_title_missing_tree
from app.models.accessibility import ContentKind, HeadingNode, NodeContent, NodeMetadata
from app.services.remediation_planner import RemediationPolicy, plan_remediations
from app.services.remediators import execute_plans


def main() -> int:
    tree, document = document_title_missing_tree()
    # Give the document a heading so a REAL title is derivable. This smoke is
    # about the DISPATCHER (it must run SET_DOCUMENT_TITLE rather than fall
    # through to FLAG_FOR_MANUAL_REVIEW), not about the executor's output;
    # with the bare fixture the executor now — correctly — refuses to write
    # the "Untitled Document" placeholder, which this smoke used to demand.
    document.children.append(
        HeadingNode(
            id="h-1",
            level=1,
            content=NodeContent(kind=ContentKind.TEXT, text="Regional Budget Review"),
            metadata=NodeMetadata(page=1, source_format="pdf"),
            children=[],
            accessibility_flags=[],
        )
    )

    before_title = document.metadata.properties.get("title")
    run_analyzers(tree, get_default_analyzers())
    policy = RemediationPolicy(
        allow_auto_actions=True,
        allow_ai_actions=False,
        require_human_review_for_all=False,
        allowed_action_codes=None,
    )
    plans = [plan for plan in plan_remediations(tree, policy) if plan.target_node_id == "doc-1"]
    results = execute_plans(tree, plans)
    after_title = document.metadata.properties.get("title")

    print("Results:", results)
    print("Title before:", before_title)
    print("Title after:", after_title)

    if after_title != "Regional Budget Review":
        return 1
    has_title_success = any(
        result.action_code.value == "SET_DOCUMENT_TITLE" and result.status.value == "success"
        for result in results
    )
    has_manual_success = any(
        result.action_code.value == "FLAG_FOR_MANUAL_REVIEW" and result.status.value == "success"
        for result in results
    )
    if not has_title_success:
        return 1
    if has_manual_success:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
