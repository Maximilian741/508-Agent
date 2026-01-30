"""Smoke test for policy blocking auto-applicable actions.

Run with:
python -m app.devtools.smoke_policy_blocks_auto_actions
"""

from __future__ import annotations

import sys

from app.analyzers import get_default_analyzers, run_analyzers
from app.devtools.test_fixtures import decorative_image_with_alt_flag_tree
from app.services.remediation_planner import RemediationPolicy, plan_remediations
from app.services.remediators import execute_plans


def main() -> int:
    tree, image_node = decorative_image_with_alt_flag_tree()

    before_alt = image_node.alt_text
    run_analyzers(tree, get_default_analyzers())
    policy = RemediationPolicy(
        allow_auto_actions=False,
        allow_ai_actions=False,
        require_human_review_for_all=False,
        allowed_action_codes=None,
    )
    plans = [plan for plan in plan_remediations(tree, policy) if plan.target_node_id == "img-1"]
    results = execute_plans(tree, plans)
    after_alt = image_node.alt_text

    print("Results:", results)
    print("Alt text before:", before_alt)
    print("Alt text after:", after_alt)

    if before_alt != "decorative flourish":
        return 1
    if after_alt != "decorative flourish":
        return 1
    for result in results:
        if result.action_code.value == "REMOVE_DECORATIVE_ALT_TEXT" and result.status.value == "success":
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
