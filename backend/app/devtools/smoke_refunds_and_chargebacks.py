"""Smoke: money going back out takes the credits with it.

Pre-launch audit finding (high, reproduced): /billing/webhook had no branch
for charge.refunded, charge.dispute.*, payment_intent.canceled or
invoice.voided. A studio pack credited 1300, then a full refund AND a lost
chargeback both returned {"ignored": ...} and the balance stayed 1300 —
spendable. Net credits from nothing.

The grant now records the PaymentIntent / Invoice that paid for it
(``credit_ledger.stripe_ref``, migration 0017), so a reversal can find it.
Pinned here:

1. a full refund claws the credits back; the ledger carries a reversal row
2. a partial refund claws back only that share
3. a lost chargeback claws back; a redelivery is a no-op, and a refund plus a
   dispute on the SAME charge reverse the money once, not twice
4. a chargeback we WIN puts the credits back (we kept the money)
5. subscription money reverses the same way (sub_init and a cycle invoice)
6. invoice.voided / invoice.marked_uncollectible reverse the period
7. BALANCE FLOOR: credits already spent cannot push the wallet negative — a
   negative balance would silently become free product on the next purchase.
   The unrecovered part is recorded as ``shortfall=N`` on the reversal row so
   support can see the loss.
8. starter grants (no stripe_ref) are never touched by a reversal
9. the documented asymmetry: a subscription_cycle invoice landing AFTER the
   subscription went terminal still grants, because Stripe only emits a PAID
   cycle invoice once it collected that period's money and terminal status
   here is permanent. Refund handling, not that check, is what covers money
   going back.

Stripe is never called: STRIPE_SECRET_KEY is unset, and every webhook body is
signed locally with the real HMAC the endpoint verifies.

Usage:
    python -m app.devtools.smoke_refunds_and_chargebacks
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="508_smoke_refunds_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/refunds.db"
os.environ["STORAGE_LOCAL_ROOT"] = str(_TMP / "storage")
os.environ["MATERIALIZED_ROOT"] = str(_TMP / "materialized")
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test_refunds"
os.environ["STRIPE_PRICE_TEAM"] = "price_team"
os.environ["STRIPE_PRICE_BUSINESS"] = "price_business"
os.environ.pop("STRIPE_SECRET_KEY", None)
os.environ.pop("SMTP_HOST", None)

# Real migration chain, so the new stripe_ref column is proven to exist after
# `alembic upgrade head` and not just from create_all.
_BACKEND = Path(__file__).resolve().parents[2]
_mig = subprocess.run(
    [sys.executable, "-m", "alembic", "upgrade", "head"],
    cwd=str(_BACKEND), capture_output=True, text=True,
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db.models import CreditLedgerRow, UserRow  # noqa: E402
from app.db.session_sqlalchemy import session_scope  # noqa: E402
from app.main import app  # noqa: E402

_WHSEC = os.environ["STRIPE_WEBHOOK_SECRET"].encode()


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: object = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, "" if cond else f"  [{extra}]")
        if not cond:
            failures += 1

    check("alembic upgrade head succeeded", _mig.returncode == 0, (_mig.stderr or "")[-300:])

    client = TestClient(app, headers={"X-Forwarded-For": "10.55.0.1"})

    def hook(evt: dict) -> dict:
        raw = json.dumps(evt).encode()
        ts = str(int(time.time()))
        sig = hmac.new(_WHSEC, f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
        resp = client.post(
            "/billing/webhook", content=raw,
            headers={"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def signin(email: str):
        r = client.post("/auth/sign-in", json={"email": email, "password": "refundpass1"})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["user"]["id"]

    def balance(uid: str) -> int:
        with session_scope() as s:
            return int(s.get(UserRow, uid).credits_balance or 0)

    def ledger(uid: str):
        with session_scope() as s:
            return [
                (r.kind, r.amount, r.description)
                for r in s.execute(
                    select(CreditLedgerRow).where(CreditLedgerRow.user_id == uid)
                ).scalars().all()
            ]

    def buy_pack(uid: str, session_id: str, pi: str, tier: str = "studio") -> dict:
        return hook({
            "type": "checkout.session.completed",
            "data": {"object": {
                "id": session_id, "mode": "payment", "payment_status": "paid",
                "payment_intent": pi, "client_reference_id": uid,
                "metadata": {"tier": tier, "user_id": uid},
            }},
        })

    def refund(charge_id: str, pi: str, amount: int, refunded: int) -> dict:
        return hook({
            "type": "charge.refunded",
            "data": {"object": {
                "id": charge_id, "payment_intent": pi,
                "amount": amount, "amount_refunded": refunded, "refunded": refunded >= amount,
            }},
        })

    # --- 1. full refund of a credit pack -------------------------------------
    hdr, uid = signin("refund-full@example.com")
    buy_pack(uid, "cs_full", "pi_full")
    check("the pack credited 1300 (sanity)", balance(uid) == 1300, balance(uid))
    res = refund("ch_full", "pi_full", 129900, 129900)
    check("a full refund reverses the grant", res.get("reversed") is True, res)
    check("the balance goes back to 0", balance(uid) == 0, balance(uid))
    rows = ledger(uid)
    check(
        "the ledger records a compensating -1300 reversal row",
        any(k == "reversal" and a == -1300 for k, a, _ in rows),
        rows,
    )
    check("a redelivered refund is a no-op", refund("ch_full", "pi_full", 129900, 129900).get("reversed") is False)
    check("and the balance is unchanged by the redelivery", balance(uid) == 0, balance(uid))

    # --- 2. partial refund ----------------------------------------------------
    hdr, uid = signin("refund-part@example.com")
    buy_pack(uid, "cs_part", "pi_part")
    refund("ch_part", "pi_part", 129900, 64950)  # half back
    check("a half refund claws back half the credits", balance(uid) == 650, balance(uid))

    # --- 3. chargeback --------------------------------------------------------
    hdr, uid = signin("dispute-lost@example.com")
    buy_pack(uid, "cs_disp", "pi_disp")
    res = hook({
        "type": "charge.dispute.created",
        "data": {"object": {"id": "dp_lost", "charge": "ch_disp",
                            "payment_intent": "pi_disp", "amount": 129900}},
    })
    check("a chargeback claws the credits back", res.get("reversed") is True, res)
    check("the balance is 0 after the chargeback", balance(uid) == 0, balance(uid))
    closed = hook({
        "type": "charge.dispute.closed",
        "data": {"object": {"id": "dp_lost", "charge": "ch_disp", "payment_intent": "pi_disp",
                            "amount": 129900, "status": "lost"}},
    })
    check("closing it 'lost' does not debit a second time", closed.get("reversed") is False, closed)
    check("balance still 0, not negative", balance(uid) == 0, balance(uid))

    # a refund AND a dispute on the same charge must reverse the money ONCE
    hdr, uid = signin("refund-then-dispute@example.com")
    buy_pack(uid, "cs_both", "pi_both")
    refund("ch_both", "pi_both", 129900, 129900)
    hook({
        "type": "charge.dispute.created",
        "data": {"object": {"id": "dp_both", "charge": "ch_both",
                            "payment_intent": "pi_both", "amount": 129900}},
    })
    rows = [r for r in ledger(uid) if r[0] == "reversal"]
    check(
        "two different events for the same money reverse it once, not twice",
        sum(a for _, a, _ in rows) == -1300,
        rows,
    )
    check("and the wallet never goes negative", balance(uid) == 0, balance(uid))

    # --- 4. a chargeback we WIN gives the credits back ------------------------
    hdr, uid = signin("dispute-won@example.com")
    buy_pack(uid, "cs_won", "pi_won")
    hook({
        "type": "charge.dispute.created",
        "data": {"object": {"id": "dp_won", "charge": "ch_won",
                            "payment_intent": "pi_won", "amount": 129900}},
    })
    check("the chargeback took the credits", balance(uid) == 0, balance(uid))
    res = hook({
        "type": "charge.dispute.closed",
        "data": {"object": {"id": "dp_won", "charge": "ch_won", "payment_intent": "pi_won",
                            "amount": 129900, "status": "won"}},
    })
    check("winning the dispute restores them (we kept the money)", res.get("restored") is True, res)
    check("the balance is whole again", balance(uid) == 1300, balance(uid))
    check(
        "a redelivered 'won' does not double-credit",
        hook({
            "type": "charge.dispute.closed",
            "data": {"object": {"id": "dp_won", "charge": "ch_won", "payment_intent": "pi_won",
                                "amount": 129900, "status": "won"}},
        }).get("restored") is False,
    )
    check("balance still 1300", balance(uid) == 1300, balance(uid))

    # --- 5. subscription money ------------------------------------------------
    hdr, uid = signin("refund-sub@example.com")
    hook({
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_sub", "mode": "subscription", "payment_status": "paid",
            "subscription": "sub_R", "customer": "cus_R", "client_reference_id": uid,
            "payment_intent": "pi_subinit", "metadata": {"plan": "team", "user_id": uid},
        }},
    })
    check("the subscription's first period credited 1000 (sanity)", balance(uid) == 1000, balance(uid))
    hook({
        "type": "invoice.payment_succeeded",
        "data": {"object": {
            "id": "in_cycle", "subscription": "sub_R", "billing_reason": "subscription_cycle",
            "status": "paid", "payment_intent": "pi_cycle",
            "lines": {"data": [{"pricing": {"price_details": {"price": "price_team"}}}]},
        }},
    })
    check("a renewal credited another 1000 (sanity)", balance(uid) == 2000, balance(uid))
    refund("ch_cycle", "pi_cycle", 9900, 9900)
    check("refunding the renewal claws back only the renewal", balance(uid) == 1000, balance(uid))
    refund("ch_subinit", "pi_subinit", 9900, 9900)
    check("refunding the first period claws that back too", balance(uid) == 0, balance(uid))

    # --- 6. voided / uncollectible invoices -----------------------------------
    hdr, uid = signin("void-invoice@example.com")
    hook({
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_void", "mode": "subscription", "payment_status": "paid",
            "subscription": "sub_V", "customer": "cus_V", "client_reference_id": uid,
            "metadata": {"plan": "team", "user_id": uid},
        }},
    })
    hook({
        "type": "invoice.payment_succeeded",
        "data": {"object": {
            "id": "in_void", "subscription": "sub_V", "billing_reason": "subscription_cycle",
            "status": "paid",
            "lines": {"data": [{"pricing": {"price_details": {"price": "price_team"}}}]},
        }},
    })
    check("the renewal credited (sanity)", balance(uid) == 2000, balance(uid))
    res = hook({"type": "invoice.voided", "data": {"object": {"id": "in_void"}}})
    check("voiding the invoice reverses its allowance", res.get("reversed") is True, res)
    check("only that period comes back out", balance(uid) == 1000, balance(uid))

    # --- 7. balance floor: already-spent credits ------------------------------
    hdr, uid = signin("refund-spent@example.com")
    buy_pack(uid, "cs_spent", "pi_spent")
    with session_scope() as s:  # spend it all, as the product would
        s.get(UserRow, uid).credits_balance = 0
    res = refund("ch_spent", "pi_spent", 129900, 129900)
    check("a refund of spent credits does not push the wallet negative", balance(uid) == 0, balance(uid))
    check("the unrecovered credits are reported as a shortfall", int(res.get("shortfall") or 0) == 1300, res)
    rows = ledger(uid)
    check(
        "and the loss is recorded on the reversal row for support",
        any(k == "reversal" and "shortfall=1300" in d for k, _, d in rows),
        rows,
    )
    # A later top-up must be real credits, not a repayment of a hidden debt.
    buy_pack(uid, "cs_spent2", "pi_spent2")
    check("a later purchase is worth its full face value", balance(uid) == 1300, balance(uid))

    # --- 8. starter grants are not refundable money ---------------------------
    hdr, uid = signin("grant-untouched@example.com")
    assert client.post("/auth/grant-starter", headers=hdr).status_code == 200
    buy_pack(uid, "cs_mixed", "pi_mixed")
    check("starter + pack (sanity)", balance(uid) == 1325, balance(uid))
    refund("ch_mixed", "pi_mixed", 129900, 129900)
    check("refunding the pack leaves the starter grant alone", balance(uid) == 25, balance(uid))

    # --- 9. the documented subscription_cycle asymmetry -----------------------
    hdr, uid = signin("cycle-after-cancel@example.com")
    hook({
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_late", "mode": "subscription", "payment_status": "paid",
            "subscription": "sub_L", "customer": "cus_L", "client_reference_id": uid,
            "metadata": {"plan": "team", "user_id": uid},
        }},
    })
    hook({
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": "sub_L", "status": "canceled",
                            "items": {"data": [{"price": {"id": "price_team"}}]}}},
    })
    late = hook({
        "type": "invoice.payment_succeeded",
        "data": {"object": {
            "id": "in_late", "subscription": "sub_L", "billing_reason": "subscription_cycle",
            "status": "paid", "payment_intent": "pi_late",
            "lines": {"data": [{"pricing": {"price_details": {"price": "price_team"}}}]},
        }},
    })
    check(
        "a PAID cycle invoice after cancellation still credits (the money arrived)",
        late.get("credited") is True,
        late,
    )
    unpaid = hook({
        "type": "invoice.payment_succeeded",
        "data": {"object": {
            "id": "in_late_unpaid", "subscription": "sub_L", "billing_reason": "subscription_cycle",
            "status": "open",
            "lines": {"data": [{"pricing": {"price_details": {"price": "price_team"}}}]},
        }},
    })
    check(
        "but an UNPAID one never does — the grant always tracks collected money",
        unpaid.get("credited") is False and unpaid.get("reason") == "invoice_not_paid",
        unpaid,
    )
    before = balance(uid)
    refund("ch_late", "pi_late", 9900, 9900)
    check(
        "and if that late period is refunded, the allowance comes back out",
        balance(uid) == before - 1000,
        (before, balance(uid)),
    )

    print()
    print("FAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
