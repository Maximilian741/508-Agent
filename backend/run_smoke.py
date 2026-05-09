"""Top-level smoke runner.

Discovers every ``smoke_*.py`` under ``app/devtools/``, runs each as a
subprocess with a fresh interpreter (so they don't trample each other's
``DATABASE_URL`` or stripe env), and prints a summary table.

Exit code is 0 only if every smoke passes.

Usage:
    cd backend
    python run_smoke.py
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import time


HERE = os.path.dirname(os.path.abspath(__file__))
DEVTOOLS_DIR = os.path.join(HERE, "app", "devtools")


def _discover() -> list[str]:
    """Return module names like ``app.devtools.smoke_e2e`` in stable order."""
    paths = sorted(glob.glob(os.path.join(DEVTOOLS_DIR, "smoke_*.py")))
    out: list[str] = []
    for p in paths:
        name = os.path.splitext(os.path.basename(p))[0]
        out.append(f"app.devtools.{name}")
    return out


def _run_one(module: str) -> tuple[int, float, str]:
    """Run one smoke as a child python process. Return (rc, secs, tail)."""
    started = time.time()
    proc = subprocess.run(
        [sys.executable, "-m", module],
        cwd=HERE,
        capture_output=True,
        text=True,
    )
    elapsed = time.time() - started
    tail = (proc.stdout or "") + (proc.stderr or "")
    tail_lines = [ln for ln in tail.splitlines() if ln.strip()]
    last = tail_lines[-1] if tail_lines else ""
    return proc.returncode, elapsed, last


def _print_table(rows: list[tuple[str, str, float, str]]) -> None:
    name_w = max((len(r[0]) for r in rows), default=20)
    name_w = max(name_w, len("smoke"))
    header = f"  {'STATUS':<6}  {'TIME':>6}  {'SMOKE':<{name_w}}  LAST LINE"
    sep = "  " + "-" * (6 + 2 + 6 + 2 + name_w + 2 + 40)
    print()
    print(header)
    print(sep)
    for name, status, secs, last in rows:
        print(
            f"  {status:<6}  {secs:>5.1f}s  {name:<{name_w}}  {last[:80]}"
        )
    print(sep)


def main() -> int:
    modules = _discover()
    if not modules:
        print("no smoke_*.py files found under app/devtools/")
        return 1

    print(f"discovered {len(modules)} smoke module(s); running...")
    rows: list[tuple[str, str, float, str]] = []
    passed = 0
    failed = 0

    for module in modules:
        short = module.split(".")[-1]
        print(f"\n>>> {short}")
        rc, elapsed, last = _run_one(module)
        if rc == 0:
            passed += 1
            rows.append((short, "PASS", elapsed, last))
        else:
            failed += 1
            rows.append((short, "FAIL", elapsed, last))

    _print_table(rows)
    print(
        f"\nTotals: passed={passed} failed={failed} "
        f"of {len(modules)} smoke(s)"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
