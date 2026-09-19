"""Smoke: a hostile session cannot block the owner's password recovery.

Verification links and password-reset links share one table
(``email_verify_tokens``); reset tokens are namespaced with ``pr_``.
``POST /auth/request-verify-email`` used to delete EVERY row for the user, so
whoever held a borrowed session could not take the account (that needs the
password) but COULD fire that always-200 endpoint after each of the owner's
reset requests and shred the link before it was clicked. Reset is the recovery
path for someone who has forgotten their password, so the owner had no way out
and the attacker could loop it forever.

Pins:

1. request-verify-email leaves a pending ``pr_`` reset token usable — the owner
   completes the reset and the hostile session dies with it.
2. It still does what it is for: it replaces this user's OWN stale verification
   token, and the link it mints verifies the address.
3. The attacker can repeat the attack; recovery still completes.
4. Neither token kind can be redeemed by the other endpoint, and a reset
   request still clears only the previous reset token.
5. Reset-email requests are throttled per address, so the endpoint is not an
   inbox-flood weapon aimed at someone else.

Usage:
    python -m app.devtools.smoke_recovery_not_blockable
"""

from __future__ import annotations

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="508_smoke_recovery_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/r.db"
os.environ.pop("SMTP_HOST", None)
# One rate-limit bucket per section; only read when a proxy is declared.
os.environ["TRUST_PROXY_HEADERS"] = "true"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db.models import EmailVerifyTokenRow  # noqa: E402
from app.db.session_sqlalchemy import session_scope  # noqa: E402
from app.main import app  # noqa: E402


def _tokens(user_id: str) -> list:
    with session_scope() as s:
        return list(
            s.execute(
                select(EmailVerifyTokenRow.token).where(EmailVerifyTokenRow.user_id == user_id)
            ).scalars().all()
        )


def _reset_token(user_id: str):
    return next((t for t in _tokens(user_id) if t.startswith("pr_")), None)


def _verify_token(user_id: str):
    return next((t for t in _tokens(user_id) if not t.startswith("pr_")), None)


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, detail: object = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, "" if cond else f"  [{detail}]")
        if not cond:
            failures += 1

    def client(ip: str) -> TestClient:
        return TestClient(app, headers={"X-Forwarded-For": ip})

    c = client("10.7.0.1")
    EMAIL = "hostage@example.com"
    r = c.post("/auth/sign-in", json={"email": EMAIL, "password": "owner-pass-1"})
    stolen = {"Authorization": f"Bearer {r.json()['token']}"}
    uid = r.json()["user"]["id"]

    check("the borrowed session is live to start with", c.get("/auth/me", headers=stolen).status_code == 200)
    # The takeover routes are already closed; this smoke is about what is left.
    check(
        "borrowed session cannot set a password without the current one",
        c.post("/auth/set-password", headers=stolen, json={"password": "attacker-pw1"}).status_code == 403,
    )

    # --- 1. the attack, three times ------------------------------------------
    passwords = ["owner-pass-2", "owner-pass-3", "owner-pass-4"]
    for i, new_pw in enumerate(passwords):
        c2 = client(f"10.7.1.{i}")
        c2.post("/auth/request-password-reset", json={"email": EMAIL})
        link = _reset_token(uid)
        check(f"round {i + 1}: the owner has a pr_ reset link", bool(link), link)
        attack = c2.post("/auth/request-verify-email", headers=stolen)
        # Round 1 is the real test: the borrowed session is still live and the
        # endpoint answers 200 for it. After the first reset the session is
        # revoked, so later rounds are 401 — recovery must still work anyway.
        expected = 200 if i == 0 else 401
        check(
            f"round {i + 1}: the attacker's request-verify-email -> {expected}",
            attack.status_code == expected,
            attack.status_code,
        )
        check(f"round {i + 1}: the reset link SURVIVES it", _reset_token(uid) == link, _reset_token(uid))
        r = c2.post("/auth/reset-password", json={"token": link, "password": new_pw})
        check(f"round {i + 1}: the owner completes the reset", r.status_code == 200, r.text)
        if i == 0:
            check("the hostile session dies with the reset", c2.get("/auth/me", headers=stolen).status_code == 401)
    c3 = client("10.7.0.9")
    check(
        "the owner ends up in control with the password they chose",
        c3.post("/auth/sign-in", json={"email": EMAIL, "password": passwords[-1]}).status_code == 200,
    )

    # --- 2. request-verify-email still does its own job ----------------------
    c = client("10.7.0.2")
    r = c.post("/auth/sign-in", json={"email": "prover@example.com", "password": "prover-pass-1"})
    auth = {"Authorization": f"Bearer {r.json()['token']}"}
    pid = r.json()["user"]["id"]
    c.post("/auth/request-verify-email", headers=auth)
    stale = _verify_token(pid)
    c.post("/auth/request-verify-email", headers=auth)
    fresh = _verify_token(pid)
    check("a second request replaces the stale verification token", stale != fresh and fresh is not None, (stale, fresh))
    check("only one verification token is outstanding", len([t for t in _tokens(pid) if not t.startswith("pr_")]) == 1, _tokens(pid))
    check("the stale link is dead", c.get("/auth/verify-email", params={"token": stale}).status_code == 410)
    check("the fresh link verifies", c.get("/auth/verify-email", params={"token": fresh}).status_code == 200)
    check("the account reads as verified", c.get("/auth/me", headers=auth).json().get("emailVerifiedAt") is not None)

    # --- 3. the two kinds never cross ----------------------------------------
    c = client("10.7.0.3")
    c.post("/auth/request-password-reset", json={"email": "prover@example.com"})
    reset = _reset_token(pid)
    c.post("/auth/request-verify-email", headers=auth)
    verify = _verify_token(pid)
    check("both kinds coexist for one user", bool(reset) and bool(verify) and reset != verify, (reset, verify))
    check("a reset token cannot verify an email", c.get("/auth/verify-email", params={"token": reset}).status_code == 410)
    r = c.post("/auth/reset-password", json={"token": verify, "password": "wrong-kind-1"})
    check("a verification token cannot reset a password", r.status_code == 410, r.status_code)
    check("neither attempt consumed the other's row", _reset_token(pid) == reset and _verify_token(pid) == verify)

    # A NEW reset request still invalidates the previous reset token only.
    c.post("/auth/request-password-reset", json={"email": "prover@example.com"})
    check("a new reset request replaces the old reset token", _reset_token(pid) not in (None, reset))
    check("...and leaves the verification token alone", _verify_token(pid) == verify)

    # --- 4. reset mail is throttled per address ------------------------------
    c = client("10.7.0.4")
    codes = [
        c.post("/auth/request-password-reset", json={"email": "floodme@example.com"}).status_code
        for _ in range(9)
    ]
    check("repeated reset requests for one address are throttled", 429 in codes, codes)
    check(
        "a different address is not caught by it",
        c.post("/auth/request-password-reset", json={"email": "someone-else@example.com"}).status_code == 200,
    )

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
