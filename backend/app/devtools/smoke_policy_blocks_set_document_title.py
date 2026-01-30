"""Smoke test for policy blocking document title setting.

Run with:
python -m app.devtools.smoke_policy_blocks_set_document_title
"""

from __future__ import annotations

import sys

from app.analyzers import get_default_analyzers, run_analyzers
from app.devtools.test_fixtures import document_title_missing_tree
from app.services.remediation_planner import RemediationPolicy, plan_remediations
from app.services.remediators import execute_plans


def main() -> int:
    tree, document = document_title_missing_tree()

    before_title = document.metadata.properties.get("title")
    run_analyzers(tree, get_default_analyzers())
    policy = RemediationPolicy(
        allow_auto_actions=False,
        allow_ai_actions=False,
        require_human_review_for_all=True,
        allowed_action_codes=[],
    )
    plans = [plan for plan in plan_remediations(tree, policy) if plan.target_node_id == "doc-1"]
    results = execute_plans(tree, plans)
    after_title = document.metadata.properties.get("title")

    print("Results:", results)
    print("Title before:", before_title)
    print("Title after:", after_title)

    if after_title != before_title:
        return 1
    has_manual_success = any(
        result.action_code.value == "FLAG_FOR_MANUAL_REVIEW" and result.status.value == "success"
        for result in results
    )
    has_title_success = any(
        result.action_code.value == "SET_DOCUMENT_TITLE" and result.status.value == "success"
        for result in results
    )
    if not has_manual_success:
        return 1
    if has_title_success:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
