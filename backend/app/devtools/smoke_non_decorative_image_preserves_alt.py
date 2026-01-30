"""Smoke test for non-decorative images preserving alt text.

Run with:
python -m app.devtools.smoke_non_decorative_image_preserves_alt
"""

from __future__ import annotations

import sys

from app.analyzers import get_default_analyzers, run_analyzers
from app.models.accessibility import (
    AccessibilityTree,
    ContentKind,
    DocumentNode,
    ImageNode,
    NodeContent,
    NodeMetadata,
)
from app.services.remediation_planner import RemediationPolicy, plan_remediations
from app.services.remediators import execute_plans


def main() -> int:
    image_node = ImageNode(
        id="img-1",
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(page=1, source_format="pdf"),
        children=[],
        accessibility_flags=[],
        is_decorative=False,
        alt_text="meaningful description",
    )
    tree = AccessibilityTree(
        root=DocumentNode(
            id="doc-1",
            content=NodeContent(kind=ContentKind.NONE),
            metadata=NodeMetadata(language="en", source_format="pdf"),
            children=[image_node],
            accessibility_flags=[],
        )
    )

    before_alt = image_node.alt_text
    run_analyzers(tree, get_default_analyzers())
    policy = RemediationPolicy(
        allow_auto_actions=True,
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

    if before_alt != "meaningful description":
        return 1
    if after_alt != "meaningful description":
        return 1
    for result in results:
        if result.action_code.value == "REMOVE_DECORATIVE_ALT_TEXT" and result.status.value == "success":
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
