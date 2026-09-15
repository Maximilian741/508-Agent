"""Credits router.

Balance, ledger history, mock purchases, and spend.

# TODO: integrate Stripe Checkout for real purchases.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select

from app.api.auth import get_current_user_row
from app.api.deps import require_user_id
from app.config import get_settings
from app.db.models import CreditLedgerRow, UserRow
from app.db.session_sqlalchemy import session_scope
from app.persistence import audit_log as _audit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/credits")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LedgerEntryDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    at: str
    kind: str
    amount: int
    description: str
    relatedDocId: Optional[str] = None


class BalanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    balance: int
    history: List[LedgerEntryDTO] = Field(default_factory=list)


class PurchaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tier: Literal["starter", "pro", "studio"]


class PurchaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    newBalance: int
    purchased: int
    tier: str


class SpendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: int = Field(gt=0)
    description: str = Field(min_length=1, max_length=500)
    relatedDocId: Optional[str] = Field(default=None, max_length=128)


class SpendResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    newBalance: int
    spent: int


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_TIER_AMOUNTS = {
    "starter": 50,
    "pro": 250,
    "studio": 1300,
}


# Per-format spend amounts for /pipeline/remediate.
DOC_FORMAT_COSTS = {
    "pdf": 5,
    "docx": 3,
    "pptx": 4,
    # HTML is text-native and v1 does only lightweight attribute/text DOM
    # edits (no content-stream surgery or OOXML rewriting), so it sits in the
    # cheapest tier alongside docx. MUST match frontend creditCosts.ts.
    "html": 3,
    "htm": 3,
}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _ledger_to_dto(row: CreditLedgerRow) -> LedgerEntryDTO:
    return LedgerEntryDTO(
        id=int(row.id),
        at=(row.at.isoformat() if row.at else ""),
        kind=row.kind,
        amount=int(row.amount),
        description=row.description,
        relatedDocId=row.related_doc_id,
    )


class InsufficientCreditsError(Exception):
    pass


def spend_credits_for_user(
    user_id: str,
    amount: int,
    description: str,
    related_doc_id: Optional[str] = None,
) -> int:
    """Atomic spend.  Returns the new balance.

    Raises InsufficientCreditsError if the user doesn't have enough.
    """
    if amount <= 0:
        raise ValueError("amount must be positive")

    with session_scope() as session:
        row = session.execute(
            select(UserRow).where(UserRow.id == user_id).with_for_update()
            if session.bind.dialect.name != "sqlite"
            else select(UserRow).where(UserRow.id == user_id)
        ).scalar_one_or_none()
        if row is None:
            raise InsufficientCreditsError("unknown_user")

        current = int(row.credits_balance or 0)
        if current < amount:
            raise InsufficientCreditsError("insufficient_credits")

        row.credits_balance = current - amount
        session.add(
            CreditLedgerRow(
                user_id=user_id,
                at=datetime.utcnow(),
                kind="spend",
                amount=-amount,
                description=description,
                related_doc_id=related_doc_id,
            )
        )
        session.flush()
        return int(row.credits_balance or 0)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/balance", response_model=BalanceResponse)
async def balance(
    request: Request,
    user_id: str = Depends(require_user_id),
) -> BalanceResponse:
    # A team member sees the shared (owner's) wallet; everyone else sees their own.
    try:
        from app.api.teams import resolve_credit_user_id

        target_id = resolve_credit_user_id(user_id)
    except Exception:
        target_id = user_id

    with session_scope() as session:
        # Ensure the caller's own row exists / is current.
        get_current_user_row(session, account_id=user_id)
        wallet = session.execute(
            select(UserRow).where(UserRow.id == target_id)
        ).scalar_one_or_none()
        balance = int(wallet.credits_balance or 0) if wallet else 0
        ledger_rows = session.execute(
            select(CreditLedgerRow)
            .where(CreditLedgerRow.user_id == target_id)
            .order_by(desc(CreditLedgerRow.at), desc(CreditLedgerRow.id))
            .limit(50)
        ).scalars().all()
        history = [_ledger_to_dto(r) for r in ledger_rows]
        return BalanceResponse(balance=balance, history=history)


@router.post("/purchase", response_model=PurchaseResponse)
async def purchase(
    payload: PurchaseRequest,
    request: Request,
    user_id: str = Depends(require_user_id),
) -> PurchaseResponse:
    amount = _TIER_AMOUNTS.get(payload.tier)
    if amount is None:
        raise HTTPException(status_code=400, detail="invalid_tier")

    # If Stripe is configured, force callers through Stripe Checkout instead
    # of the dev mock path.
    if os.environ.get("STRIPE_SECRET_KEY", "").strip():
        raise HTTPException(status_code=409, detail="use_stripe_checkout")

    # Never hand out free credits via the mock path in production. The mock
    # path exists only for local/dev where Stripe is not wired up.
    if get_settings().environment == "production":
        raise HTTPException(status_code=503, detail="billing_not_configured")

    with session_scope() as session:
        row = get_current_user_row(session, account_id=user_id)
        row.credits_balance = int(row.credits_balance or 0) + amount
        session.add(
            CreditLedgerRow(
                user_id=row.id,
                at=datetime.utcnow(),
                kind="purchase",
                amount=amount,
                description=f"purchase_{payload.tier}",
                related_doc_id=None,
            )
        )
        session.flush()
        new_balance = int(row.credits_balance or 0)
        actor_email = row.email

    try:
        ctx = _audit.context_from_request(request)
    except Exception:
        ctx = {}
    try:
        _audit.record_event(
            event="purchase_credits",
            actor_email=actor_email,
            details={"tier": payload.tier, "amount": amount},
            **{k: v for k, v in ctx.items() if k in {"request_id", "ip"}},
        )
    except Exception:
        pass

    return PurchaseResponse(newBalance=new_balance, purchased=amount, tier=payload.tier)


@router.post("/spend", response_model=SpendResponse)
async def spend(
    payload: SpendRequest,
    request: Request,
    user_id: str = Depends(require_user_id),
) -> SpendResponse:
    # Look up email for the audit log before spending (so we don't lose it
    # in the locked transaction).
    actor_email: Optional[str] = None
    with session_scope() as session:
        row = get_current_user_row(session, account_id=user_id)
        actor_email = row.email

    # Team members draw on (and overage-charge) the team owner's shared wallet.
    try:
        from app.api.teams import resolve_credit_user_id

        target_id = resolve_credit_user_id(user_id)
    except Exception:
        target_id = user_id

    # Subscribers with overage enabled get an automatic top-up instead of a 402.
    try:
        from app.api.stripe_billing import ensure_balance_for

        ensure_balance_for(target_id, payload.amount, actor_id=user_id)
    except Exception:
        pass
    try:
        new_balance = spend_credits_for_user(
            user_id=target_id,
            amount=payload.amount,
            description=payload.description,
            related_doc_id=payload.relatedDocId,
        )
    except InsufficientCreditsError:
        raise HTTPException(status_code=402, detail="insufficient_credits")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        ctx = _audit.context_from_request(request)
    except Exception:
        ctx = {}
    try:
        _audit.record_event(
            event="spend_credits",
            actor_email=actor_email,
            doc_id=payload.relatedDocId,
            details={"amount": payload.amount, "description": payload.description},
            **{k: v for k, v in ctx.items() if k in {"request_id", "ip"}},
        )
    except Exception:
        pass

    return SpendResponse(newBalance=new_balance, spent=payload.amount)
