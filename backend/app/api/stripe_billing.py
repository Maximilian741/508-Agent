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
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.auth import get_current_user_row
from app.api.deps import require_user_id
from app.db.models import (
    CertificateRow,
    CreditLedgerRow,
    SubscriptionRow,
    TeamMemberRow,
    TeamRow,
    UserRow,
)
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
# estimate. MUST match the public pricing on the billing page (Team $99/mo or
# $990/yr; Business $499/mo or $4,990/yr — annual shown as monthly-equivalent
# for MRR). Override via STRIPE_PRICE_<PLAN>_USD if your real prices differ.
_PLAN_PRICE_USD: Dict[str, int] = {
    "team": 99,
    "team_annual": 99,
    "business": 499,
    "business_annual": 499,
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

# At most this many overage packs are charged to one wallet in any rolling 24
# hours. Past it the spend gets a plain 402 and the customer buys a pack (or
# raises the cap) deliberately. Override with OVERAGE_MAX_PACKS_PER_DAY; 0
# turns auto-overage off for everyone.
_OVERAGE_WINDOW = timedelta(hours=24)
_DEFAULT_OVERAGE_MAX_PACKS_PER_DAY = 3

# Stripe never reactivates a subscription in these states, so no later (or
# redelivered) event may move our row out of them.
_TERMINAL_SUB_STATUSES = frozenset({"canceled", "incomplete_expired"})
# Our row's status while the first payment of a Checkout subscription has not
# cleared yet (a delayed method such as ACH). Unlocks nothing.
_PENDING_SUB_STATUS = "incomplete"
# Checkout payment_status values that mean the money is in (or none was owed,
# e.g. an operator's 100%-off coupon). "unpaid" means a delayed payment is
# still settling: grant on checkout.session.async_payment_succeeded instead.
_PAID_CHECKOUT_STATUSES = frozenset({"paid", "no_payment_required"})


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


def _plan_for_price(price_id: Optional[str]) -> Optional[str]:
    """Map a Stripe price id back to a plan via the STRIPE_PRICE_* config.

    The price is the only trustworthy record of what a customer is billed for:
    a plan switch in the customer portal changes it and nothing else. An id we
    don't recognise maps to None, which must never grant anything.
    """
    if not price_id:
        return None
    for plan in _SUB_PLANS:
        if _sub_price_id_for(plan) == price_id:
            return plan
    return None


def _stripe_id(value: Any) -> Optional[str]:
    """A Stripe reference is either an id string or an expanded object."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        inner = value.get("id")
        return str(inner) if inner else None
    return None


def _single_plan(price_ids: List[Optional[str]]) -> Optional[str]:
    """The one known plan among ``price_ids``; None if none or ambiguous."""
    plans = {p for p in (_plan_for_price(pid) for pid in price_ids) if p}
    return next(iter(plans)) if len(plans) == 1 else None


def _subscription_price_ids(obj: Dict[str, Any]) -> List[Optional[str]]:
    items = (obj.get("items") or {}).get("data") or []
    return [_stripe_id(item.get("price")) for item in items if isinstance(item, dict)]


def _subscription_period_end(obj: Dict[str, Any]) -> Optional[datetime]:
    # Newer Stripe API versions moved current_period_end onto the items.
    raw = obj.get("current_period_end")
    if raw is None:
        items = (obj.get("items") or {}).get("data") or []
        if items and isinstance(items[0], dict):
            raw = items[0].get("current_period_end")
    if not isinstance(raw, (int, float)):
        return None
    try:
        return datetime.utcfromtimestamp(int(raw))
    except Exception:
        return None


def _invoice_subscription_id(obj: Dict[str, Any]) -> Optional[str]:
    # Newer Stripe API versions moved invoice.subscription under parent.
    sub = _stripe_id(obj.get("subscription"))
    if sub:
        return sub
    details = (obj.get("parent") or {}).get("subscription_details") or {}
    return _stripe_id(details.get("subscription"))


def _invoice_billed_plan(obj: Dict[str, Any]) -> Optional[str]:
    """The plan this invoice actually bills for, from its non-proration lines."""
    price_ids: List[Optional[str]] = []
    for line in (obj.get("lines") or {}).get("data") or []:
        if not isinstance(line, dict):
            continue
        item_details = (line.get("parent") or {}).get("subscription_item_details") or {}
        if line.get("proration") or item_details.get("proration"):
            continue
        pricing = (line.get("pricing") or {}).get("price_details") or {}
        price_ids.append(_stripe_id(line.get("price")) or _stripe_id(pricing.get("price")))
    return _single_plan(price_ids)


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

    # The certificate is bound to a server-side analysis; the client supplies
    # only which analyzed document to certify (never the score/claim).
    documentId: str = Field(min_length=1, max_length=128)


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


def _summary_claim(score: int, fixed: int, remaining: int) -> str:
    """Build the certificate's claim text SERVER-SIDE.

    Deliberately honest: this is an automated-remediation processing record, not
    a formal WCAG / Section 508 conformance determination (which requires manual
    evaluation of every applicable success criterion — including ones this tool
    does not yet check, e.g. colour contrast). We never let the client supply a
    "Conforms to ..." string, because a verifiable certificate that attests an
    unverified, caller-supplied claim is worse than no certificate at all.
    """
    parts = [
        "Automated accessibility remediation summary produced by 508 Agent.",
        f"{int(fixed)} issue(s) were automatically remediated",
    ]
    if remaining > 0:
        parts[-1] += f" and {int(remaining)} item(s) were flagged for manual review"
    parts[-1] += f" (automated check score {int(score)}/100)."
    parts.append(
        "This is an automated processing record, not a formal WCAG 2.1 / "
        "Section 508 conformance certification. A conformance determination "
        "requires manual evaluation of all applicable success criteria."
    )
    return " ".join(parts)


def _overage_max_packs_per_day() -> int:
    raw = os.environ.get("OVERAGE_MAX_PACKS_PER_DAY", "").strip()
    return int(raw) if raw.isdigit() else _DEFAULT_OVERAGE_MAX_PACKS_PER_DAY


def _can_trigger_overage(wallet_id: str, actor_id: Optional[str]) -> bool:
    """Only the wallet owner or an admin of the owner's team may cause an
    off-session charge on the owner's card. A plain member gets a 402 instead.

    ``actor_id=None`` means the caller did not say who is acting and is
    treated as the wallet owner (the legacy call shape).
    """
    if actor_id is None or actor_id == wallet_id:
        return True
    with session_scope() as session:
        member = session.execute(
            select(TeamMemberRow).where(TeamMemberRow.user_id == actor_id)
        ).scalar_one_or_none()
        if member is None or member.role != "admin":
            return False
        team = session.execute(
            select(TeamRow).where(TeamRow.id == member.team_id)
        ).scalar_one_or_none()
        return team is not None and team.owner_id == wallet_id


def _overage_eligible(user_id: str, actor_id: Optional[str] = None) -> Optional[str]:
    """Return the Stripe customer id if an overage pack may be charged now:
    an active subscription with overage on, an actor allowed to charge the
    owner's card, and the rolling 24-hour pack cap not yet reached."""
    cap = _overage_max_packs_per_day()
    if cap <= 0 or not _can_trigger_overage(user_id, actor_id):
        return None
    with session_scope() as session:
        row = session.execute(
            select(SubscriptionRow)
            .where(SubscriptionRow.user_id == user_id, SubscriptionRow.status == "active")
            .order_by(SubscriptionRow.created_at.desc())
        ).scalars().first()
        if row is None or not row.overage_enabled or not row.stripe_customer_id:
            return None
        recent = session.execute(
            select(CreditLedgerRow.id).where(
                CreditLedgerRow.user_id == user_id,
                CreditLedgerRow.kind == "overage",
                CreditLedgerRow.at >= datetime.utcnow() - _OVERAGE_WINDOW,
            )
        ).all()
        if len(recent) >= cap:
            logger.info("overage cap reached for %s (%d packs in 24h)", user_id, len(recent))
            return None
        return row.stripe_customer_id


def _charge_overage(customer_id: str, user_id: str) -> Optional[str]:
    """Charge the overage pack off-session; returns a charge id on success.

    Honours OVERAGE_TEST_MODE ("succeed"/"fail") so the flow is exercisable
    without live Stripe; in production it creates a real off-session
    PaymentIntent against the customer's default payment method.
    """
    # OVERAGE_TEST_MODE grants credits off a synthetic charge — it must NEVER be
    # honoured in production, or a leftover/mis-set env var would hand out free
    # credits with no real Stripe charge. Ignore it outside dev/test.
    from app.config import get_settings

    if get_settings().environment != "production":
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


def ensure_balance_for(user_id: str, needed: int, actor_id: Optional[str] = None) -> None:
    """Best-effort overage top-up before a credit spend.

    If the wallet ``user_id`` is short on credits but is an overage-eligible
    subscriber, auto-charge an overage pack and grant the credits. Never
    raises; if it can't top up, the subsequent spend fails as usual (402).

    Charges at most one pack per call, and only when that pack actually covers
    the shortfall: a charge that still ends in a 402 takes the customer's money
    for a spend that never happens. ``actor_id`` is the real caller when it
    differs from the wallet (a team member spending the owner's wallet); only
    the owner or a team admin may trigger the charge. Packs are capped per
    rolling 24 hours (see _overage_eligible). Grants are idempotent on the
    charge id and happen inside the per-user lock, so a concurrent spend sees
    the topped-up balance instead of charging again.
    """
    if needed <= 0:
        return
    with session_scope() as session:
        # Postgres: take a per-user advisory lock for the duration of this
        # transaction so two workers can't both see "balance short" and each
        # fire a real Stripe charge. No-op on SQLite (single-writer anyway).
        try:
            if session.get_bind().dialect.name == "postgresql":
                from sqlalchemy import text as _sql_text

                session.execute(
                    _sql_text("SELECT pg_advisory_xact_lock(hashtext(:uid))"),
                    {"uid": str(user_id)},
                )
        except Exception:  # pragma: no cover - lock is belt-and-braces
            logger.debug("advisory lock unavailable", exc_info=True)
        user = session.execute(select(UserRow).where(UserRow.id == user_id)).scalar_one_or_none()
        balance = int(user.credits_balance or 0) if user else 0
        if balance >= needed:
            return
        if balance + _OVERAGE_CREDITS < needed:
            return
        customer_id = _overage_eligible(user_id, actor_id=actor_id)
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
    """Issue a verifiable remediation-summary certificate.

    The score and issue counts are taken from the caller's own server-side
    ``/pipeline/analyze`` of this document (looked up by ``documentId``), never
    from the client — so a caller cannot mint a certificate asserting more than
    the server actually measured. Requires that the document was analyzed first
    (400 otherwise). Free for active subscribers; otherwise spends credits.
    """
    from app.api.credits import InsufficientCreditsError, spend_credits_for_user
    from app.api.teams import resolve_credit_user_id
    from app.db.models import AnalysisResultRow

    # 1. Bind to a real, server-computed analysis owned by the caller.
    rid = f"{user_id}::{payload.documentId}"
    with session_scope() as session:
        rec = session.get(AnalysisResultRow, rid)
        if rec is None:
            raise HTTPException(status_code=400, detail="analyze_required")
        filename = rec.filename or payload.documentId
        score_val = int(rec.score)
        fixed_val = int(rec.fixed_automatically)
        remaining_val = int(rec.pending_manual)

    # 1b. Idempotency: a double-click / retry / second tab must not mint a
    # second certificate AND charge twice. If an identical certificate (same
    # owner, document, and server numbers) was issued in the last few minutes,
    # return it unchanged instead of charging again.
    _DEDUP_WINDOW = timedelta(minutes=10)
    with session_scope() as session:
        recent = (
            session.execute(
                select(CertificateRow)
                .where(CertificateRow.user_id == user_id)
                .where(CertificateRow.filename == str(filename)[:400])
                .where(CertificateRow.score == score_val)
                .where(CertificateRow.fixed_count == fixed_val)
                .where(CertificateRow.remaining_count == remaining_val)
                .where(CertificateRow.issued_at >= datetime.utcnow() - _DEDUP_WINDOW)
                .order_by(CertificateRow.issued_at.desc())
            )
            .scalars()
            .first()
        )
        if recent is not None:
            return CertificateDTO(
                certificateId=recent.id,
                issuedAt=recent.issued_at.isoformat(),
                issuedTo=recent.issued_email,
                filename=str(recent.filename),
                conformanceClaim=recent.conformance_claim,
                score=int(recent.score),
                fixedCount=int(recent.fixed_count),
                remainingCount=int(recent.remaining_count),
                paidWith=recent.paid_with,
                verifyUrl=_verify_url_for(recent.id),
            )

    # 2. Gate on subscription / credits (team members use the owner's wallet).
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

    # 3. Honest claim, generated server-side from the server's own numbers.
    claim = _summary_claim(score_val, fixed_val, remaining_val)

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
                filename=str(filename)[:400],
                conformance_claim=claim[:600],
                score=score_val,
                fixed_count=fixed_val,
                remaining_count=remaining_val,
                paid_with=paid_with,
                issued_at=now,
            )
        )
        session.flush()

    return CertificateDTO(
        certificateId=cert_id,
        issuedAt=now.isoformat(),
        issuedTo=email,
        filename=str(filename),
        conformanceClaim=claim,
        score=score_val,
        fixedCount=fixed_val,
        remainingCount=remaining_val,
        paidWith=paid_with,
        verifyUrl=_verify_url_for(cert_id),
    )


def _mask_email(email: str) -> str:
    """Redact an account email for the PUBLIC certificate endpoint. A cert link
    is shared with auditors/agencies, so the raw email must not be harvestable:
    show the first character + the domain (enough to recognise the issuing org,
    not enough to harvest the address). e.g. alice@example.com -> a***@example.com."""
    e = (email or "").strip()
    if "@" not in e:
        return "verified account"
    local, _, domain = e.partition("@")
    masked_local = (local[0] + "***") if local else "***"
    return f"{masked_local}@{domain}"


def _public_filename(filename: str) -> str:
    """Redact the original filename on the PUBLIC endpoint — the document name is
    often business-sensitive (e.g. "Q4-Financial-Report.docx"). Preserve only the
    file type so the verifier can see what kind of document was certified."""
    name = (filename or "").strip()
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext and len(ext) <= 5 and ext.isalnum():
        return f"document.{ext}"
    return "document"


@router.get("/certificate/{cert_id}", response_model=CertificateDTO)
async def verify_certificate(cert_id: str) -> CertificateDTO:
    """Public: verify a previously-issued certificate by its id.

    This endpoint is unauthenticated (cert links are meant to be shared), so it
    must NOT leak the issuer's raw email or the original document filename. The
    authenticated issue response returns full detail to the owner; here we redact.
    """
    with session_scope() as session:
        row = session.execute(
            select(CertificateRow).where(CertificateRow.id == cert_id)
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="certificate_not_found")
        return CertificateDTO(
            certificateId=row.id,
            issuedAt=(row.issued_at.isoformat() if row.issued_at else ""),
            issuedTo=_mask_email(row.issued_email),
            filename=_public_filename(row.filename),
            conformanceClaim=row.conformance_claim,
            score=int(row.score),
            fixedCount=int(row.fixed_count),
            remainingCount=int(row.remaining_count),
            paidWith=row.paid_with,
            verifyUrl=_verify_url_for(row.id),
        )


# ---------------------------------------------------------------------------
# Embeddable badge (sellable / viral: every embed links back to the public
# verification page). The badge is HONEST — it surfaces the certificate's own
# server-computed score (the same number shown on /verify) and links to the
# full report with its careful "automated summary, not a formal determination"
# claim. It never asserts a conformance level on its own.
# ---------------------------------------------------------------------------

# Score thresholds -> shields-style colours.
_BADGE_THRESHOLDS = [(90, "#2da44e"), (75, "#3f9142"), (60, "#dfb317"), (0, "#e0843b")]


def _badge_color(score: int) -> str:
    for threshold, color in _BADGE_THRESHOLDS:
        if score >= threshold:
            return color
    return "#9f9f9f"


def _badge_text_width(text: str) -> int:
    # Approximate width for an 11px Verdana-ish font; generous so text never clips.
    return int(len(text) * 6.5) + 12


def _render_badge_svg(label: str, value: str, color: str) -> str:
    import html as _html

    label = _html.escape(label)
    value = _html.escape(value)
    lw = _badge_text_width(label)
    vw = _badge_text_width(value)
    total = lw + vw
    label_mid = lw / 2
    value_mid = lw + vw / 2
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" '
        f'role="img" aria-label="{label}: {value}">'
        f'<title>{label}: {value}</title>'
        f'<linearGradient id="s" x2="0" y2="100%">'
        f'<stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        f'<stop offset="1" stop-opacity=".1"/></linearGradient>'
        f'<clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>'
        f'<g clip-path="url(#r)">'
        f'<rect width="{lw}" height="20" fill="#555"/>'
        f'<rect x="{lw}" width="{vw}" height="20" fill="{color}"/>'
        f'<rect width="{total}" height="20" fill="url(#s)"/></g>'
        f'<g fill="#fff" text-anchor="middle" '
        f'font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">'
        f'<text x="{label_mid:.0f}" y="15" fill="#010101" fill-opacity=".3">{label}</text>'
        f'<text x="{label_mid:.0f}" y="14">{label}</text>'
        f'<text x="{value_mid:.0f}" y="15" fill="#010101" fill-opacity=".3">{value}</text>'
        f'<text x="{value_mid:.0f}" y="14">{value}</text>'
        f'</g></svg>'
    )


@router.get("/certificate/{cert_id}/badge.svg")
async def certificate_badge(cert_id: str) -> Response:
    """Public SVG badge for a certificate. Embed it (wrapped in a link to the
    verify page) on a site to show an accessibility report exists. Renders a
    neutral 'not found' badge for an unknown id so a stale embed degrades
    gracefully rather than showing a broken image."""
    with session_scope() as session:
        row = session.execute(
            select(CertificateRow).where(CertificateRow.id == cert_id)
        ).scalar_one_or_none()
        # Read the score while the session is open (the row detaches on exit).
        score = max(0, min(100, int(row.score))) if row is not None else None

    if score is None:
        svg = _render_badge_svg("508 Agent", "not found", "#9f9f9f")
    else:
        svg = _render_badge_svg("508 Agent", f"{score}/100", _badge_color(score))

    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "public, max-age=300",
            # SVG badges are safe to embed cross-origin as <img>.
            "Access-Control-Allow-Origin": "*",
        },
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


def _record_subscription_checkout(
    sub_id: str,
    user_id: str,
    plan: str,
    customer_id: Optional[str],
    paid: bool,
) -> str:
    """Record a Checkout-created subscription; return the row's status.

    Checkout only creates the row. After that, status and plan belong to the
    customer.subscription.* events, which carry Stripe's own values, so a
    redelivered or late checkout event cannot overwrite them. Two cases are
    handled here: our pending placeholder turns active once the session is
    paid, and a row that is already terminal (canceled before this event
    landed) stays terminal.
    """
    with session_scope() as session:
        row = session.execute(
            select(SubscriptionRow).where(SubscriptionRow.id == sub_id)
        ).scalar_one_or_none()
        now = datetime.utcnow()
        if row is None:
            status = "active" if paid else _PENDING_SUB_STATUS
            session.add(
                SubscriptionRow(
                    id=sub_id,
                    user_id=user_id,
                    plan=plan,
                    status=status,
                    stripe_customer_id=customer_id,
                    current_period_end=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()
            return status
        if not row.user_id:
            row.user_id = user_id
        if customer_id and not row.stripe_customer_id:
            row.stripe_customer_id = customer_id
        if paid and row.status == _PENDING_SUB_STATUS:
            row.status = "active"
        row.updated_at = now
        session.flush()
        return str(row.status)


def _checkout_paid(obj: Dict[str, Any]) -> bool:
    return str(obj.get("payment_status") or "") in _PAID_CHECKOUT_STATUSES


def _handle_credit_checkout(obj: Dict[str, Any]) -> Dict[str, Any]:
    """One-time credit-pack purchase via Checkout: checkout.session.completed,
    or checkout.session.async_payment_succeeded when a delayed payment clears.
    Both carry the same session id, so they share one grant key and the pair
    can never grant twice."""
    user_id = obj.get("client_reference_id")
    metadata = obj.get("metadata") or {}
    tier = metadata.get("tier")
    if not user_id or tier not in _TIER_AMOUNTS:
        logger.warning("stripe webhook: missing/invalid user_id=%r tier=%r", user_id, tier)
        return {"received": True, "credited": False}
    if not _checkout_paid(obj):
        return {"received": True, "credited": False, "reason": "payment_pending"}
    session_id = obj.get("id") or ""
    desc = f"stripe_{tier}_{session_id}" if session_id else f"stripe_{tier}"
    granted = _grant_credits_idempotent(str(user_id), _TIER_AMOUNTS[tier], "purchase", desc)
    if not granted:
        return {"received": True, "credited": False, "reason": "duplicate_or_unknown"}
    return {"received": True, "credited": True, "amount": _TIER_AMOUNTS[tier], "tier": tier}


def _handle_subscription_checkout(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Subscription Checkout completed (or its delayed first payment cleared):
    record the subscription, and grant the first billing period's allowance
    only once the payment is in and the subscription has not already ended."""
    user_id = obj.get("client_reference_id")
    metadata = obj.get("metadata") or {}
    plan = metadata.get("plan")
    sub_id = obj.get("subscription")
    customer_id = obj.get("customer")
    if not user_id or plan not in _SUB_PLANS or not sub_id:
        logger.warning("stripe sub checkout: missing user_id=%r plan=%r sub=%r", user_id, plan, sub_id)
        return {"received": True, "credited": False}
    paid = _checkout_paid(obj)
    status = _record_subscription_checkout(
        str(sub_id), str(user_id), str(plan), str(customer_id) if customer_id else None, paid
    )
    if status in _TERMINAL_SUB_STATUSES:
        return {"received": True, "subscription": sub_id, "credited": False, "reason": "subscription_ended"}
    if not paid:
        return {"received": True, "subscription": sub_id, "credited": False, "reason": "payment_pending", "plan": plan}
    granted = _grant_credits_idempotent(str(user_id), _plan_grant(plan), "subscription", f"sub_init_{sub_id}")
    return {"received": True, "subscription": sub_id, "credited": granted, "plan": plan}


def _handle_payment_failed(event_type: str, obj: Dict[str, Any]) -> Dict[str, Any]:
    """checkout.session.async_payment_failed / invoice.payment_failed: the
    money never arrived, so nothing is granted. The subscription's status
    follows from the customer.subscription.* events Stripe sends next."""
    logger.info("stripe %s for %s: nothing granted", event_type, obj.get("id"))
    return {"received": True, "credited": False, "reason": "payment_failed"}


# Invoices that pay for a subscription period. Proration (subscription_update),
# manual and threshold invoices are not a new period and grant nothing.
_PERIOD_BILLING_REASONS = frozenset({"subscription_create", "subscription_cycle"})


def _handle_invoice_paid(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Subscription invoice paid: grant the allowance of the plan this invoice
    actually bills (its line price), never the plan the row last stored.

    subscription_cycle grants a renewal keyed on the invoice id.
    subscription_create shares the checkout's sub_init key: it only lands
    credits when a delayed first payment left the checkout event ungranted,
    and it can never grant the first period twice.
    """
    sub_id = _invoice_subscription_id(obj)
    invoice_id = obj.get("id") or ""
    reason = obj.get("billing_reason")
    if not sub_id:
        return {"received": True, "ignored": "no_subscription"}
    if reason not in _PERIOD_BILLING_REASONS:
        return {"received": True, "credited": False, "reason": "not_a_period_invoice"}
    if obj.get("status") != "paid" or not invoice_id:
        return {"received": True, "credited": False, "reason": "invoice_not_paid"}
    plan = _invoice_billed_plan(obj)
    if plan is None:
        logger.warning("invoice %s for %s bills no known plan price", invoice_id, sub_id)
        return {"received": True, "credited": False, "reason": "unknown_price"}
    with session_scope() as session:
        row = session.execute(
            select(SubscriptionRow).where(SubscriptionRow.id == str(sub_id))
        ).scalar_one_or_none()
        if row is None or not row.user_id:
            logger.warning("invoice paid for unknown subscription %s", sub_id)
            return {"received": True, "credited": False, "reason": "unknown_subscription"}
        user_id = row.user_id
        if reason == "subscription_create":
            if row.status in _TERMINAL_SUB_STATUSES:
                return {"received": True, "credited": False, "reason": "subscription_ended"}
            if row.status == _PENDING_SUB_STATUS:
                row.status = "active"
                row.updated_at = datetime.utcnow()
                session.flush()
    key = f"sub_init_{sub_id}" if reason == "subscription_create" else f"sub_invoice_{invoice_id}"
    amount = _plan_grant(plan)
    granted = _grant_credits_idempotent(user_id, amount, "subscription", key)
    return {"received": True, "credited": granted, "plan": plan, "amount": amount}


def _resize_owner_team(session: Any, owner_id: str, plan: str) -> None:
    """Keep the owner's team seat limit in step with a plan change. Existing
    members stay; a team over its new limit just can't invite until it fits."""
    if not owner_id:
        return
    team = session.execute(
        select(TeamRow).where(TeamRow.owner_id == owner_id)
    ).scalars().first()
    if team is not None:
        team.seat_limit = plan_seats(plan)
        team.updated_at = datetime.utcnow()


def _handle_subscription_change(event_type: str, obj: Dict[str, Any]) -> Dict[str, Any]:
    """customer.subscription.updated/deleted: track Stripe's status, the plan
    (from the subscription's current price) and the period end.

    A terminal status is final: an event that lands after the row went
    terminal (redelivered, or delivered out of order) changes nothing. A
    deletion for a subscription we have not recorded yet leaves a canceled
    tombstone, so a late checkout.session.completed cannot create it active.
    """
    sub_id = _stripe_id(obj.get("id"))
    if not sub_id:
        return {"received": True, "ignored": "no_id"}
    if event_type == "customer.subscription.deleted":
        status: Optional[str] = "canceled"
    else:
        status = str(obj.get("status") or "") or None
    price_ids = _subscription_price_ids(obj)
    plan = _single_plan(price_ids)
    period_dt = _subscription_period_end(obj)
    with session_scope() as session:
        row = session.execute(
            select(SubscriptionRow).where(SubscriptionRow.id == sub_id)
        ).scalar_one_or_none()
        now = datetime.utcnow()
        if row is None:
            if status not in _TERMINAL_SUB_STATUSES:
                return {"received": True, "updated": False, "reason": "unknown_subscription"}
            metadata = obj.get("metadata") or {}
            meta_plan = metadata.get("plan")
            session.add(
                SubscriptionRow(
                    id=sub_id,
                    user_id=str(metadata.get("user_id") or ""),
                    plan=plan or (str(meta_plan) if meta_plan in _SUB_PLANS else "unknown"),
                    status=str(status),
                    stripe_customer_id=_stripe_id(obj.get("customer")),
                    current_period_end=period_dt,
                    overage_enabled=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()
            return {"received": True, "updated": True, "status": status}
        if row.status in _TERMINAL_SUB_STATUSES:
            return {"received": True, "updated": False, "reason": "subscription_ended", "status": row.status}
        if status:
            row.status = status
        if plan and plan != row.plan:
            row.plan = plan
            _resize_owner_team(session, row.user_id, plan)
        elif price_ids and plan is None:
            logger.warning("subscription %s has no known plan price %s; plan stays %s", sub_id, price_ids, row.plan)
        if period_dt is not None:
            row.current_period_end = period_dt
        row.updated_at = now
        session.flush()
        return {"received": True, "updated": True, "status": row.status, "plan": row.plan}


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

    # A delayed payment method (ACH, SEPA, ...) completes Checkout "unpaid"
    # and reports the outcome later; both success events share a grant key.
    if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        if (obj.get("mode") == "subscription") or obj.get("subscription"):
            return _handle_subscription_checkout(obj)
        return _handle_credit_checkout(obj)
    if event_type in ("checkout.session.async_payment_failed", "invoice.payment_failed"):
        return _handle_payment_failed(event_type, obj)
    if event_type == "invoice.payment_succeeded":
        return _handle_invoice_paid(obj)
    if event_type in ("customer.subscription.deleted", "customer.subscription.updated"):
        return _handle_subscription_change(event_type, obj)
    return {"received": True, "ignored": event_type or "unknown"}


__all__ = ["router"]
