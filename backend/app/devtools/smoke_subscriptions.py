"""Smoke test for subscription billing via synthetic signed Stripe webhooks.

Drives the /billing/webhook endpoint with hand-built, correctly-signed Stripe
events (no live Stripe needed) and asserts: first-month grant on checkout,
renewal grant on invoice cycle, idempotency on replay, and cancellation.

Usage:
    python -m app.devtools.smoke_subscriptions
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time

_TMP = tempfile.mkdtemp(prefix="508_smoke_sub_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/sub.db"
os.environ["STRIPE_SECRET_KEY"] = "sk_test_dummy"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test_secret"
os.environ["STRIPE_PRICE_TEAM"] = "price_team_test"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

_WEBHOOK_SECRET = "whsec_test_secret"


def _sig_header(body: bytes) -> dict:
    ts = int(time.time())
    signed = f"{ts}.".encode("utf-8") + body
    sig = hmac.new(_WEBHOOK_SECRET.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={ts},v1={sig}"}


def _post_event(client: TestClient, event: dict):
    body = json.dumps(event).encode("utf-8")
    return client.post("/billing/webhook", content=body, headers=_sig_header(body))


def main() -> int:
    client = TestClient(app)
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    r = client.post(
        "/auth/sign-in",
        json={"email": "sub@example.com", "displayName": "Sub", "password": "subpass12345"},
    )
    assert r.status_code == 200, r.text
    auth = {"Authorization": f"Bearer {r.json()['token']}"}
    user_id = r.json()["user"]["id"]

    def balance() -> int:
        return client.get("/credits/balance", headers=auth).json()["balance"]

    start = balance()

    cfg = client.get("/billing/config").json()
    plans = {p["plan"] for p in cfg.get("subscriptionPlans", [])}
    check("config lists monthly+annual plans", {"team", "team_annual", "business", "business_annual"} <= plans)
    check("team price configured", any(p["plan"] == "team" and p["priceConfigured"] for p in cfg["subscriptionPlans"]))
    check("annual plan has year interval", any(p["plan"] == "team_annual" and p["interval"] == "year" for p in cfg["subscriptionPlans"]))

    sub_id = "sub_test_123"
    # Real Stripe events always carry payment_status / invoice status and line
    # prices; grants require them (unpaid checkouts and unknown prices grant
    # nothing — see smoke_subscription_webhooks).
    checkout = {"type": "checkout.session.completed", "data": {"object": {
        "id": "cs_test_1", "mode": "subscription", "subscription": sub_id, "customer": "cus_test_1",
        "payment_status": "paid", "client_reference_id": user_id, "metadata": {"plan": "team", "user_id": user_id}}}}
    check("sub checkout -> 200", _post_event(client, checkout).status_code == 200)
    check("first-month grant (+1000)", balance() == start + 1000)
    _post_event(client, checkout)
    check("checkout replay no double-grant", balance() == start + 1000)

    sub = client.get("/billing/subscription", headers=auth).json()
    check("subscription active=team", sub["active"] is True and sub["plan"] == "team")

    cycle = {"type": "invoice.payment_succeeded", "data": {"object": {
        "id": "in_test_1", "subscription": sub_id, "billing_reason": "subscription_cycle", "status": "paid",
        "lines": {"data": [{"price": {"id": "price_team_test"}, "proration": False}]}}}}
    check("renewal invoice -> 200", _post_event(client, cycle).status_code == 200)
    check("renewal grant (+1000)", balance() == start + 2000)
    _post_event(client, cycle)
    check("invoice replay no double-grant", balance() == start + 2000)

    create_inv = {"type": "invoice.payment_succeeded", "data": {"object": {
        "id": "in_test_0", "subscription": sub_id, "billing_reason": "subscription_create", "status": "paid",
        "lines": {"data": [{"price": {"id": "price_team_test"}, "proration": False}]}}}}
    _post_event(client, create_inv)
    check("subscription_create invoice does not double-grant the first period", balance() == start + 2000)

    # Annual plan grants 12x the monthly allowance up front (team_annual = 12000).
    before_annual = balance()
    annual = {"type": "checkout.session.completed", "data": {"object": {
        "id": "cs_annual", "mode": "subscription", "subscription": "sub_annual_1", "customer": "cus_1",
        "payment_status": "paid", "client_reference_id": user_id, "metadata": {"plan": "team_annual", "user_id": user_id}}}}
    _post_event(client, annual)
    check("annual checkout grants 12000", balance() == before_annual + 12000)

    for sid in (sub_id, "sub_annual_1"):
        cancel = {"type": "customer.subscription.deleted", "data": {"object": {"id": sid, "status": "canceled"}}}
        check(f"cancel {sid} -> 200", _post_event(client, cancel).status_code == 200)
    check("subscription inactive after cancel", client.get("/billing/subscription", headers=auth).json()["active"] is False)

    body = json.dumps(checkout).encode("utf-8")
    check("bad signature -> 400", client.post("/billing/webhook", content=body, headers={"Stripe-Signature": "t=1,v1=bad"}).status_code == 400)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
