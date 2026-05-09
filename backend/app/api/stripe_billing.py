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

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import get_current_user_row, resolve_account_id
from app.db.models import CreditLedgerRow, UserRow
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


class BillingConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    tiers: List[BillingTierDTO]


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
    return BillingConfigResponse(enabled=enabled, tiers=tiers)


@router.post("/create-checkout-session", response_model=CreateCheckoutResponse)
async def create_checkout_session(
    payload: CreateCheckoutRequest,
    request: Request,
    x_account_id: Optional[str] = Header(default=None, alias="X-Account-Id"),
    authorization: Optional[str] = Header(default=None),
) -> CreateCheckoutResponse:
    secret = _stripe_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="billing_not_configured")

    price_id = _price_id_for(payload.tier)
    if not price_id:
        raise HTTPException(status_code=503, detail="billing_not_configured")

    account_id = resolve_account_id(
        authorization=authorization,
        x_account_id=x_account_id,
    )
    with session_scope() as session:
        row = get_current_user_row(session, account_id=account_id)
        user_id = row.id

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


@router.post("/webhook")
async def stripe_webhook(request: Request) -> Dict[str, Any]:
    secret = _webhook_secret()
    raw = await request.body()
    if secret:
        sig_header = request.headers.get("stripe-signature", "")
        if not _verify_signature(raw, sig_header, secret):
            raise HTTPException(status_code=400, detail="invalid_signature")

    try:
        event = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_payload")

    event_type = event.get("type")
    if event_type != "checkout.session.completed":
        return {"received": True, "ignored": event_type or "unknown"}

    obj = (event.get("data") or {}).get("object") or {}
    user_id = obj.get("client_reference_id")
    metadata = obj.get("metadata") or {}
    tier = metadata.get("tier")
    if not user_id or tier not in _TIER_AMOUNTS:
        logger.warning(
            "stripe webhook: missing/invalid user_id=%r tier=%r",
            user_id,
            tier,
        )
        return {"received": True, "credited": False}

    amount = _TIER_AMOUNTS[tier]
    session_id = obj.get("id") or ""

    with session_scope() as session:
        from sqlalchemy import select

        row = session.execute(
            select(UserRow).where(UserRow.id == user_id)
        ).scalar_one_or_none()
        if row is None:
            logger.warning("stripe webhook: unknown user_id=%s", user_id)
            return {"received": True, "credited": False}

        # Idempotency: if we've already recorded a ledger entry referencing
        # this Stripe session id, skip.
        if session_id:
            existing = session.execute(
                select(CreditLedgerRow).where(
                    CreditLedgerRow.user_id == user_id,
                    CreditLedgerRow.description == f"stripe_{tier}_{session_id}",
                )
            ).first()
            if existing is not None:
                return {"received": True, "credited": False, "reason": "duplicate"}

        row.credits_balance = int(row.credits_balance or 0) + amount
        session.add(
            CreditLedgerRow(
                user_id=user_id,
                at=datetime.utcnow(),
                kind="purchase",
                amount=amount,
                description=f"stripe_{tier}_{session_id}" if session_id else f"stripe_{tier}",
                related_doc_id=None,
            )
        )
        session.flush()

    return {"received": True, "credited": True, "amount": amount, "tier": tier}


__all__ = ["router"]
