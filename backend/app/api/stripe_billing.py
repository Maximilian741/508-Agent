"""Stripe Checkout scaffold.

Plain-urllib implementation so we don't need the stripe SDK as a dep.
If STRIPE_SECRET_KEY is unset, every billing endpoint returns 503 with
detail=billing_not_configured (except /billing/config which reports state).

Real keys are swapped in via env:
    STRIPE_SECRET_KEY       sk_live_... or sk_test_...
    STRIPE_WEBHOOK_SECRET   whsec_...
    STRIPE_PRICE_STARTER    price_...
    STRIPE_PRICE_PRO        price_...
    STRIPE_PRICE_STUDIO     price_...
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.auth import get_current_user_row
from app.api.deps import require_user_id
from app.db.models import CreditLedgerRow, SubscriptionRow, UserRow
from app.db.session_sqlalchemy import session_scope

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing")


_TIER_AMOUNTS: Dict[str, int] = {
    "starter": 50,
    "pro": 250,
    "studio": 1300,
}

_TIER_PRICE_ENV: Dict[str, str] = {
    "starter": "STRIPE_PRICE_STARTER",
    "pro": "STRIPE_PRICE_PRO",
    "studio": "STRIPE_PRICE_STUDIO",
}

# Recurring subscription plans. Each paid invoice grants this many credits for
# the billing period; users still spend credits per remediation, so a plan is
# effectively a monthly allowance (set high enough to feel "unlimited").
_SUB_PLAN_CREDITS: Dict[str, int] = {
    "team": 1000,
    "business": 6000,
}

_SUB_PLAN_PRICE_ENV: Dict[str, str] = {
    "team": "STRIPE_PRICE_TEAM",
    "business": "STRIPE_PRICE_BUSINESS",
}


def _stripe_secret() -> Optional[str]:
    val = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    return val or None


def _webhook_secret() -> Optional[str]:
    val = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    return val or None


def _price_id_for(tier: str) -> Optional[str]:
    env_name = _TIER_PRICE_ENV.get(tier)
    if not env_name:
        return None
    val = os.environ.get(env_name, "").strip()
    return val or None


def _sub_price_id_for(plan: str) -> Optional[str]:
    env_name = _SUB_PLAN_PRICE_ENV.get(plan)
    if not env_name:
        return None
    val = os.environ.get(env_name, "").strip()
    return val or None


def _grant_credits_idempotent(user_id: str, amount: int, kind: str, description: str) -> bool:
    """Grant ``amount`` credits to ``user_id`` exactly once per ``description``.

    ``description`` is the idempotency key — a duplicate webhook delivery with
    the same key is a no-op. Returns True if credited, False otherwise.
    """
    if not user_id or amount <= 0:
        return False
    with session_scope() as session:
        row = session.execute(
            select(UserRow).where(UserRow.id == user_id)
        ).scalar_one_or_none()
        if row is None:
            logger.warning("grant_credits: unknown user_id=%s", user_id)
            return False
        existing = session.execute(
            select(CreditLedgerRow).where(
                CreditLedgerRow.user_id == user_id,
                CreditLedgerRow.description == description,
            )
        ).first()
        if existing is not None:
            return False
        row.credits_balance = int(row.credits_balance or 0) + amount
        session.add(
            CreditLedgerRow(
                user_id=user_id,
                at=datetime.utcnow(),
                kind=kind,
                amount=amount,
                description=description,
                related_doc_id=None,
            )
        )
        session.flush()
        return True


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CreateCheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tier: Literal["starter", "pro", "studio"]
    success_url: str = Field(min_length=1, max_length=2000)
    cancel_url: str = Field(min_length=1, max_length=2000)


class CreateCheckoutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str


class BillingTierDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tier: str
    credits: int
    priceConfigured: bool


class SubscriptionPlanDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: str
    monthlyCredits: int
    priceConfigured: bool


class BillingConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    tiers: List[BillingTierDTO]
    subscriptionPlans: List[SubscriptionPlanDTO] = Field(default_factory=list)


class CreateSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: Literal["team", "business"]
    success_url: str = Field(min_length=1, max_length=2000)
    cancel_url: str = Field(min_length=1, max_length=2000)


class SubscriptionDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool
    plan: Optional[str] = None
    status: Optional[str] = None
    currentPeriodEnd: Optional[str] = None
    monthlyCredits: Optional[int] = None


class PortalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    return_url: str = Field(min_length=1, max_length=2000)


class PortalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str


# ---------------------------------------------------------------------------
# Stripe HTTP helpers
# ---------------------------------------------------------------------------


def _stripe_post(path: str, form: Dict[str, str], secret: str) -> Dict[str, Any]:
    """POST form-encoded body to Stripe.  Returns parsed JSON dict."""
    body = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.stripe.com{path}",
        data=body,
        method="POST",
    )
    req.add_header("Authorization", f"Bearer {secret}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8")
        except Exception:
            err_body = ""
        logger.warning("stripe HTTPError %s: %s", exc.code, err_body[:500])
        raise HTTPException(status_code=502, detail="stripe_error")
    except Exception as exc:
        logger.warning("stripe request failed: %s", exc)
        raise HTTPException(status_code=502, detail="stripe_unreachable")


def _verify_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    """Verify Stripe-Signature header.

    Header format: t=TIMESTAMP,v1=HEX[,v1=HEX...]
    Computed signature: HMAC-SHA256(secret, "<timestamp>.<payload>").
    """
    if not sig_header or not secret:
        return False
    parts = {}
    for chunk in sig_header.split(","):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            parts.setdefault(k.strip(), []).append(v.strip())
    timestamps = parts.get("t", [])
    sigs = parts.get("v1", [])
    if not timestamps or not sigs:
        return False
    timestamp = timestamps[0]
    # Reject stale or replayed events outside Stripe's default 5-minute
    # tolerance window — the HMAC alone does not prevent replay of a captured
    # body+signature.
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(time.time() - ts) > 300:
        logger.warning("stripe webhook timestamp outside tolerance window")
        return False
    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    expected = hmac.new(
        secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    for candidate in sigs:
        if hmac.compare_digest(expected, candidate):
            return True
    return False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/config", response_model=BillingConfigResponse)
async def billing_config() -> BillingConfigResponse:
    enabled = _stripe_secret() is not None
    tiers = [
        BillingTierDTO(
            tier=t,
            credits=_TIER_AMOUNTS[t],
            priceConfigured=_price_id_for(t) is not None,
        )
        for t in ("starter", "pro", "studio")
    ]
    plans = [
        SubscriptionPlanDTO(
            plan=p,
            monthlyCredits=_SUB_PLAN_CREDITS[p],
            priceConfigured=_sub_price_id_for(p) is not None,
        )
        for p in ("team", "business")
    ]
    return BillingConfigResponse(enabled=enabled, tiers=tiers, subscriptionPlans=plans)


@router.post("/create-checkout-session", response_model=CreateCheckoutResponse)
async def create_checkout_session(
    payload: CreateCheckoutRequest,
    request: Request,
    user_id: str = Depends(require_user_id),
) -> CreateCheckoutResponse:
    secret = _stripe_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="billing_not_configured")

    price_id = _price_id_for(payload.tier)
    if not price_id:
        raise HTTPException(status_code=503, detail="billing_not_configured")

    with session_scope() as session:
        # Confirm the authenticated user still exists before opening a checkout.
        get_current_user_row(session, account_id=user_id)

    form = {
        "mode": "payment",
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "success_url": payload.success_url,
        "cancel_url": payload.cancel_url,
        "client_reference_id": str(user_id),
        "metadata[tier]": payload.tier,
        "metadata[user_id]": str(user_id),
    }

    data = _stripe_post("/v1/checkout/sessions", form, secret)
    url = data.get("url")
    if not isinstance(url, str) or not url:
        logger.warning("stripe response missing url: %s", str(data)[:500])
        raise HTTPException(status_code=502, detail="stripe_error")
    return CreateCheckoutResponse(url=url)


@router.post("/create-subscription-session", response_model=CreateCheckoutResponse)
async def create_subscription_session(
    payload: CreateSubscriptionRequest,
    request: Request,
    user_id: str = Depends(require_user_id),
) -> CreateCheckoutResponse:
    secret = _stripe_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="billing_not_configured")

    price_id = _sub_price_id_for(payload.plan)
    if not price_id:
        raise HTTPException(status_code=503, detail="billing_not_configured")

    with session_scope() as session:
        get_current_user_row(session, account_id=user_id)

    form = {
        "mode": "subscription",
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "success_url": payload.success_url,
        "cancel_url": payload.cancel_url,
        "client_reference_id": str(user_id),
        "metadata[plan]": payload.plan,
        "metadata[user_id]": str(user_id),
        # Propagate onto the subscription so renewal invoices can be attributed.
        "subscription_data[metadata][plan]": payload.plan,
        "subscription_data[metadata][user_id]": str(user_id),
    }

    data = _stripe_post("/v1/checkout/sessions", form, secret)
    url = data.get("url")
    if not isinstance(url, str) or not url:
        logger.warning("stripe subscription response missing url: %s", str(data)[:500])
        raise HTTPException(status_code=502, detail="stripe_error")
    return CreateCheckoutResponse(url=url)


@router.get("/subscription", response_model=SubscriptionDTO)
async def get_subscription(user_id: str = Depends(require_user_id)) -> SubscriptionDTO:
    with session_scope() as session:
        row = session.execute(
            select(SubscriptionRow)
            .where(SubscriptionRow.user_id == user_id, SubscriptionRow.status == "active")
            .order_by(SubscriptionRow.created_at.desc())
        ).scalars().first()
        if row is None:
            return SubscriptionDTO(active=False)
        return SubscriptionDTO(
            active=row.status == "active",
            plan=row.plan,
            status=row.status,
            currentPeriodEnd=(row.current_period_end.isoformat() if row.current_period_end else None),
            monthlyCredits=_SUB_PLAN_CREDITS.get(row.plan),
        )


@router.post("/create-portal-session", response_model=PortalResponse)
async def create_portal_session(
    payload: PortalRequest,
    user_id: str = Depends(require_user_id),
) -> PortalResponse:
    """Open the Stripe Billing Portal so the user can manage/cancel their plan."""
    secret = _stripe_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="billing_not_configured")

    with session_scope() as session:
        row = session.execute(
            select(SubscriptionRow)
            .where(SubscriptionRow.user_id == user_id)
            .order_by(SubscriptionRow.created_at.desc())
        ).scalars().first()
        customer_id = row.stripe_customer_id if row else None
    if not customer_id:
        raise HTTPException(status_code=404, detail="no_subscription")

    data = _stripe_post(
        "/v1/billing_portal/sessions",
        {"customer": customer_id, "return_url": payload.return_url},
        secret,
    )
    url = data.get("url")
    if not isinstance(url, str) or not url:
        raise HTTPException(status_code=502, detail="stripe_error")
    return PortalResponse(url=url)


# ---------------------------------------------------------------------------
# Webhook handlers
# ---------------------------------------------------------------------------


def _upsert_subscription(
    sub_id: str,
    user_id: str,
    plan: str,
    status: str,
    customer_id: Optional[str],
    period_end: Optional[datetime],
) -> None:
    with session_scope() as session:
        row = session.execute(
            select(SubscriptionRow).where(SubscriptionRow.id == sub_id)
        ).scalar_one_or_none()
        now = datetime.utcnow()
        if row is None:
            session.add(
                SubscriptionRow(
                    id=sub_id,
                    user_id=user_id,
                    plan=plan,
                    status=status,
                    stripe_customer_id=customer_id,
                    current_period_end=period_end,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            row.user_id = user_id
            row.plan = plan
            row.status = status
            if customer_id:
                row.stripe_customer_id = customer_id
            if period_end is not None:
                row.current_period_end = period_end
            row.updated_at = now
        session.flush()


def _handle_credit_checkout(obj: Dict[str, Any]) -> Dict[str, Any]:
    """One-time credit-pack purchase via Checkout."""
    user_id = obj.get("client_reference_id")
    metadata = obj.get("metadata") or {}
    tier = metadata.get("tier")
    if not user_id or tier not in _TIER_AMOUNTS:
        logger.warning("stripe webhook: missing/invalid user_id=%r tier=%r", user_id, tier)
        return {"received": True, "credited": False}
    session_id = obj.get("id") or ""
    desc = f"stripe_{tier}_{session_id}" if session_id else f"stripe_{tier}"
    granted = _grant_credits_idempotent(str(user_id), _TIER_AMOUNTS[tier], "purchase", desc)
    if not granted:
        return {"received": True, "credited": False, "reason": "duplicate_or_unknown"}
    return {"received": True, "credited": True, "amount": _TIER_AMOUNTS[tier], "tier": tier}


def _handle_subscription_checkout(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Subscription Checkout completed: record the subscription and grant the
    first billing period's credit allowance."""
    user_id = obj.get("client_reference_id")
    metadata = obj.get("metadata") or {}
    plan = metadata.get("plan")
    sub_id = obj.get("subscription")
    customer_id = obj.get("customer")
    if not user_id or plan not in _SUB_PLAN_CREDITS or not sub_id:
        logger.warning("stripe sub checkout: missing user_id=%r plan=%r sub=%r", user_id, plan, sub_id)
        return {"received": True, "credited": False}
    _upsert_subscription(str(sub_id), str(user_id), str(plan), "active", str(customer_id) if customer_id else None, None)
    granted = _grant_credits_idempotent(str(user_id), _SUB_PLAN_CREDITS[plan], "subscription", f"sub_init_{sub_id}")
    return {"received": True, "subscription": sub_id, "credited": granted, "plan": plan}


def _handle_invoice_paid(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Recurring subscription invoice paid: grant the monthly allowance on
    renewals (the first invoice is granted by the checkout event)."""
    sub_id = obj.get("subscription")
    invoice_id = obj.get("id") or ""
    reason = obj.get("billing_reason")
    if not sub_id:
        return {"received": True, "ignored": "no_subscription"}
    if reason == "subscription_create":
        return {"received": True, "skipped": "initial_handled_by_checkout"}
    with session_scope() as session:
        row = session.execute(
            select(SubscriptionRow).where(SubscriptionRow.id == str(sub_id))
        ).scalar_one_or_none()
        if row is None:
            logger.warning("invoice paid for unknown subscription %s", sub_id)
            return {"received": True, "credited": False, "reason": "unknown_subscription"}
        user_id, plan = row.user_id, row.plan
    amount = _SUB_PLAN_CREDITS.get(plan)
    if not amount:
        return {"received": True, "credited": False, "reason": "unknown_plan"}
    granted = _grant_credits_idempotent(user_id, amount, "subscription", f"sub_invoice_{invoice_id}")
    return {"received": True, "credited": granted, "plan": plan, "amount": amount}


def _handle_subscription_change(event_type: str, obj: Dict[str, Any]) -> Dict[str, Any]:
    """customer.subscription.updated/deleted: track status + period end."""
    sub_id = obj.get("id")
    if not sub_id:
        return {"received": True, "ignored": "no_id"}
    status = "canceled" if event_type == "customer.subscription.deleted" else str(obj.get("status") or "active")
    period_end = obj.get("current_period_end")
    period_dt: Optional[datetime] = None
    if isinstance(period_end, (int, float)):
        try:
            period_dt = datetime.utcfromtimestamp(int(period_end))
        except Exception:
            period_dt = None
    with session_scope() as session:
        row = session.execute(
            select(SubscriptionRow).where(SubscriptionRow.id == str(sub_id))
        ).scalar_one_or_none()
        if row is None:
            return {"received": True, "updated": False, "reason": "unknown_subscription"}
        row.status = status
        if period_dt is not None:
            row.current_period_end = period_dt
        row.updated_at = datetime.utcnow()
        session.flush()
    return {"received": True, "updated": True, "status": status}


@router.post("/webhook")
async def stripe_webhook(request: Request) -> Dict[str, Any]:
    secret = _webhook_secret()
    raw = await request.body()
    # Fail closed: without a signing secret we cannot verify the event, and
    # processing it anyway would let anyone forge a checkout.session.completed
    # to self-credit. Require STRIPE_WEBHOOK_SECRET to be configured.
    if not secret:
        logger.warning("stripe webhook hit but STRIPE_WEBHOOK_SECRET is not configured")
        raise HTTPException(status_code=503, detail="webhook_not_configured")
    sig_header = request.headers.get("stripe-signature", "")
    if not _verify_signature(raw, sig_header, secret):
        raise HTTPException(status_code=400, detail="invalid_signature")

    try:
        event = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_payload")

    event_type = event.get("type")
    obj = (event.get("data") or {}).get("object") or {}

    if event_type == "checkout.session.completed":
        if (obj.get("mode") == "subscription") or obj.get("subscription"):
            return _handle_subscription_checkout(obj)
        return _handle_credit_checkout(obj)
    if event_type == "invoice.payment_succeeded":
        return _handle_invoice_paid(obj)
    if event_type in ("customer.subscription.deleted", "customer.subscription.updated"):
        return _handle_subscription_change(event_type, obj)
    return {"received": True, "ignored": event_type or "unknown"}


__all__ = ["router"]
