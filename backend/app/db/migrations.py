from __future__ import annotations

import os
import subprocess
from pathlib import Path


def run_migrations() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    alembic_ini = backend_root / "alembic.ini"
    if not alembic_ini.exists():
        return
    env = dict(os.environ)
    env.setdefault("DATABASE_URL", os.getenv("DATABASE_URL", "sqlite:///./.runtime/508_agent.db"))
    is_postgres = str(env.get("DATABASE_URL", "")).startswith("postgres")
    try:
        subprocess.run(
            ["alembic", "-c", str(alembic_ini), "upgrade", "head"],
            cwd=str(backend_root),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        if is_postgres:
            raise RuntimeError(f"Failed to run Alembic migrations for Postgres: {exc}") from exc
        # Keep sqlite development startup resilient.
        return
