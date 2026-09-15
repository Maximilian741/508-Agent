"""Smoke: subscription webhooks grant what was paid for, once, and never revive
a canceled subscription.

Pins three confirmed billing bugs over signed synthetic Stripe events (no live
Stripe):

  - a plan change updates the stored plan, and the next invoice grants the
    plan that invoice bills (its line price), never the stale row plan;
    proration / manual / unknown-price invoices grant nothing
  - an unpaid checkout (a delayed method such as ACH) grants nothing and
    unlocks nothing; the async success grants exactly once; failures grant
    nothing
  - a redelivered or late checkout.session.completed after deletion leaves
    the subscription canceled and grants nothing

Usage:
    python -m app.devtools.smoke_subscription_webhooks
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time

_TMP = tempfile.mkdtemp(prefix="508_smoke_subhooks_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/sh.db"
os.environ["STRIPE_SECRET_KEY"] = "sk_test_dummy"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test_hooks"
os.environ["STRIPE_PRICE_TEAM"] = "price_team_t"
os.environ["STRIPE_PRICE_TEAM_ANNUAL"] = "price_team_annual_t"
os.environ["STRIPE_PRICE_BUSINESS"] = "price_business_t"
os.environ["STRIPE_PRICE_BUSINESS_ANNUAL"] = "price_business_annual_t"
os.environ["STRIPE_PRICE_STUDIO"] = "price_studio_t"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

_WEBHOOK_SECRET = "whsec_test_hooks"


def _post_event(client: TestClient, event: dict) -> dict:
    body = json.dumps(event).encode("utf-8")
    ts = int(time.time())
    sig = hmac.new(_WEBHOOK_SECRET.encode("utf-8"), f"{ts}.".encode("utf-8") + body, hashlib.sha256).hexdigest()
    r = client.post("/billing/webhook", content=body, headers={"Stripe-Signature": f"t={ts},v1={sig}"})
    assert r.status_code == 200, r.text
    return r.json()


def _signin(client: TestClient, email: str):
    r = client.post("/auth/sign-in", json={"email": email, "displayName": "H", "password": "hookspass123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["user"]["id"]


def _sub_checkout(event_type: str, cs: str, sub: str, uid: str, plan: str, payment_status: str) -> dict:
    return {"type": event_type, "data": {"object": {
        "id": cs, "mode": "subscription", "subscription": sub, "customer": f"cus_{sub}",
        "payment_status": payment_status, "client_reference_id": uid,
        "metadata": {"plan": plan, "user_id": uid}}}}


def _pack_checkout(event_type: str, cs: str, uid: str, payment_status: str) -> dict:
    return {"type": event_type, "data": {"object": {
        "id": cs, "mode": "payment", "payment_status": payment_status, "client_reference_id": uid,
        "metadata": {"tier": "studio", "user_id": uid}}}}


def _invoice(inv: str, sub: str, reason: str, price: str, proration: bool = False, status: str = "paid") -> dict:
    return {"type": "invoice.payment_succeeded", "data": {"object": {
        "id": inv, "subscription": sub, "billing_reason": reason, "status": status, "amount_paid": 9900,
        "lines": {"data": [{"price": {"id": price}, "proration": proration}]}}}}


def _updated(sub: str, price: str, status: str = "active") -> dict:
    return {"type": "customer.subscription.updated", "data": {"object": {
        "id": sub, "status": status, "items": {"data": [{"price": {"id": price}}]}}}}


def _deleted(sub: str, uid: str = "", plan: str = "") -> dict:
    return {"type": "customer.subscription.deleted", "data": {"object": {
        "id": sub, "status": "canceled", "metadata": {"plan": plan, "user_id": uid}}}}


def main() -> int:
    client = TestClient(app)
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    from app.db.models import UserRow
    from app.db.session_sqlalchemy import session_scope

    # Balances are read from the DB so the smoke stays under the per-IP
    # limiter on /credits; subscription state goes through the real API.
    def bal(uid: str) -> int:
        with session_scope() as s:
            return int(s.get(UserRow, uid).credits_balance or 0)

    def sub_state(auth: dict) -> dict:
        return client.get("/billing/subscription", headers=auth).json()

    # --- 1. Plan change: downgrade business_annual -> team ------------------
    a_auth, a_id = _signin(client, "hooks-downgrade@example.com")
    _post_event(client, _sub_checkout("checkout.session.completed", "cs_a", "sub_a", a_id, "business_annual", "paid"))
    check("business_annual checkout grants 72000", bal(a_id) == 72000, f"bal={bal(a_id)}")
    check("team created on business plan has 10 seats",
          client.post("/teams", headers=a_auth, json={"name": "Downgraders"}).json().get("seatLimit") == 10)

    _post_event(client, _updated("sub_a", "price_team_t"))
    s = sub_state(a_auth)
    check("downgrade updates the stored plan to team", s.get("plan") == "team" and s.get("monthlyCredits") == 1000, str(s))
    team = client.get("/teams/me", headers=a_auth).json().get("team") or {}
    check("downgrade resizes the owner's team to 3 seats", team.get("seatLimit") == 3, str(team))

    before = bal(a_id)
    _post_event(client, _invoice("in_a_prorate", "sub_a", "subscription_update", "price_team_t", proration=True))
    check("proration invoice grants nothing", bal(a_id) == before)
    _post_event(client, _invoice("in_a_manual", "sub_a", "manual", "price_team_t"))
    check("manual invoice grants nothing", bal(a_id) == before)
    _post_event(client, _invoice("in_a_m1", "sub_a", "subscription_cycle", "price_team_t"))
    check("next $99 renewal grants the team allowance (+1000), not 72000", bal(a_id) == before + 1000, f"delta={bal(a_id) - before}")
    _post_event(client, _invoice("in_a_m1", "sub_a", "subscription_cycle", "price_team_t"))
    check("renewal replay grants nothing", bal(a_id) == before + 1000)

    # Upgrade: the invoice's billed price wins even before the update event lands.
    before = bal(a_id)
    _post_event(client, {"type": "invoice.payment_succeeded", "data": {"object": {
        "id": "in_a_m2", "billing_reason": "subscription_cycle", "status": "paid",
        "parent": {"type": "subscription_details", "subscription_details": {"subscription": "sub_a"}},
        "lines": {"data": [
            {"pricing": {"price_details": {"price": "price_team_t"}}, "parent": {"subscription_item_details": {"proration": True}}},
            {"pricing": {"price_details": {"price": "price_business_t"}}, "parent": {"subscription_item_details": {"proration": False}}},
        ]}}}})
    check("upgrade renewal grants the business allowance (+6000; newer API shape)", bal(a_id) == before + 6000, f"delta={bal(a_id) - before}")

    before = bal(a_id)
    _post_event(client, _invoice("in_a_unknown", "sub_a", "subscription_cycle", "price_somebody_else"))
    check("invoice for an unknown price grants nothing", bal(a_id) == before)
    _post_event(client, _updated("sub_a", "price_somebody_else"))
    check("update to an unknown price keeps the last known plan", sub_state(a_auth).get("plan") == "team")

    # --- 2. Unpaid checkouts ------------------------------------------------
    b_auth, b_id = _signin(client, "hooks-unpaid@example.com")
    _post_event(client, _sub_checkout("checkout.session.completed", "cs_b", "sub_b", b_id, "business", "unpaid"))
    check("unpaid subscription checkout grants nothing", bal(b_id) == 0, f"bal={bal(b_id)}")
    check("unpaid subscription is not active", sub_state(b_auth).get("active") is False)
    _post_event(client, {"type": "invoice.payment_failed", "data": {"object": {
        "id": "in_b_fail", "subscription": "sub_b", "billing_reason": "subscription_create", "status": "open"}}})
    check("invoice.payment_failed grants nothing", bal(b_id) == 0)
    _post_event(client, _sub_checkout("checkout.session.async_payment_succeeded", "cs_b", "sub_b", b_id, "business", "paid"))
    check("async success grants the first period (+6000)", bal(b_id) == 6000, f"bal={bal(b_id)}")
    check("async success activates the subscription", sub_state(b_auth).get("active") is True)
    _post_event(client, _sub_checkout("checkout.session.async_payment_succeeded", "cs_b", "sub_b", b_id, "business", "paid"))
    _post_event(client, _sub_checkout("checkout.session.completed", "cs_b", "sub_b", b_id, "business", "paid"))
    _post_event(client, _invoice("in_b_create", "sub_b", "subscription_create", "price_business_t"))
    check("redelivered success / completed / create invoice grant nothing more", bal(b_id) == 6000, f"bal={bal(b_id)}")

    # A delayed subscription whose first invoice clears (no async event seen).
    c_auth, c_id = _signin(client, "hooks-invoice-first@example.com")
    _post_event(client, _sub_checkout("checkout.session.completed", "cs_c", "sub_c", c_id, "team", "unpaid"))
    _post_event(client, _invoice("in_c_create", "sub_c", "subscription_create", "price_team_t"))
    check("paid subscription_create invoice grants the pending first period once", bal(c_id) == 1000, f"bal={bal(c_id)}")
    _post_event(client, _sub_checkout("checkout.session.async_payment_succeeded", "cs_c", "sub_c", c_id, "team", "paid"))
    check("later async success does not double-grant", bal(c_id) == 1000)
    check("subscription active after its first invoice cleared", sub_state(c_auth).get("active") is True)

    # Credit packs.
    _post_event(client, _pack_checkout("checkout.session.completed", "cs_pack_fail", b_id, "unpaid"))
    _post_event(client, _pack_checkout("checkout.session.async_payment_failed", "cs_pack_fail", b_id, "unpaid"))
    check("unpaid pack + async failure grants nothing", bal(b_id) == 6000, f"bal={bal(b_id)}")
    _post_event(client, _pack_checkout("checkout.session.completed", "cs_pack_ok", b_id, "unpaid"))
    check("unpaid pack grants nothing yet", bal(b_id) == 6000)
    _post_event(client, _pack_checkout("checkout.session.async_payment_succeeded", "cs_pack_ok", b_id, "paid"))
    check("pack async success grants 1300", bal(b_id) == 7300, f"bal={bal(b_id)}")
    _post_event(client, _pack_checkout("checkout.session.async_payment_succeeded", "cs_pack_ok", b_id, "paid"))
    _post_event(client, _pack_checkout("checkout.session.completed", "cs_pack_ok", b_id, "paid"))
    check("pack success/completed redelivery grants nothing more", bal(b_id) == 7300, f"bal={bal(b_id)}")

    # --- 3. No resurrection -------------------------------------------------
    d_auth, d_id = _signin(client, "hooks-revive@example.com")
    checkout_d = _sub_checkout("checkout.session.completed", "cs_d", "sub_d", d_id, "team", "paid")
    _post_event(client, checkout_d)
    _post_event(client, _deleted("sub_d"))
    check("deleted subscription is inactive", sub_state(d_auth).get("active") is False)
    before = bal(d_id)
    _post_event(client, checkout_d)
    check("redelivered checkout after deletion stays canceled", sub_state(d_auth).get("active") is False)
    _post_event(client, _updated("sub_d", "price_team_t", status="active"))
    check("stale 'active' update after deletion stays canceled", sub_state(d_auth).get("active") is False)
    check("no grant from redelivery after deletion", bal(d_id) == before)
    check("canceled subscriber cannot create a team",
          client.post("/teams", headers=d_auth, json={"name": "Zombie"}).status_code == 402)

    e_auth, e_id = _signin(client, "hooks-late@example.com")
    _post_event(client, _deleted("sub_e", uid=e_id, plan="team"))
    _post_event(client, _sub_checkout("checkout.session.completed", "cs_e", "sub_e", e_id, "team", "paid"))
    check("checkout arriving after deletion stays canceled", sub_state(e_auth).get("active") is False)
    _post_event(client, _invoice("in_e_create", "sub_e", "subscription_create", "price_team_t"))
    check("late checkout / create invoice after deletion grant nothing", bal(e_id) == 0, f"bal={bal(e_id)}")

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
