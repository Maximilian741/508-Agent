"""Smoke test for manual review executor success.

Run with:
python -m app.devtools.smoke_manual_review_executor_runs
"""

from __future__ import annotations

import sys

from app.analyzers import get_default_analyzers, run_analyzers
from app.devtools.test_fixtures import invalid_list_structure_tree
from app.services.remediation_planner import RemediationPolicy, plan_remediations
from app.services.remediators import execute_plans


def main() -> int:
    tree, _list_node = invalid_list_structure_tree()

    run_analyzers(tree, get_default_analyzers())
    policy = RemediationPolicy(
        allow_auto_actions=False,
        allow_ai_actions=False,
        require_human_review_for_all=False,
        allowed_action_codes=None,
    )
    plans = [plan for plan in plan_remediations(tree, policy) if plan.target_node_id == "list-1"]
    results = execute_plans(tree, plans)

    print("Results:", results)

    for result in results:
        if result.action_code.value == "FLAG_FOR_MANUAL_REVIEW" and result.status.value == "success":
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
