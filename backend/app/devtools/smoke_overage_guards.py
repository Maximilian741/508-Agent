"""Smoke: overage never charges a card it shouldn't.

A confirmed bug: /credits/spend with any amount made a real $10 off-session
charge on every spend it could not cover (the spend still 402'd), uncapped,
and a plain team member could trigger it on the owner's card. This pins the
guards with the Stripe HTTP call stubbed (no OVERAGE_TEST_MODE, nothing
leaves the process), counting the exact PaymentIntents that would be sent:

  - no charge when one pack does not cover the shortfall
  - no charge when a non-admin member spends; an admin may
  - at most OVERAGE_MAX_PACKS_PER_DAY packs per rolling 24 hours

Usage:
    python -m app.devtools.smoke_overage_guards
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time

_TMP = tempfile.mkdtemp(prefix="508_smoke_ovguard_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/og.db"
os.environ["STRIPE_SECRET_KEY"] = "sk_test_dummy"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test_ovguard"
os.environ.pop("OVERAGE_TEST_MODE", None)
os.environ.pop("OVERAGE_MAX_PACKS_PER_DAY", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.api import stripe_billing  # noqa: E402
from app.main import app  # noqa: E402

_WEBHOOK_SECRET = "whsec_test_ovguard"
_INTENTS: list = []


def _fake_stripe_post(path: str, form: dict, secret: str) -> dict:
    _INTENTS.append((path, dict(form)))
    return {"id": f"pi_guard_{len(_INTENTS)}", "status": "succeeded"}


stripe_billing._stripe_post = _fake_stripe_post


def _signin(client: TestClient, email: str):
    r = client.post("/auth/sign-in", json={"email": email, "displayName": "G", "password": "guardpass123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["user"]["id"]


def _verify(email: str) -> None:
    """Mark an address proven, as clicking the verification link would."""
    from datetime import datetime

    from sqlalchemy import update

    from app.db.models import UserRow
    from app.db.session_sqlalchemy import session_scope

    with session_scope() as s:
        s.execute(
            update(UserRow).where(UserRow.email == email).values(email_verified_at=datetime.utcnow())
        )


def main() -> int:
    client = TestClient(app)
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    from datetime import timedelta

    from app.db.models import CreditLedgerRow, UserRow
    from app.db.session_sqlalchemy import session_scope

    def bal(uid: str) -> int:
        with session_scope() as s:
            return int(s.get(UserRow, uid).credits_balance or 0)

    def spend(auth: dict, amount: int) -> int:
        return client.post("/credits/spend", headers=auth, json={"amount": amount, "description": "guard"}).status_code

    # Owner subscribes through a real signed webhook (overage defaults on).
    owner_auth, owner_id = _signin(client, "guard-owner@example.com")
    body = json.dumps({"type": "checkout.session.completed", "data": {"object": {
        "id": "cs_guard", "mode": "subscription", "subscription": "sub_guard", "customer": "cus_guard",
        "payment_status": "paid", "client_reference_id": owner_id,
        "metadata": {"plan": "team", "user_id": owner_id}}}}).encode("utf-8")
    ts = int(time.time())
    sig = hmac.new(_WEBHOOK_SECRET.encode("utf-8"), f"{ts}.".encode("utf-8") + body, hashlib.sha256).hexdigest()
    client.post("/billing/webhook", content=body, headers={"Stripe-Signature": f"t={ts},v1={sig}"})
    check("owner funded by team checkout (1000)", bal(owner_id) == 1000, f"bal={bal(owner_id)}")

    # A plain member and an admin join the owner's team.
    client.post("/teams", headers=owner_auth, json={"name": "Guarded"})
    member_auth, _ = _signin(client, "guard-member@example.com")
    admin_auth, _ = _signin(client, "guard-admin@example.com")
    for email, auth, role in (("guard-member@example.com", member_auth, "member"), ("guard-admin@example.com", admin_auth, "admin")):
        # A seat spends the owner's wallet, so /teams/accept requires a PROVEN
        # address (see smoke_team_invites). Stamp it rather than round-trip the
        # verification email — this smoke is about overage, not verification.
        _verify(email)
        inv = client.post("/teams/invite", headers=owner_auth, json={"email": email, "role": role}).json()
        r = client.post("/teams/accept", headers=auth, json={"token": inv["acceptUrl"].split("token=")[-1]})
        check(f"{role} joined the team", r.status_code == 200)

    check("owner drains the wallet", spend(owner_auth, 1000) == 200 and bal(owner_id) == 0)

    # 1. A pack that can't cover the shortfall is never charged.
    check("spend 201 on an empty wallet -> 402", spend(owner_auth, 201) == 402)
    check("no PaymentIntent when one pack can't cover the shortfall", len(_INTENTS) == 0, str(_INTENTS))
    check("member spend 100000 -> 402", spend(member_auth, 100000) == 402)
    check("still no PaymentIntent", len(_INTENTS) == 0)

    # 2. Only the owner or a team admin may charge the owner's card.
    check("non-admin member spend 5 on an empty wallet -> 402", spend(member_auth, 5) == 402)
    check("no PaymentIntent for a non-admin member", len(_INTENTS) == 0, str(_INTENTS))
    check("team admin spend 5 -> 200 via overage", spend(admin_auth, 5) == 200)
    check("admin triggered exactly one PaymentIntent", len(_INTENTS) == 1)
    form = _INTENTS[0][1] if _INTENTS else {}
    check("the intent is one $10 off-session charge on the owner's customer",
          form.get("amount") == "1000" and form.get("customer") == "cus_guard" and form.get("off_session") == "true", str(form))
    check("owner wallet = 200 pack - 5", bal(owner_id) == 195)

    # A pack that exactly covers the shortfall is allowed.
    check("owner spend 395 (195 + one pack) -> 200", spend(owner_auth, 395) == 200)
    check("owner spend 200 on an empty wallet -> 200", spend(owner_auth, 200) == 200)
    check("three packs charged so far", len(_INTENTS) == 3, str(len(_INTENTS)))

    # 3. Default cap: 3 packs per rolling 24 hours.
    check("4th pack within 24h is refused -> 402", spend(owner_auth, 1) == 402)
    check("no PaymentIntent past the cap", len(_INTENTS) == 3, str(len(_INTENTS)))

    os.environ["OVERAGE_MAX_PACKS_PER_DAY"] = "4"
    check("raised cap (OVERAGE_MAX_PACKS_PER_DAY=4) allows one more", spend(owner_auth, 1) == 200 and len(_INTENTS) == 4)
    check("5th pack refused under the raised cap", spend(owner_auth, 200) == 402 and len(_INTENTS) == 4)
    os.environ.pop("OVERAGE_MAX_PACKS_PER_DAY", None)

    # Packs older than the window stop counting.
    with session_scope() as s:
        for row in s.query(CreditLedgerRow).filter(CreditLedgerRow.user_id == owner_id, CreditLedgerRow.kind == "overage"):
            row.at = row.at - timedelta(hours=25)
    check("after 24h the cap resets -> 200", spend(owner_auth, 200) == 200 and len(_INTENTS) == 5, str(len(_INTENTS)))

    os.environ["OVERAGE_MAX_PACKS_PER_DAY"] = "0"
    check("OVERAGE_MAX_PACKS_PER_DAY=0 disables overage", spend(owner_auth, bal(owner_id) + 1) == 402 and len(_INTENTS) == 5)
    os.environ.pop("OVERAGE_MAX_PACKS_PER_DAY", None)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
