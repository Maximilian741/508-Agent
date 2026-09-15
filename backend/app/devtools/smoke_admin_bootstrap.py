"""Smoke: nobody becomes admin by typing an email.

Before the fix, admin was a string match on the account email, and the email
was never verified: signing up as an ADMIN_EMAILS address, or PATCHing an
ordinary account onto one, unlocked /admin/metrics, the cross-tenant audit log
and its purge. Pins:

1. Sign-up as a listed address -> an ordinary account (whoami false,
   /admin/metrics, /audit-log and purge all 403).
2. PATCH /auth/me onto a listed address -> 409 email_in_use, even from an
   account with a verified email; any email change clears verification,
   rotates the token, and kills a verify link sent to the old inbox.
3. The ops bootstrap (python -m app.devtools.bootstrap_admin) refuses unlisted
   addresses and short passwords; takes over a squatted listed account (the
   squatter's password and token die) or creates a missing one; the operator
   is then admin.
4. A verify link is no way in: a squatted listed account that the real inbox
   owner verifies by clicking is still not admin (only the bootstrap promotes).
5. Moving an admin account off the listed address drops admin (role reset).

Usage:
    python -m app.devtools.smoke_admin_bootstrap
"""

from __future__ import annotations

import io
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="508_smoke_admin_boot_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/a.db"
os.environ["ADMIN_EMAILS"] = "owner@508agent.test, ops@508agent.test, sec@508agent.test"
os.environ.pop("SMTP_HOST", None)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.main import app  # noqa: E402
from app.db.models import EmailVerifyTokenRow  # noqa: E402
from app.db.session_sqlalchemy import session_scope  # noqa: E402
from app.devtools.bootstrap_admin import (  # noqa: E402
    BootstrapError,
    bootstrap_admin,
    main as bootstrap_main,
)


def main() -> int:
    c = TestClient(app)
    failures = 0

    def check(name: str, cond: bool, detail: object = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, "" if cond else f"  [{detail}]")
        if not cond:
            failures += 1

    def signin(email: str, pw: str):
        return c.post("/auth/sign-in", json={"email": email, "password": pw})

    def h(tok: str) -> dict:
        return {"Authorization": f"Bearer {tok}"}

    def is_admin(tok: str) -> bool:
        return c.get("/api/admin/whoami", headers=h(tok)).json().get("isAdmin") is True

    def mint_verify_link(tok: str, user_id: str) -> str:
        """Request a verify email and read its token (stands in for the inbox)."""
        c.post("/auth/request-verify-email", headers=h(tok))
        with session_scope() as s:
            return s.execute(
                select(EmailVerifyTokenRow.token).where(
                    EmailVerifyTokenRow.user_id == user_id,
                    ~EmailVerifyTokenRow.token.like("pr_%"),
                )
            ).scalars().first()

    def refused(fn, *args) -> bool:
        try:
            fn(*args)
        except BootstrapError:
            return True
        return False

    # --- 1. squatting a listed address at sign-up ---------------------------
    r = signin("owner@508agent.test", "squatter-pass-1")
    check("sign-up as a listed address -> 200 (ordinary account)", r.status_code == 200, r.text)
    squat_tok, squat_id = r.json()["token"], r.json()["user"]["id"]
    check("unverified listed address: whoami isAdmin false", not is_admin(squat_tok))
    check("unverified listed address: /admin/metrics 403", c.get("/admin/metrics", headers=h(squat_tok)).status_code == 403)
    check("unverified listed address: /audit-log 403", c.get("/audit-log", headers=h(squat_tok)).status_code == 403)
    check(
        "unverified listed address: POST /audit-log/purge 403",
        c.post("/audit-log/purge", params={"days": 1}, headers=h(squat_tok)).status_code == 403,
    )

    # --- 2. PATCH onto a listed address -------------------------------------
    r = signin("mallory@example.com", "mallory-pass-1")
    m_tok, m_id = r.json()["token"], r.json()["user"]["id"]
    link = mint_verify_link(m_tok, m_id)
    check("mallory verifies her own inbox", c.get("/auth/verify-email", params={"token": link}).status_code == 200)
    for target in ("ops@508agent.test", "  OPS@508agent.test "):
        r = c.patch("/auth/me", headers=h(m_tok), json={"email": target})
        check(
            f"PATCH email onto listed {target.strip()!r} -> 409 email_in_use",
            r.status_code == 409 and r.json().get("detail") == "email_in_use",
            r.text,
        )
    me = c.get("/auth/me", headers=h(m_tok)).json()
    check("refused PATCH changed nothing", me["email"] == "mallory@example.com" and me["emailVerifiedAt"], me)
    check("mallory is not admin", not is_admin(m_tok))

    old_inbox_link = mint_verify_link(m_tok, m_id)
    r = c.patch("/auth/me", headers=h(m_tok), json={"email": "mallory2@example.com"})
    body = r.json()
    moved_tok = body.get("token")
    check("PATCH onto a free address -> 200 with a fresh token", r.status_code == 200 and bool(moved_tok), r.text)
    check("email change clears emailVerifiedAt", body.get("emailVerifiedAt") is None, body)
    check("email change revokes the token that made it", c.get("/auth/me", headers=h(m_tok)).status_code == 401)
    check("the fresh token works", c.get("/auth/me", headers=h(moved_tok)).status_code == 200)
    check(
        "a verify link sent to the OLD inbox can't verify the new address (410)",
        c.get("/auth/verify-email", params={"token": old_inbox_link}).status_code == 410,
    )

    # --- 3. ops bootstrap -----------------------------------------------------
    check("bootstrap refuses an address not in ADMIN_EMAILS", refused(bootstrap_admin, "mallory2@example.com", "whatever-pass-1"))
    check("bootstrap refuses a short password", refused(bootstrap_admin, "owner@508agent.test", "short"))
    check("refusals changed nothing (squatter's token still live)", c.get("/auth/me", headers=h(squat_tok)).status_code == 200)

    res = bootstrap_admin("owner@508agent.test", "operator-pass-1")
    check("bootstrap takes over the squatted account", res["created"] is False and res["userId"] == squat_id, res)
    check("squatter's token is revoked", c.get("/auth/me", headers=h(squat_tok)).status_code == 401)
    check("squatter's password no longer signs in", signin("owner@508agent.test", "squatter-pass-1").status_code == 401)
    r = signin("owner@508agent.test", "operator-pass-1")
    op_tok = r.json().get("token", "")
    check("operator signs in with the bootstrap password, verified", r.status_code == 200 and r.json()["user"]["emailVerifiedAt"], r.text)
    check("operator: whoami isAdmin true", is_admin(op_tok))
    check("operator: /admin/metrics 200", c.get("/admin/metrics", headers=h(op_tok)).status_code == 200)
    check("operator: /audit-log 200", c.get("/audit-log", headers=h(op_tok)).status_code == 200)

    real_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO("ops-operator-pass-1\n")
        rc = bootstrap_main(["--password-stdin", "ops@508agent.test"])
        check("CLI creates a listed account nobody signed up for (rc 0)", rc == 0, rc)
        sys.stdin = io.StringIO("some-pass-12345\n")
        rc = bootstrap_main(["--password-stdin", "nobody@example.com"])
        check("CLI rejects an unlisted address (rc 2)", rc == 2, rc)
    finally:
        sys.stdin = real_stdin
    r = signin("ops@508agent.test", "ops-operator-pass-1")
    check("CLI-created account signs in and is admin", r.status_code == 200 and is_admin(r.json()["token"]), r.text)

    # --- 4. a verify link is no way in ----------------------------------------
    # A squatter on a listed address can make the real inbox owner receive a
    # genuine verify email. One click verifies the SQUATTER's account; that
    # must still not be admin.
    r = signin("sec@508agent.test", "sec-squatter-pass-1")
    s_tok, s_id = r.json()["token"], r.json()["user"]["id"]
    link = mint_verify_link(s_tok, s_id)
    check("inbox owner clicks the verify link (200)", c.get("/auth/verify-email", params={"token": link}).status_code == 200)
    me = c.get("/auth/me", headers=h(s_tok)).json()
    check("listed + verified by link, never promoted -> NOT admin", bool(me["emailVerifiedAt"]) and not is_admin(s_tok), me)
    check("...and /audit-log still 403", c.get("/audit-log", headers=h(s_tok)).status_code == 403)

    # --- 5. moving off the listed address drops admin -------------------------
    r = c.patch("/auth/me", headers=h(op_tok), json={"email": "owner-personal@example.com"})
    away_tok = r.json().get("token") or ""
    check("admin moving to an unlisted address loses admin", r.status_code == 200 and not is_admin(away_tok), r.text)
    check("...and its role is reset to user", r.json().get("role") == "user", r.text)
    r = c.patch("/auth/me", headers=h(away_tok), json={"email": "owner@508agent.test"})
    check("...and can't PATCH back onto the listed address (409)", r.status_code == 409, r.text)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
