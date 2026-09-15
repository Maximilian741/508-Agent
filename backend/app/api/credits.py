"""Credits router.

Balance, ledger history, mock purchases, and spend.

# TODO: integrate Stripe Checkout for real purchases.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select, update

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


def _debit_wallet(session, user_id: str, amount: int) -> int:
    """Debit ``amount`` in ONE conditional UPDATE; return the new balance.

    The guard and the write are a single statement, so the database evaluates
    ``balance >= amount`` against the row it is about to write — on sqlite and
    Postgres alike. rowcount 0 means the user is unknown or can't afford it.
    """
    result = session.execute(
        update(UserRow)
        .where(UserRow.id == user_id, UserRow.credits_balance >= amount)
        .values(credits_balance=UserRow.credits_balance - amount)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        exists = session.execute(select(UserRow.id).where(UserRow.id == user_id)).scalar_one_or_none()
        raise InsufficientCreditsError("unknown_user" if exists is None else "insufficient_credits")
    return int(
        session.execute(select(UserRow.credits_balance).where(UserRow.id == user_id)).scalar_one() or 0
    )


def spend_credits_for_user(
    user_id: str,
    amount: int,
    description: str,
    related_doc_id: Optional[str] = None,
) -> int:
    """Atomic spend.  Returns the new balance.

    Raises InsufficientCreditsError if the user doesn't have enough.

    The debit is a conditional UPDATE plus the ledger row in one transaction.
    It used to be read-check-write, row-locked only when the dialect was not
    sqlite — and sqlite is the default DATABASE_URL, run under 2 uvicorn
    workers. Eight concurrent spends of 5 against a balance of 5 all read 5,
    all passed the check, and all delivered: balance 0, ledger -40.
    """
    if amount <= 0:
        raise ValueError("amount must be positive")

    with session_scope() as session:
        new_balance = _debit_wallet(session, user_id, amount)
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
        return new_balance


def spend_credits_once_for_user(
    user_id: str,
    amount: int,
    description: str,
    idempotency_key: str,
) -> Tuple[int, bool]:
    """Spend at most once per ``idempotency_key``. Returns (balance, charged_now).

    The key is recorded as the ledger row's ``related_doc_id``, in the SAME
    transaction as the debit, so "charged" and "recorded" commit together or
    not at all: a crash can't leave one without the other, and a failed debit
    (InsufficientCreditsError) records nothing, so a later retry can still pay.

    Racing callers serialize on the wallet's write lock, taken first by a
    no-op UPDATE (a row lock on Postgres, the database write lock on sqlite).
    The loser then sees the winner's committed ledger row and returns
    ``charged_now=False`` without debiting. Works across worker processes.
    """
    if amount <= 0:
        raise ValueError("amount must be positive")
    key = str(idempotency_key or "").strip()
    if not key:
        raise ValueError("idempotency_key is required")

    with session_scope() as session:
        locked = session.execute(
            update(UserRow)
            .where(UserRow.id == user_id)
            .values(credits_balance=UserRow.credits_balance)
            .execution_options(synchronize_session=False)
        )
        if locked.rowcount != 1:
            raise InsufficientCreditsError("unknown_user")
        already = session.execute(
            select(CreditLedgerRow.id)
            .where(
                CreditLedgerRow.user_id == user_id,
                CreditLedgerRow.kind == "spend",
                CreditLedgerRow.related_doc_id == key,
            )
            .limit(1)
        ).scalar_one_or_none()
        if already is not None:
            balance = session.execute(
                select(UserRow.credits_balance).where(UserRow.id == user_id)
            ).scalar_one()
            return int(balance or 0), False
        new_balance = _debit_wallet(session, user_id, amount)
        session.add(
            CreditLedgerRow(
                user_id=user_id,
                at=datetime.utcnow(),
                kind="spend",
                amount=-amount,
                description=description,
                related_doc_id=key,
            )
        )
        session.flush()
        return new_balance, True


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
