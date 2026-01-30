"""Smoke test for dispatcher messages containing no newlines.

Run with:
python -m app.devtools.smoke_dispatcher_messages_single_line
"""

from __future__ import annotations

import sys

import app.devtools.smoke_dispatcher_messages_single_line as self_mod
import app.services.remediators.dispatcher as disp_mod
from app.models.accessibility import (
    AccessibilityFlag,
    AccessibilityFlagCode,
    ActionCode,
    NodeType,
    RemediationAction,
)
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.dispatcher import RemediationDispatcher


def main() -> int:
    print("SMOKE FILE:", self_mod.__file__)
    print("DISPATCHER FILE:", disp_mod.__file__)
    print("SMOKE VERSION: 2026-01-30A")
    action = RemediationAction(
        action_code=ActionCode.SET_DOCUMENT_TITLE,
        description="Set document title metadata.",
        requires_ai=False,
        requires_human_review=False,
        is_auto_applicable=True,
        supported_node_types=[NodeType.DOCUMENT],
        related_flag_code=AccessibilityFlagCode.DOCUMENT_TITLE_MISSING,
    )
    flag = AccessibilityFlag.from_code(AccessibilityFlagCode.DOCUMENT_TITLE_MISSING)
    plan_allowed = RemediationPlan(
        flag=flag,
        target_node_id="doc-1",
        actions=[action],
        execution_allowed=True,
    )
    plan_blocked = RemediationPlan(
        flag=flag,
        target_node_id="doc-1",
        actions=[action],
        execution_allowed=False,
    )

    dispatcher = RemediationDispatcher()
    selection_blocked = dispatcher.select(plan_blocked)
    result_allowed = dispatcher._dispatch_plan(plan_allowed)

    print("Selection notes:", selection_blocked.notes)
    print("Execution notes:", result_allowed.notes)
    print("Selection notes repr:", repr(selection_blocked.notes))
    print("Execution notes repr:", repr(result_allowed.notes))
    print("Selection unicode_escape:", selection_blocked.notes.encode("unicode_escape"))
    print("Execution unicode_escape:", result_allowed.notes.encode("unicode_escape"))
    print("Selection notes len:", len(selection_blocked.notes))
    print("Execution notes len:", len(result_allowed.notes))
    print(
        "Selection codepoints:",
        [ord(c) for c in selection_blocked.notes if c in "\n\r\t"],
    )
    print(
        "Execution codepoints:",
        [ord(c) for c in result_allowed.notes if c in "\n\r\t"],
    )
    print("Selection splitlines:", selection_blocked.notes.splitlines())
    print("Execution splitlines:", result_allowed.notes.splitlines())

    def assert_clean(label: str, value: str) -> None:
        bad = []
        for ch, name in [("\n", "\\n"), ("\r", "\\r"), ("\t", "\\t")]:
            if ch in value:
                bad.append(name)
        if bad:
            raise AssertionError(f"{label} contains {', '.join(bad)}: {repr(value)}")

    assert_clean("selection_blocked.notes", selection_blocked.notes)
    assert_clean("result_allowed.notes", result_allowed.notes)
    assert b"\\n" not in selection_blocked.notes.encode("unicode_escape"), selection_blocked.notes.encode("unicode_escape")
    assert b"\\n" not in result_allowed.notes.encode("unicode_escape"), result_allowed.notes.encode("unicode_escape")
    assert len(selection_blocked.notes.splitlines()) == 1, selection_blocked.notes.encode("unicode_escape")
    assert len(result_allowed.notes.splitlines()) == 1, result_allowed.notes.encode("unicode_escape")
    assert "  " not in selection_blocked.notes, repr(selection_blocked.notes)
    assert "  " not in result_allowed.notes, repr(result_allowed.notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
