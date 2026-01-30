"""Run all devtools smoke tests in a single command.

Run with:
python -m app.devtools.run_smoke_suite
"""

from __future__ import annotations

import importlib
import sys


SMOKE_MODULES = [
    "app.devtools.smoke_remove_decorative_alt",
    "app.devtools.smoke_non_decorative_image_preserves_alt",
    "app.devtools.smoke_policy_blocks_auto_actions",
    "app.devtools.smoke_normalize_heading_level_changes",
    "app.devtools.smoke_policy_blocks_normalize_heading",
    "app.devtools.smoke_fix_list_structure_changes",
    "app.devtools.smoke_policy_blocks_fix_list_structure",
    "app.devtools.smoke_manual_review_executor_runs",
    "app.devtools.smoke_set_document_title_changes",
    "app.devtools.smoke_policy_blocks_set_document_title",
    "app.devtools.smoke_dispatcher_prefers_real_action_over_manual",
    "app.devtools.smoke_dispatcher_messages_single_line",
]


def main() -> int:
    passed = 0
    failed = 0
    for module_name in SMOKE_MODULES:
        test_name = module_name.split(".")[-1]
        try:
            module = importlib.import_module(module_name)
            main = getattr(module, "main", None)
            if not callable(main):
                print(f"FAIL  {test_name}  (Missing callable main)")
                failed += 1
                continue
            result = main()
        except Exception as exc:
            print(f"FAIL  {test_name}  ({exc.__class__.__name__}: {exc})")
            failed += 1
            continue
        if result == 0:
            print(f"PASS  {test_name}")
            passed += 1
        else:
            print(f"FAIL  {test_name}")
            failed += 1
    print(f"Totals: passed={passed}, failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
