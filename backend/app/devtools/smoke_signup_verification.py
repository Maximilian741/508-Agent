"""Smoke: signing up sends the verification email, so new accounts can get credits.

UX finding (critical, reproduced): on every hosted deploy the starter credits
wait on a verified email (grant-starter answers 403 verify_email_first), yet
nothing ever SENT the link — sign-up created the account and stopped. New
users sat at 0 credits and their first "Fix" click 402'd to /billing.

Now POST /auth/sign-in, when it CREATES an account on a deploy with SMTP
configured, mints the verification link with the account and mails it
(off the event loop), and says so:

  * ``verificationSent`` — True only when the mailer accepted the message;
  * ``verificationRequired`` — the starter credits are waiting on this
    account's email (so the UI says "check your inbox", not "buy credits").

Pins (the mailer is replaced by a recorder: no network is ever touched):
  1. No SMTP on a dev box: nothing sent, nothing required, no token minted.
  2. SMTP configured: a new account gets exactly ONE email, to its address,
     whose link carries the token stored for it; opening it verifies the
     account and the starter grant then pays 25 credits.
  3. Signing in again sends nothing new; after verification nothing is required.
  4. A mailer that fails or raises never fails sign-up; ``verificationSent`` is
     False, and the stored link stays usable (request-verify-email re-sends).
  5. A non-development deploy with no SMTP reports ``verificationRequired``
     (credits are locked) with ``verificationSent`` False — the truth, not a
     promise.
  6. Sign-up never touches password-reset links (separate "pr_" namespace).

Run: python -m app.devtools.smoke_signup_verification
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="508_smoke_signup_verify_"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'verify.db').as_posix()}"
os.environ["STORAGE_LOCAL_ROOT"] = str(_TMP / "storage")
os.environ["MATERIALIZED_ROOT"] = str(_TMP / "materialized")
os.environ["TRUST_PROXY_HEADERS"] = "true"
os.environ["PUBLIC_BASE_URL"] = "https://app.508agent.test"
for _key in ("SMTP_HOST", "APP_ENV", "ENVIRONMENT", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ.pop(_key, None)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

import app.api.auth as auth_mod  # noqa: E402
import app.config as config_mod  # noqa: E402
from app.db.models import EmailVerifyTokenRow  # noqa: E402
from app.db.session_sqlalchemy import session_scope  # noqa: E402
from app.main import app  # noqa: E402


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, detail: object = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, "" if cond else f"  [{str(detail)[:400]}]")
        if not cond:
            failures += 1

    outbox: list = []
    mode = {"result": True}

    def recorder(**kw):
        if mode["result"] == "raise":
            raise RuntimeError("smtp exploded")
        outbox.append(kw)
        return mode["result"]

    auth_mod.send_email = recorder  # never touch a network

    def tokens_for(uid: str) -> list:
        with session_scope() as s:
            return list(s.execute(select(EmailVerifyTokenRow.token).where(EmailVerifyTokenRow.user_id == uid)).scalars())

    ip = iter(range(1, 250))

    def client() -> TestClient:
        return TestClient(app, headers={"X-Forwarded-For": f"10.44.0.{next(ip)}"})

    def signup(email: str, password: str = "verifypass1"):
        r = client().post("/auth/sign-in", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return r.json()

    # --- 1. dev, no SMTP ------------------------------------------------------------
    body = signup("devbox@example.com")
    check(
        "dev without SMTP: verificationSent False, verificationRequired False",
        body.get("verificationSent") is False and body.get("verificationRequired") is False,
        body,
    )
    check("dev without SMTP: no email, no token minted", not outbox and not tokens_for(body["user"]["id"]), (outbox, tokens_for(body["user"]["id"])))

    # --- 2. SMTP configured -----------------------------------------------------------
    os.environ["SMTP_HOST"] = "smtp.recorder.invalid"
    try:
        body = signup("newcomer@example.com")
        uid = body["user"]["id"]
        check("SMTP: sign-up reports verificationSent True", body.get("verificationSent") is True, body)
        check("SMTP: ...and verificationRequired True (credits wait on the email)", body.get("verificationRequired") is True, body)
        check("SMTP: exactly one email, to the new address", len(outbox) == 1 and outbox[0].get("to") == "newcomer@example.com", outbox)
        toks = tokens_for(uid)
        link_ok = len(toks) == 1 and f"https://app.508agent.test/verify-email?token={toks[0]}" in (outbox[0].get("body") if outbox else "")
        check("SMTP: the email's link carries the token stored for this account", link_ok, (toks, outbox[:1]))
        auth = {"Authorization": f"Bearer {body['token']}"}
        c = client()
        g = c.post("/auth/grant-starter", headers=auth)
        check("SMTP: before the link is opened the grant still waits (403 verify_email_first)", g.status_code == 403 and g.json().get("code") == "verify_email_first", g.text[:200])
        v = c.get("/auth/verify-email", params={"token": toks[0] if toks else "x"})
        check("SMTP: opening the emailed link verifies the account", v.status_code == 200 and v.json().get("verified") is True, v.text[:200])
        g = c.post("/auth/grant-starter", headers=auth)
        check("SMTP: ...and the starter grant then pays 25", g.status_code == 200 and g.json().get("amount") == 25, g.text[:200])

        # --- 3. sign in again ------------------------------------------------------
        before = len(outbox)
        again = client().post("/auth/sign-in", json={"email": "newcomer@example.com", "password": "verifypass1"}).json()
        check("signing in again sends nothing new", len(outbox) == before and again.get("verificationSent") is False, again)
        check("after verification nothing is required", again.get("verificationRequired") is False, again)

        # --- 4. a failing mailer never fails sign-up -----------------------------------
        mode["result"] = False
        body = signup("mailfails@example.com")
        check("mailer returns False: sign-up 200, verificationSent False", body.get("verificationSent") is False, body)
        check("...the link is still stored, so a re-send can deliver it", len(tokens_for(body["user"]["id"])) == 1)
        mode["result"] = "raise"
        body = signup("mailraises@example.com")
        check("mailer raises: sign-up 200, verificationSent False", body.get("verificationSent") is False and body.get("token"), body)
        mode["result"] = True
        r = client().post("/auth/request-verify-email", headers={"Authorization": f"Bearer {body['token']}"})
        check("request-verify-email still re-sends (one fresh link)", r.status_code == 200 and len(tokens_for(body["user"]["id"])) == 1, r.text[:200])

        # --- 6. reset links are untouched --------------------------------------------
        c = client()
        c.post("/auth/request-password-reset", json={"email": "newcomer@example.com"})
        with session_scope() as s:
            reset_before = list(s.execute(select(EmailVerifyTokenRow.token).where(EmailVerifyTokenRow.token.like("pr_%"))).scalars())
        signup("another@example.com")
        with session_scope() as s:
            reset_after = list(s.execute(select(EmailVerifyTokenRow.token).where(EmailVerifyTokenRow.token.like("pr_%"))).scalars())
        check("a sign-up never deletes someone's password-reset link", reset_before and reset_before == reset_after, (reset_before, reset_after))
    finally:
        os.environ.pop("SMTP_HOST", None)

    # --- 5. production with no SMTP: the truth, not a promise --------------------------
    os.environ["APP_ENV"] = "production"
    os.environ.setdefault("APP_SECRET", "p" * 64)
    config_mod.get_settings.cache_clear()
    try:
        before = len(outbox)
        body = signup("prod-nosmtp@example.com")
        check(
            "production without SMTP: verificationRequired True, verificationSent False, nothing sent",
            body.get("verificationRequired") is True and body.get("verificationSent") is False and len(outbox) == before,
            body,
        )
    finally:
        os.environ.pop("APP_ENV", None)
        config_mod.get_settings.cache_clear()

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
