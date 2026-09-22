"""Smoke: taking over an account needs more than a stolen session.

Sessions can now be revoked (token_version) and a password reset kills API
keys — but nothing re-authenticated the two changes that TAKE the account:

* ``PATCH /auth/me`` could move the email. Future reset links then go to the
  attacker's inbox, so the real owner cannot recover — the session being
  revoked afterwards does not help, because the attacker holds the new token.
* ``POST /auth/set-password`` could replace an existing password outright,
  locking the owner out.

Both now require ``currentPassword`` when the account HAS a password. Setting
a FIRST password still doesn't: there is no credential to prove and no owner
to lock out. A name-only profile edit is not an identity change and stays
friction-free.

Pins:
1. Name-only PATCH needs no password.
2. Email change: missing password -> 403 current_password_required; wrong ->
   403 invalid_current_password; the email does not move in either case.
3. Email change with the right password succeeds, revokes API keys (they
   outlive session tokens) and rotates the session: the old token is dead,
   the returned one works.
4. set-password on an account WITH a password: missing/wrong -> 403; right ->
   200, and the returned token works.
5. set-password on a passwordless account: no currentPassword needed.

Usage:
    python -m app.devtools.smoke_reauth_required
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

_TMP = tempfile.mkdtemp(prefix="508_smoke_reauth_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/s.db"
os.environ.pop("SMTP_HOST", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.db.models import UserRow  # noqa: E402
from app.db.session_sqlalchemy import session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.security.sessions import mint_session  # noqa: E402

PASSWORD = "correct-horse-battery"
FAILURES: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}{(' - ' + detail) if detail else ''}")
        FAILURES.append(label)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    client = TestClient(app)

    res = client.post(
        "/auth/sign-in",
        json={"email": "owner@example.com", "password": PASSWORD, "displayName": "Owner"},
    )
    if res.status_code != 200:
        print(f"  FAIL could not create the account: {res.status_code} {res.text[:200]}")
        return 1
    token = res.json()["token"]

    # 1. A name-only change is not an identity change.
    res = client.patch("/auth/me", json={"displayName": "Owner Two"}, headers=_auth(token))
    check("name-only PATCH needs no password", res.status_code == 200, f"{res.status_code} {res.text[:120]}")

    # An API key, to prove an email change ends it too.
    res = client.post("/api-keys", json={"name": "ci"}, headers=_auth(token))
    check("minted an API key", res.status_code == 200, f"{res.status_code} {res.text[:120]}")

    # 2. The email cannot move without the current password.
    res = client.patch("/auth/me", json={"email": "attacker@example.com"}, headers=_auth(token))
    check(
        "email change without the current password is refused",
        res.status_code == 403 and res.json().get("detail") == "current_password_required",
        f"{res.status_code} {res.text[:160]}",
    )

    res = client.patch(
        "/auth/me",
        json={"email": "attacker@example.com", "currentPassword": "not-the-password"},
        headers=_auth(token),
    )
    check(
        "email change with the wrong password is refused",
        res.status_code == 403 and res.json().get("detail") == "invalid_current_password",
        f"{res.status_code} {res.text[:160]}",
    )

    res = client.get("/auth/me", headers=_auth(token))
    check(
        "the email did not move",
        res.status_code == 200 and res.json().get("email") == "owner@example.com",
        res.text[:160],
    )

    # 3. With the real password it goes through, and ends every credential.
    res = client.patch(
        "/auth/me",
        json={"email": "new-owner@example.com", "currentPassword": PASSWORD},
        headers=_auth(token),
    )
    body = res.json() if res.status_code == 200 else {}
    check("email change with the right password succeeds", res.status_code == 200, f"{res.status_code} {res.text[:160]}")
    check("the new email is stored", body.get("email") == "new-owner@example.com", str(body)[:160])
    check("API keys were revoked with it", int(body.get("apiKeysRevoked") or 0) >= 1, str(body)[:160])

    rotated = body.get("token")
    check("a fresh token came back", bool(rotated), str(body)[:160])
    res = client.get("/auth/me", headers=_auth(token))
    check("the pre-change token is dead", res.status_code == 401, f"{res.status_code} {res.text[:120]}")
    if rotated:
        res = client.get("/auth/me", headers=_auth(rotated))
        check("the rotated token works", res.status_code == 200, f"{res.status_code} {res.text[:120]}")
        token = rotated

    # 4. Replacing an existing password needs the old one.
    res = client.post("/auth/set-password", json={"password": "attacker-new-pass"}, headers=_auth(token))
    check(
        "set-password without the current password is refused",
        res.status_code == 403 and res.json().get("detail") == "current_password_required",
        f"{res.status_code} {res.text[:160]}",
    )

    res = client.post(
        "/auth/set-password",
        json={"password": "attacker-new-pass", "currentPassword": "not-the-password"},
        headers=_auth(token),
    )
    check(
        "set-password with the wrong current password is refused",
        res.status_code == 403 and res.json().get("detail") == "invalid_current_password",
        f"{res.status_code} {res.text[:160]}",
    )

    res = client.post(
        "/auth/set-password",
        json={"password": "a-brand-new-password", "currentPassword": PASSWORD},
        headers=_auth(token),
    )
    check("set-password with the right current password succeeds", res.status_code == 200, f"{res.status_code} {res.text[:160]}")
    new_token = (res.json() or {}).get("token") if res.status_code == 200 else None
    if new_token:
        res = client.get("/auth/me", headers=_auth(new_token))
        check("the token returned by set-password works", res.status_code == 200, f"{res.status_code}")

    # 5. A passwordless account can still set its FIRST password.
    with session_scope() as session:
        session.add(
            UserRow(
                id="legacy-user-1",
                email="legacy@example.com",
                display_name="Legacy",
                created_at=datetime.utcnow(),
            )
        )
    legacy_token = mint_session("legacy-user-1", version=0)
    res = client.post("/auth/set-password", json={"password": "first-password-ever"}, headers=_auth(legacy_token))
    check(
        "a passwordless account can set its first password",
        res.status_code == 200,
        f"{res.status_code} {res.text[:160]}",
    )

    print()
    if FAILURES:
        print(f"SMOKE REAUTH REQUIRED: FAIL ({len(FAILURES)} check(s))")
        return 1
    print("SMOKE REAUTH REQUIRED: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
