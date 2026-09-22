"""Smoke: a client-supplied header cannot lift the brute-force rate limit.

The per-IP limiter on /auth is the front line against password guessing, and it
is only as good as the address it keys on. It used to read
``X-Forwarded-For.split(",")[0]`` — the leftmost entry, which is whatever the
CLIENT typed — with ``TRUST_PROXY_HEADERS`` defaulting to TRUE. Rotating one
header per request therefore bought a fresh bucket every time: 130 wrong
passwords, zero 429s, and a request carrying the CORRECT password sailed
through with 200 at a moment when the real source IP was hard-limited.

Pins:

1. The setting fails CLOSED: with nothing configured, forwarded-IP headers are
   ignored entirely, so a spoofed X-Forwarded-For / CF-Connecting-IP cannot
   lift the limit end-to-end.
2. When the operator DOES declare a proxy, the address comes from the right
   place: CF-Connecting-IP first (Cloudflare overwrites it), otherwise the
   RIGHTMOST X-Forwarded-For hop (the peer our own proxy actually saw, which
   nginx's $proxy_add_x_forwarded_for appends). A forged leftmost value is
   ignored, and two genuinely distinct clients still get separate buckets.
3. Localhost development keeps working: with no proxy and no headers, the
   direct peer is the key.
4. Password guessing is bounded per ACCOUNT as well, so it stays bounded even
   where the source address is unknowable (botnet, NAT, or a proxy deployment
   with the header untrusted). A correct password clears the cool-off, so the
   owner is not locked out by someone else's failures.

Usage:
    python -m app.devtools.smoke_client_ip_trust
"""

from __future__ import annotations

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="508_smoke_clientip_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/ip.db"
os.environ.pop("SMTP_HOST", None)
# Deliberately NOT setting TRUST_PROXY_HEADERS: this smoke pins the SHIPPED
# default, which must be fail-closed.
os.environ.pop("TRUST_PROXY_HEADERS", None)

from collections import Counter  # noqa: E402

from fastapi import Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.security.rate_limit import CredentialThrottle, RateLimitMiddleware  # noqa: E402


def _request(headers: dict, peer: str = "198.51.100.1") -> Request:
    """A minimal ASGI scope, enough for _client_ip."""
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/auth/sign-in",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (peer, 51234),
        "query_string": b"",
    })


async def _ok(request):  # pragma: no cover - trivial endpoint for the probe app
    return JSONResponse({"ok": True})


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, detail: object = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, "" if cond else f"  [{detail}]")
        if not cond:
            failures += 1

    # --- 1. the shipped default ---------------------------------------------
    check(
        "TRUST_PROXY_HEADERS defaults to False (fail closed)",
        get_settings().trust_proxy_headers is False,
        get_settings().trust_proxy_headers,
    )

    untrusted = RateLimitMiddleware(app, trust_proxy_headers=False)
    trusted = RateLimitMiddleware(app, trust_proxy_headers=True)

    check(
        "untrusted: a spoofed X-Forwarded-For is ignored",
        untrusted._client_ip(_request({"X-Forwarded-For": "1.2.3.4"})) == "198.51.100.1",
    )
    check(
        "untrusted: a spoofed CF-Connecting-IP is ignored",
        untrusted._client_ip(_request({"CF-Connecting-IP": "1.2.3.4"})) == "198.51.100.1",
    )
    check(
        "localhost dev with no headers keys on the direct peer",
        untrusted._client_ip(_request({}, peer="127.0.0.1")) == "127.0.0.1",
    )

    # --- 2. what a trusted proxy actually means ------------------------------
    check(
        "trusted: CF-Connecting-IP wins (Cloudflare overwrites it)",
        trusted._client_ip(_request({"CF-Connecting-IP": "9.9.9.9", "X-Forwarded-For": "1.2.3.4"})) == "9.9.9.9",
    )
    check(
        "trusted: the RIGHTMOST XFF hop is used, not the client's leftmost",
        trusted._client_ip(_request({"X-Forwarded-For": "1.2.3.4, 203.0.113.9"})) == "203.0.113.9",
    )
    check(
        "trusted: a single-hop XFF is that hop",
        trusted._client_ip(_request({"X-Forwarded-For": "203.0.113.9"})) == "203.0.113.9",
    )
    check(
        "trusted: an empty/garbage XFF falls back to the peer",
        trusted._client_ip(_request({"X-Forwarded-For": " , "})) == "198.51.100.1",
    )
    check(
        "trusted: no headers at all falls back to the peer",
        trusted._client_ip(_request({})) == "198.51.100.1",
    )

    # --- 3. per-account throttle --------------------------------------------
    t = CredentialThrottle(failures=3, window_seconds=60.0, cool_off_seconds=30.0)
    first = [t.retry_after("victim@example.com") for _ in range(3)]
    for _ in range(3):
        t.record_attempt("victim@example.com")
    check("under the threshold nothing is throttled", first == [0, 0, 0], first)
    wait = t.retry_after("victim@example.com")
    check("past the threshold the account is in cool-off", 0 < wait <= 30, wait)
    check("a different account is untouched", t.retry_after("bystander@example.com") == 0)
    t.clear("victim@example.com")
    check("the real owner signing in clears the cool-off", t.retry_after("victim@example.com") == 0)

    # End-to-end, and FIRST, while the per-IP bucket is still far from full
    # (60/min) — so the 429s below can only be the per-account throttle.
    from app.api.auth import _SIGN_IN_THROTTLE

    c0 = TestClient(app)
    assert c0.post("/auth/sign-in", json={"email": "bounded@example.com", "password": "bounded-pass-1"}).status_code == 200
    seen: Counter = Counter()
    for _ in range(14):
        seen[c0.post("/auth/sign-in", json={"email": "bounded@example.com", "password": "nope"}).status_code] += 1
    check("wrong passwords stop being checked past the threshold", seen.get(429, 0) > 0, dict(seen))
    check(
        "the throttle is keyed on the account, not the connection",
        _SIGN_IN_THROTTLE.retry_after("bounded@example.com") > 0,
    )
    check(
        "a bystander account is unaffected while that one cools off",
        c0.post("/auth/sign-in", json={"email": "bystander@example.com", "password": "bystander-pw1"}).status_code == 200,
    )

    # --- 4. end-to-end on the real app, shipped default ----------------------
    c = TestClient(app)
    EMAIL = "brute@example.com"
    r = c.post("/auth/sign-in", json={"email": EMAIL, "password": "real-password-1"})
    assert r.status_code == 200, r.text

    def burst(n: int, header: str | None, base: str) -> dict:
        codes: Counter = Counter()
        for i in range(n):
            hdr = {header: f"{base}{i % 250}"} if header else {}
            codes[c.post("/auth/sign-in", json={"email": EMAIL, "password": "wrong"}, headers=hdr).status_code] += 1
        return dict(codes)

    spoofed = burst(90, "X-Forwarded-For", "203.0.113.")
    check(
        "90 guesses behind a rotating X-Forwarded-For are still limited",
        spoofed.get(429, 0) > 0 and spoofed.get(401, 0) < 90,
        spoofed,
    )
    spoofed_cf = burst(30, "CF-Connecting-IP", "198.51.100.")
    check(
        "...and behind a rotating CF-Connecting-IP",
        spoofed_cf.get(401, 0) == 0,
        spoofed_cf,
    )
    r = c.post(
        "/auth/sign-in",
        json={"email": EMAIL, "password": "real-password-1"},
        headers={"X-Forwarded-For": "203.0.113.251"},
    )
    check(
        "the CORRECT password cannot be smuggled in over a fresh spoofed IP",
        r.status_code == 429,
        r.status_code,
    )

    # --- 5. distinct real clients still get distinct buckets -----------------
    # Proven against the middleware directly: the app under test has the
    # shipped (untrusted) setting baked in at import.
    probe = RateLimitMiddleware(app, limit=3, window_seconds=60.0, trust_proxy_headers=True)
    import time as _time

    now = _time.monotonic()
    a = [probe._check_and_record("10.0.0.1", now) for _ in range(5)]
    b = [probe._check_and_record("10.0.0.2", now) for _ in range(5)]
    check("one client is cut off at its own limit", a == [True, True, True, False, False], a)
    check("a different client is unaffected by it", b == [True, True, True, False, False], b)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
