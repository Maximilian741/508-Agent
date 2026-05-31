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
import secrets
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
from app.db.models import CertificateRow, CreditLedgerRow, SubscriptionRow, UserRow
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

# Recurring subscription plans. "grant" = credits added per paid invoice;
# "monthly" = effective monthly credits (for display). Annual plans bill once a
# year and grant 12x up front. Each maps to a recurring Stripe Price via
# "price_env". Users still spend credits per remediation, so a plan is an
# allowance (and certificates are free for active subscribers).
_SUB_PLANS: Dict[str, Dict[str, Any]] = {
    "team": {"label": "Team", "interval": "month", "grant": 1000, "monthly": 1000, "seats": 3, "price_env": "STRIPE_PRICE_TEAM"},
    "team_annual": {"label": "Team", "interval": "year", "grant": 12000, "monthly": 1000, "seats": 3, "price_env": "STRIPE_PRICE_TEAM_ANNUAL"},
    "business": {"label": "Business", "interval": "month", "grant": 6000, "monthly": 6000, "seats": 10, "price_env": "STRIPE_PRICE_BUSINESS"},
    "business_annual": {"label": "Business", "interval": "year", "grant": 72000, "monthly": 6000, "seats": 10, "price_env": "STRIPE_PRICE_BUSINESS_ANNUAL"},
}

# Display-only monthly list price (USD) per plan, used for the admin MRR
# estimate. Override via STRIPE_PRICE_<PLAN>_USD if your real prices differ.
_PLAN_PRICE_USD: Dict[str, int] = {
    "team": 49,
    "team_annual": 49,
    "business": 299,
    "business_annual": 299,
}


def _plan_grant(plan: str) -> int:
    return int(_SUB_PLANS.get(plan, {}).get("grant", 0))


def plan_seats(plan: str) -> int:
    """Seat limit (max members incl. owner) granted by a subscription plan."""
    return int(_SUB_PLANS.get(plan, {}).get("seats", 1))


def plan_price_usd(plan: str) -> int:
    """Estimated monthly list price (USD) for a plan, for MRR reporting."""
    meta = _SUB_PLANS.get(plan)
    env_name = f"STRIPE_PRICE_{plan.upper()}_USD"
    raw = os.environ.get(env_name, "").strip()
    if raw.isdigit():
        return int(raw)
    return int(_PLAN_PRICE_USD.get(plan, 0)) if meta else 0


def active_subscription_for(user_id: str):
    """Return the user's most-recent active SubscriptionRow, or None."""
    with session_scope() as session:
        row = session.execute(
            select(SubscriptionRow)
            .where(SubscriptionRow.user_id == user_id, SubscriptionRow.status == "active")
            .order_by(SubscriptionRow.created_at.desc())
        ).scalars().first()
        if row is not None:
            session.expunge(row)
        return row


def _plan_monthly(plan: str) -> Optional[int]:
    meta = _SUB_PLANS.get(plan)
    return int(meta["monthly"]) if meta else None


# Conformance certificates are free for active subscribers; pay-as-you-go users
# spend this many credits per issued certificate.
_CERTIFICATE_CREDIT_COST = 2

# Overage: when an active subscriber (with overage on) runs out of credits, we
# auto-charge this pack off-session instead of blocking them.
_OVERAGE_CREDITS = 200
_OVERAGE_PRICE_CENTS = 1000  # $10


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
    meta = _SUB_PLANS.get(plan)
    if not meta:
        return None
    val = os.environ.get(str(meta["price_env"]), "").strip()
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
    label: str
    interval: str  # "month" | "year"
    monthlyCredits: int
    priceConfigured: bool


class BillingConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    tiers: List[BillingTierDTO]
    subscriptionPlans: List[SubscriptionPlanDTO] = Field(default_factory=list)


class CreateSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: Literal["team", "team_annual", "business", "business_annual"]
    success_url: str = Field(min_length=1, max_length=2000)
    cancel_url: str = Field(min_length=1, max_length=2000)


class SubscriptionDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool
    plan: Optional[str] = None
    status: Optional[str] = None
    currentPeriodEnd: Optional[str] = None
    monthlyCredits: Optional[int] = None
    overageEnabled: bool = False


class PortalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    return_url: str = Field(min_length=1, max_length=2000)


class OverageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class PortalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str


class IssueCertificateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=400)
    conformanceClaim: str = Field(min_length=1, max_length=600)
    score: int = Field(default=0, ge=0, le=100)
    fixedCount: int = Field(default=0, ge=0)
    remainingCount: int = Field(default=0, ge=0)


class CertificateDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    certificateId: str
    issuedAt: str
    issuedTo: Optional[str] = None
    filename: str
    conformanceClaim: str
    score: int
    fixedCount: int
    remainingCount: int
    paidWith: str  # "subscription" | "credits"
    verifyUrl: str


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


def _has_active_subscription(user_id: str) -> bool:
    with session_scope() as session:
        row = session.execute(
            select(SubscriptionRow).where(
                SubscriptionRow.user_id == user_id,
                SubscriptionRow.status == "active",
            )
        ).scalars().first()
        return row is not None


def _verify_url_for(cert_id: str) -> str:
    # Human-friendly verification page (the frontend route), which in turn calls
    # the public GET /billing/certificate/{id} API.
    base = (os.getenv("PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    return f"{base}/verify?cert={cert_id}" if base else f"/verify?cert={cert_id}"


def _overage_eligible(user_id: str) -> Optional[str]:
    """Return the Stripe customer id if the user can auto-charge overage."""
    with session_scope() as session:
        row = session.execute(
            select(SubscriptionRow)
            .where(SubscriptionRow.user_id == user_id, SubscriptionRow.status == "active")
            .order_by(SubscriptionRow.created_at.desc())
        ).scalars().first()
        if row is None or not row.overage_enabled or not row.stripe_customer_id:
            return None
        return row.stripe_customer_id


def _charge_overage(customer_id: str, user_id: str) -> Optional[str]:
    """Charge the overage pack off-session; returns a charge id on success.

    Honours OVERAGE_TEST_MODE ("succeed"/"fail") so the flow is exercisable
    without live Stripe; in production it creates a real off-session
    PaymentIntent against the customer's default payment method.
    """
    test_mode = os.getenv("OVERAGE_TEST_MODE", "").strip().lower()
    if test_mode == "succeed":
        return "pi_test_" + secrets.token_hex(6)
    if test_mode == "fail":
        return None
    secret = _stripe_secret()
    if not secret:
        return None
    form = {
        "amount": str(_OVERAGE_PRICE_CENTS),
        "currency": "usd",
        "customer": customer_id,
        "confirm": "true",
        "off_session": "true",
        "description": "508 Agent credit overage",
        "metadata[kind]": "overage",
        "metadata[user_id]": user_id,
    }
    try:
        data = _stripe_post("/v1/payment_intents", form, secret)
    except HTTPException:
        return None
    if str(data.get("status")) == "succeeded":
        pi_id = data.get("id")
        return str(pi_id) if pi_id else None
    return None


def ensure_balance_for(user_id: str, needed: int) -> None:
    """Best-effort overage top-up before a credit spend.

    If the user is short on credits but is an overage-eligible subscriber,
    auto-charge an overage pack and grant the credits. Never raises; if it
    can't top up, the subsequent spend fails as usual (402).

    Charges at most one pack per call. Real per-document spends are <= 5
    credits and a pack is 200, so one pack always covers a single spend.
    Grants are idempotent on the charge id. On a single worker the blocking
    Stripe call serializes concurrent spends; a multi-instance deploy should
    add a per-user lock here to close the concurrent-charge window.
    """
    if needed <= 0:
        return
    with session_scope() as session:
        user = session.execute(select(UserRow).where(UserRow.id == user_id)).scalar_one_or_none()
        balance = int(user.credits_balance or 0) if user else 0
    if balance >= needed:
        return
    customer_id = _overage_eligible(user_id)
    if not customer_id:
        return
    try:
        charge_id = _charge_overage(customer_id, user_id)
    except Exception as exc:  # never block the request on overage failure
        logger.warning("overage charge failed for %s: %s", user_id, exc)
        return
    if charge_id:
        _grant_credits_idempotent(user_id, _OVERAGE_CREDITS, "overage", f"overage_{charge_id}")


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
            label=str(meta["label"]),
            interval=str(meta["interval"]),
            monthlyCredits=int(meta["monthly"]),
            priceConfigured=_sub_price_id_for(p) is not None,
        )
        for p, meta in _SUB_PLANS.items()
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
            monthlyCredits=_plan_monthly(row.plan),
            overageEnabled=bool(row.overage_enabled),
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


@router.post("/issue-certificate", response_model=CertificateDTO)
async def issue_certificate(
    payload: IssueCertificateRequest,
    user_id: str = Depends(require_user_id),
) -> CertificateDTO:
    """Issue a verifiable conformance certificate.

    Free for active subscribers; otherwise spends a small number of credits
    (402 if the caller has neither). The certificate is recorded so a third
    party can verify it at GET /billing/certificate/{id}.
    """
    from app.api.credits import InsufficientCreditsError, spend_credits_for_user
    from app.api.teams import resolve_credit_user_id

    # Team members inherit the owner's subscription benefit and shared wallet.
    holder = resolve_credit_user_id(user_id)
    paid_with = "subscription"
    if not _has_active_subscription(holder):
        try:
            spend_credits_for_user(
                user_id=holder,
                amount=_CERTIFICATE_CREDIT_COST,
                description="certificate_issued",
            )
        except InsufficientCreditsError:
            raise HTTPException(status_code=402, detail="insufficient_credits")
        paid_with = "credits"

    now = datetime.utcnow()
    cert_id = secrets.token_hex(8)  # 16 hex chars, unguessable
    with session_scope() as session:
        user = session.execute(
            select(UserRow).where(UserRow.id == user_id)
        ).scalar_one_or_none()
        email = user.email if user else None
        session.add(
            CertificateRow(
                id=cert_id,
                user_id=user_id,
                issued_email=email,
                filename=payload.filename[:400],
                conformance_claim=payload.conformanceClaim[:600],
                score=int(payload.score),
                fixed_count=int(payload.fixedCount),
                remaining_count=int(payload.remainingCount),
                paid_with=paid_with,
                issued_at=now,
            )
        )
        session.flush()

    return CertificateDTO(
        certificateId=cert_id,
        issuedAt=now.isoformat(),
        issuedTo=email,
        filename=payload.filename,
        conformanceClaim=payload.conformanceClaim,
        score=int(payload.score),
        fixedCount=int(payload.fixedCount),
        remainingCount=int(payload.remainingCount),
        paidWith=paid_with,
        verifyUrl=_verify_url_for(cert_id),
    )


@router.get("/certificate/{cert_id}", response_model=CertificateDTO)
async def verify_certificate(cert_id: str) -> CertificateDTO:
    """Public: verify a previously-issued certificate by its id."""
    with session_scope() as session:
        row = session.execute(
            select(CertificateRow).where(CertificateRow.id == cert_id)
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="certificate_not_found")
        return CertificateDTO(
            certificateId=row.id,
            issuedAt=(row.issued_at.isoformat() if row.issued_at else ""),
            issuedTo=row.issued_email,
            filename=row.filename,
            conformanceClaim=row.conformance_claim,
            score=int(row.score),
            fixedCount=int(row.fixed_count),
            remainingCount=int(row.remaining_count),
            paidWith=row.paid_with,
            verifyUrl=_verify_url_for(row.id),
        )


@router.post("/overage", response_model=SubscriptionDTO)
async def set_overage(
    payload: OverageRequest,
    user_id: str = Depends(require_user_id),
) -> SubscriptionDTO:
    """Turn auto-overage on/off for the caller's active subscription."""
    with session_scope() as session:
        row = session.execute(
            select(SubscriptionRow)
            .where(SubscriptionRow.user_id == user_id, SubscriptionRow.status == "active")
            .order_by(SubscriptionRow.created_at.desc())
        ).scalars().first()
        if row is None:
            raise HTTPException(status_code=404, detail="no_subscription")
        row.overage_enabled = bool(payload.enabled)
        row.updated_at = datetime.utcnow()
        session.flush()
        return SubscriptionDTO(
            active=row.status == "active",
            plan=row.plan,
            status=row.status,
            currentPeriodEnd=(row.current_period_end.isoformat() if row.current_period_end else None),
            monthlyCredits=_plan_monthly(row.plan),
            overageEnabled=bool(row.overage_enabled),
        )


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
    if not user_id or plan not in _SUB_PLANS or not sub_id:
        logger.warning("stripe sub checkout: missing user_id=%r plan=%r sub=%r", user_id, plan, sub_id)
        return {"received": True, "credited": False}
    _upsert_subscription(str(sub_id), str(user_id), str(plan), "active", str(customer_id) if customer_id else None, None)
    granted = _grant_credits_idempotent(str(user_id), _plan_grant(plan), "subscription", f"sub_init_{sub_id}")
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
    amount = _plan_grant(plan)
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
