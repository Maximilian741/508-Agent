"""Smoke test: verify all expected security headers are set on /healthz.

Run directly:
    python -m app.devtools.smoke_security_headers
"""

from __future__ import annotations

import datetime as _dt
import sys

# Compat shim: datetime.UTC is 3.11+. The codebase targets 3.13, but this
# smoke should still run on older interpreters used in CI/sandboxes.
if not hasattr(_dt, "UTC"):
    _dt.UTC = _dt.timezone.utc  # type: ignore[attr-defined]

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


_REQUIRED_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "img-src 'self' data: blob:; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'"
    ),
    "Strict-Transport-Security": (
        "max-age=63072000; includeSubDomains; preload"
    ),
}


def main() -> int:
    client = TestClient(app)
    response = client.get("/healthz")
    if response.status_code != 200:
        print(
            f"[FAIL] /healthz returned {response.status_code}: {response.text!r}"
        )
        return 1

    failed = []
    for header, expected in _REQUIRED_HEADERS.items():
        actual = response.headers.get(header)
        if actual is None:
            failed.append(f"{header}: MISSING")
        elif actual != expected:
            failed.append(
                f"{header}: expected={expected!r} actual={actual!r}"
            )
        else:
            print(f"[ok] {header}: {actual}")

    if failed:
        print("[FAIL] security headers smoke failed:")
        for line in failed:
            print(f"  - {line}")
        return 1

    print("[pass] all security headers present and correct")
    return 0


if __name__ == "__main__":
    sys.exit(main())
