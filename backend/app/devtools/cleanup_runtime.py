from __future__ import annotations

import argparse
from pathlib import Path

from app.config import get_settings
from app.storage.materialize import cleanup_materialized_scope


def main() -> int:
    parser = argparse.ArgumentParser(description="Cleanup runtime materialized artifacts.")
    parser.add_argument("--scope", default="", help="Optional materialized scope to remove")
    args = parser.parse_args()

    settings = get_settings()
    root = settings.materialized_root
    root.mkdir(parents=True, exist_ok=True)
    if args.scope:
        cleanup_materialized_scope(root, args.scope)
        print(f"[cleanup] removed scope={args.scope}")
        return 0
    removed = 0
    for child in root.iterdir():
        if child.is_dir():
            cleanup_materialized_scope(root, child.name)
            removed += 1
    print(f"[cleanup] removed_scopes={removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

