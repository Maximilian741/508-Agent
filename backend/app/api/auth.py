"""Auth router for the hackathon scaffold.

This is intentionally simple: clients sign in with an email + display name
and we mint or fetch a UserRow keyed by that email.  Identity for subsequent
requests is carried via an X-Account-Id header (the user's uuid).

# TODO: replace with Cloudflare Access JWT verification or OAuth before prod
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.db.models import CreditLedgerRow, UserRow
from app.db.session_sqlalchemy import session_scope
from app.persistence import audit_log as _audit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class UserDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    email: str
    displayName: str
    role: str
    creditsBalance: int
    createdAt: str
    lastSeenAt: Optional[str] = None


class SignInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    displayName: Optional[str] = None


class SignInResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: UserDTO
    token: str


class GrantStarterResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: UserDTO
    granted: bool
    amount: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_dto(row: UserRow) -> UserDTO:
    return UserDTO(
        id=row.id,
        email=row.email,
        displayName=row.display_name,
        role=row.role,
        creditsBalance=int(row.credits_balance or 0),
        createdAt=(row.created_at.isoformat() if row.created_at else ""),
        lastSeenAt=(row.last_seen_at.isoformat() if row.last_seen_at else None),
    )


def get_current_user_row(
    session,
    *,
    account_id: Optional[str],
) -> UserRow:
    """Resolve the calling user from the X-Account-Id header.

    Raises HTTP 401 if the header is missing or the id does not match a user.
    """
    if not account_id:
        raise HTTPException(status_code=401, detail="missing_account")
    row = session.execute(
        select(UserRow).where(UserRow.id == account_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=401, detail="unknown_account")
    return row


def require_account(
    request: Request,
    x_account_id: Optional[str],
) -> str:
    """Light wrapper used by other routers to extract the account id."""
    if not x_account_id:
        raise HTTPException(status_code=401, detail="missing_account")
    return x_account_id


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/sign-in", response_model=SignInResponse)
async def sign_in(payload: SignInRequest, request: Request) -> SignInResponse:
    """Mint or fetch a UserRow keyed by email."""
    email_norm = payload.email.strip().lower()
    display = (payload.displayName or email_norm.split("@")[0]).strip()
    if not display:
        display = email_norm
    now = datetime.utcnow()

    with session_scope() as session:
        row = session.execute(
            select(UserRow).where(UserRow.email == email_norm)
        ).scalar_one_or_none()
        if row is None:
            row = UserRow(
                id=uuid.uuid4().hex,
                email=email_norm,
                display_name=display[:120],
                created_at=now,
                last_seen_at=now,
                role="user",
                credits_balance=0,
            )
            session.add(row)
            session.flush()
        else:
            row.last_seen_at = now
            if payload.displayName and payload.displayName.strip():
                row.display_name = payload.displayName.strip()[:120]
        dto = _row_to_dto(row)

    # The token is just the account id for this scaffold.  When we wire
    # Cloudflare Access, the JWT replaces it.
    return SignInResponse(user=dto, token=dto.id)


@router.get("/me", response_model=UserDTO)
async def me(
    request: Request,
    x_account_id: Optional[str] = Header(default=None, alias="X-Account-Id"),
    authorization: Optional[str] = Header(default=None),
) -> UserDTO:
    """Return the calling user.  Accepts either X-Account-Id or
    Authorization: Bearer <accountId> for convenience."""
    account_id = x_account_id
    if not account_id and authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            account_id = parts[1].strip()

    with session_scope() as session:
        row = get_current_user_row(session, account_id=account_id)
        # Update last_seen on every /me hit so admin views can show activity.
        row.last_seen_at = datetime.utcnow()
        return _row_to_dto(row)


@router.post("/sign-out")
async def sign_out(
    x_account_id: Optional[str] = Header(default=None, alias="X-Account-Id"),
) -> Response:
    """No server-side session to clear in this scaffold; just 204."""
    return Response(status_code=204)


@router.post("/grant-starter", response_model=GrantStarterResponse)
async def grant_starter(
    request: Request,
    x_account_id: Optional[str] = Header(default=None, alias="X-Account-Id"),
) -> GrantStarterResponse:
    """Grant 25 credits exactly once per user (idempotent)."""
    GRANT_AMOUNT = 25
    GRANT_KIND = "grant"
    GRANT_DESC = "starter_grant"

    with session_scope() as session:
        row = get_current_user_row(session, account_id=x_account_id)

        existing = session.execute(
            select(CreditLedgerRow).where(
                CreditLedgerRow.user_id == row.id,
                CreditLedgerRow.kind == GRANT_KIND,
                CreditLedgerRow.description == GRANT_DESC,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return GrantStarterResponse(
                user=_row_to_dto(row),
                granted=False,
                amount=0,
            )

        session.add(
            CreditLedgerRow(
                user_id=row.id,
                at=datetime.utcnow(),
                kind=GRANT_KIND,
                amount=GRANT_AMOUNT,
                description=GRANT_DESC,
                related_doc_id=None,
            )
        )
        row.credits_balance = int(row.credits_balance or 0) + GRANT_AMOUNT
        session.flush()

        try:
            ctx = _audit.context_from_request(request)
        except Exception:
            ctx = {}
        try:
            _audit.record_event(
                event="grant_credits",
                actor_email=row.email,
                details={"amount": GRANT_AMOUNT, "reason": GRANT_DESC},
                **{k: v for k, v in ctx.items() if k in {"request_id", "ip"}},
            )
        except Exception:
            pass

        return GrantStarterResponse(
            user=_row_to_dto(row),
            granted=True,
            amount=GRANT_AMOUNT,
        )
