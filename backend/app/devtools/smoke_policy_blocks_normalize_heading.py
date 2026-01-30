"""Smoke test for policy blocking heading normalization.

Run with:
python -m app.devtools.smoke_policy_blocks_normalize_heading
"""

from __future__ import annotations

import sys

from app.analyzers import get_default_analyzers, run_analyzers
from app.devtools.test_fixtures import heading_level_jump_tree
from app.services.remediation_planner import RemediationPolicy, plan_remediations
from app.services.remediators import execute_plans


def main() -> int:
    tree, _heading_one, heading_jump = heading_level_jump_tree()

    before_level = heading_jump.level
    run_analyzers(tree, get_default_analyzers())
    policy = RemediationPolicy(
        allow_auto_actions=False,
        allow_ai_actions=False,
        require_human_review_for_all=False,
        allowed_action_codes=None,
    )
    plans = [plan for plan in plan_remediations(tree, policy) if plan.target_node_id == "h2"]
    results = execute_plans(tree, plans)
    after_level = heading_jump.level

    print("Results:", results)
    print("Heading level before:", before_level)
    print("Heading level after:", after_level)

    if before_level != 4:
        return 1
    if after_level != 4:
        return 1
    for result in results:
        if result.action_code.value == "NORMALIZE_HEADING_LEVEL" and result.status.value == "success":
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
