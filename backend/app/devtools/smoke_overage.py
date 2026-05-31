"""Smoke test for usage-based overage (auto top-up for subscribers).

Uses OVERAGE_TEST_MODE=succeed to simulate a successful off-session charge so
the top-up -> grant -> spend flow is exercisable without live Stripe.

Usage:
    python -m app.devtools.smoke_overage
"""

from __future__ import annotations

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="508_smoke_overage_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/o.db"
os.environ["STRIPE_SECRET_KEY"] = "sk_test_dummy"
os.environ["OVERAGE_TEST_MODE"] = "succeed"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _signin(client: TestClient, email: str):
    r = client.post("/auth/sign-in", json={"email": email, "displayName": "O", "password": "overagepass1"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["user"]["id"]


def main() -> int:
    client = TestClient(app)
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    def bal(auth: dict) -> int:
        return client.get("/credits/balance", headers=auth).json()["balance"]

    # Subscriber with overage on + a Stripe customer id.
    a_auth, a_id = _signin(client, "overage-a@example.com")
    client.post("/auth/grant-starter", headers=a_auth)  # +25

    from datetime import datetime

    from app.db.models import SubscriptionRow
    from app.db.session_sqlalchemy import session_scope

    with session_scope() as s:
        s.add(SubscriptionRow(
            id="sub_ov_a", user_id=a_id, plan="team", status="active",
            stripe_customer_id="cus_ov_a", current_period_end=None, overage_enabled=True,
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ))

    check("subscription shows overageEnabled", client.get("/billing/subscription", headers=a_auth).json().get("overageEnabled") is True)

    # Spend 100 with only 25 credits -> auto top-up (+200) -> spend succeeds.
    start = bal(a_auth)
    r = client.post("/credits/spend", headers=a_auth, json={"amount": 100, "description": "remediate"})
    check("overage top-up lets spend succeed", r.status_code == 200)
    check("balance = start + 200 topup - 100 spend", bal(a_auth) == start + 200 - 100)

    # Turn overage OFF.
    r = client.post("/billing/overage", headers=a_auth, json={"enabled": False})
    check("toggle overage off -> 200", r.status_code == 200 and r.json().get("overageEnabled") is False)

    # A spend beyond balance is now NOT topped up -> 402.
    big = bal(a_auth) + 500
    r = client.post("/credits/spend", headers=a_auth, json={"amount": big, "description": "remediate"})
    check("overage off -> insufficient 402", r.status_code == 402)

    # Non-subscriber: no overage, insufficient -> 402.
    b_auth, _ = _signin(client, "overage-b@example.com")
    r = client.post("/credits/spend", headers=b_auth, json={"amount": 50, "description": "remediate"})
    check("non-subscriber insufficient -> 402", r.status_code == 402)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
