"""Smoke test for fixing list structure.

Run with:
python -m app.devtools.smoke_fix_list_structure_changes
"""

from __future__ import annotations

import sys

from app.analyzers import get_default_analyzers, run_analyzers
from app.devtools.test_fixtures import invalid_list_structure_tree
from app.models.accessibility import ListItemNode
from app.services.remediation_planner import RemediationPolicy, plan_remediations
from app.services.remediators import execute_plans


def _child_summary(list_node) -> str:
    return ",".join([f"{child.node_type.value}:{child.id}" for child in list_node.children])


def main() -> int:
    tree, list_node = invalid_list_structure_tree()
    before_summary = _child_summary(list_node)

    run_analyzers(tree, get_default_analyzers())
    policy = RemediationPolicy(
        allow_auto_actions=True,
        allow_ai_actions=False,
        require_human_review_for_all=False,
        allowed_action_codes=None,
    )
    plans = [plan for plan in plan_remediations(tree, policy) if plan.target_node_id == "list-1"]
    results = execute_plans(tree, plans)
    after_summary = _child_summary(list_node)

    print("Results:", results)
    print("List children before:", before_summary)
    print("List children after:", after_summary)

    all_list_items = all(isinstance(child, ListItemNode) for child in list_node.children)
    if not all_list_items:
        return 1
    for result in results:
        if result.action_code.value == "FIX_LIST_STRUCTURE" and result.status.value == "success":
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
