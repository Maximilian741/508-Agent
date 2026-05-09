"""Auth router for the hackathon scaffold.

This is intentionally simple: clients sign in with an email + display name
and we mint or fetch a UserRow keyed by that email.  Identity for subsequent
requests is carried via either:

- an ``Authorization: Bearer <jwt>`` header (preferred), or
- the legacy ``X-Account-Id`` header (the user's uuid)

# TODO: replace with Cloudflare Access JWT verification or OAuth before prod
# (real JWT now in place; swap signing key + audience for prod)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from app.db.models import CreditLedgerRow, EmailVerifyTokenRow, UserRow
from app.db.session_sqlalchemy import session_scope
from app.persistence import audit_log as _audit
from app.security.sessions import mint_session, verify_session

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
    hasPassword: bool = False
    emailVerifiedAt: Optional[str] = None


class SignInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    displayName: Optional[str] = None
    # Optional - if the user has a password_hash on file, this MUST match.
    # Existing accounts with no password_hash continue to sign in by email
    # alone (back-compat).
    password: Optional[str] = Field(default=None, max_length=256)


class SignInResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: UserDTO
    token: str




class UpdateProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    displayName: Optional[str] = Field(default=None, max_length=120)
    email: Optional[str] = Field(default=None, min_length=3, max_length=320)

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
        hasPassword=bool(row.password_hash),
        emailVerifiedAt=(
            row.email_verified_at.isoformat() if row.email_verified_at else None
        ),
    )


# ---------------------------------------------------------------------------
# Password hashing helpers (stdlib scrypt, no new deps)
# ---------------------------------------------------------------------------

# scrypt cost params -- fine for dev / hackathon. Bump for prod.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


def _hash_password(password: str) -> str:
    """Return ``salt:hash`` hex string. Both halves are hex-encoded."""
    if not isinstance(password, str) or not password:
        raise ValueError("empty password")
    salt = os.urandom(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"{salt.hex()}:{derived.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    """Constant-time compare against a ``salt:hash`` string."""
    if not stored or ":" not in stored:
        return False
    try:
        salt_hex, hash_hex = stored.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    derived = hashlib.scrypt(
        (password or "").encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=len(expected) or _SCRYPT_DKLEN,
    )
    return hmac.compare_digest(derived, expected)



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

def resolve_account_id(
    *,
    authorization: Optional[str],
    x_account_id: Optional[str],
) -> Optional[str]:
    """Pick an account id out of a request, preferring Bearer JWT.

    1. If ``Authorization: Bearer <jwt>`` is present and verifies, use ``sub``.
    2. Otherwise fall back to the legacy ``X-Account-Id`` header.

    Returns ``None`` if neither yields a value; the caller is expected to
    raise HTTP 401 in that case (see ``get_current_user_row``).
    """

    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            raw_token = parts[1].strip()
            if raw_token:
                claims = verify_session(raw_token)
                if claims is not None:
                    sub = claims.get("sub")
                    if isinstance(sub, str) and sub:
                        return sub
                # If the bearer token failed to verify, fall through to
                # the X-Account-Id header rather than 401-ing here -
                # /auth/me may want to return a clean 401 only when both
                # paths are exhausted.
    if x_account_id:
        return x_account_id.strip() or None
    return None



# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/sign-in", response_model=SignInResponse)
async def sign_in(payload: SignInRequest, request: Request) -> SignInResponse:
    """Mint or fetch a UserRow keyed by email.

    Password rules (back-compat):
      - If the user has a ``password_hash`` set, ``password`` is required and
        must verify; otherwise we 401.
      - If the user has no ``password_hash``, sign-in still succeeds without
        a password (existing pre-password users keep working).
    """
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
            # Existing user: enforce password if one is on file.
            if row.password_hash:
                if not payload.password or not _verify_password(
                    payload.password, row.password_hash
                ):
                    raise HTTPException(status_code=401, detail="invalid_credentials")
            row.last_seen_at = now
            if payload.displayName and payload.displayName.strip():
                row.display_name = payload.displayName.strip()[:120]
        dto = _row_to_dto(row)

    # Mint an HS256-signed session JWT.  The token survives backend
    # restarts (assuming APP_SECRET is set) and resists tampering.
    token = mint_session(dto.id)
    return SignInResponse(user=dto, token=token)


@router.get("/me", response_model=UserDTO)
async def me(
    request: Request,
    x_account_id: Optional[str] = Header(default=None, alias="X-Account-Id"),
    authorization: Optional[str] = Header(default=None),
) -> UserDTO:
    """Return the calling user.

    Authentication precedence:

    1. ``Authorization: Bearer <jwt>`` - verified against ``settings.app_secret``.
    2. ``X-Account-Id`` - legacy direct-uuid header (kept for backward compat).
    """

    account_id = resolve_account_id(
        authorization=authorization,
        x_account_id=x_account_id,
    )

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


# ---------------------------------------------------------------------------
# GDPR self-service: edit profile, export data, delete account
# ---------------------------------------------------------------------------


@router.patch("/me", response_model=UserDTO)
async def update_me(
    payload: UpdateProfileRequest,
    request: Request,
    x_account_id: Optional[str] = Header(default=None, alias="X-Account-Id"),
    authorization: Optional[str] = Header(default=None),
) -> UserDTO:
    """Update the current user's display name and/or email.

    Requires Bearer token or X-Account-Id. Email collisions return 409.
    """
    account_id = resolve_account_id(
        authorization=authorization,
        x_account_id=x_account_id,
    )
    with session_scope() as session:
        row = get_current_user_row(session, account_id=account_id)

        if payload.displayName is not None:
            new_name = payload.displayName.strip()
            if new_name:
                row.display_name = new_name[:120]

        if payload.email is not None:
            new_email = payload.email.strip().lower()
            if new_email and new_email != row.email:
                clash = session.execute(
                    select(UserRow).where(
                        UserRow.email == new_email,
                        UserRow.id != row.id,
                    )
                ).scalar_one_or_none()
                if clash is not None:
                    raise HTTPException(status_code=409, detail="email_in_use")
                row.email = new_email

        row.last_seen_at = datetime.utcnow()
        return _row_to_dto(row)


@router.get("/export")
async def export_me(
    request: Request,
    x_account_id: Optional[str] = Header(default=None, alias="X-Account-Id"),
    authorization: Optional[str] = Header(default=None),
) -> StreamingResponse:
    """Stream a JSON dump of {user, credit_history, audit_log_entries_for_this_user}.

    Returned as application/json with Content-Disposition attachment so the
    browser triggers a file save dialog.
    """
    import io
    import json as _json

    account_id = resolve_account_id(
        authorization=authorization,
        x_account_id=x_account_id,
    )
    with session_scope() as session:
        row = get_current_user_row(session, account_id=account_id)
        user_dto = _row_to_dto(row).model_dump()
        ledger_rows = session.execute(
            select(CreditLedgerRow)
            .where(CreditLedgerRow.user_id == row.id)
            .order_by(CreditLedgerRow.at.desc())
        ).scalars().all()
        credit_history = [
            {
                "id": entry.id,
                "at": entry.at.isoformat() if entry.at else None,
                "kind": entry.kind,
                "amount": int(entry.amount or 0),
                "description": entry.description,
                "relatedDocId": entry.related_doc_id,
            }
            for entry in ledger_rows
        ]
        actor_email = row.email

    audit_entries: list = []
    try:
        events = _audit.list_events(
            filters={"actor_email": actor_email},
            limit=2000,
        )
        audit_entries = [e.to_dict() for e in events]
    except Exception as exc:  # fail soft
        logger.warning("auth.export: audit dump failed: %s", exc)
        audit_entries = []

    payload = {
        "user": user_dto,
        "credit_history": credit_history,
        "audit_log_entries_for_this_user": audit_entries,
        "exported_at": datetime.utcnow().isoformat() + "Z",
    }
    body = _json.dumps(payload, indent=2, default=str).encode("utf-8")

    fname = f"508-export-{user_dto.get('id', 'user')}.json"
    return StreamingResponse(
        io.BytesIO(body),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Content-Length": str(len(body)),
        },
    )


@router.delete("/me", status_code=204)
async def delete_me(
    request: Request,
    x_account_id: Optional[str] = Header(default=None, alias="X-Account-Id"),
    authorization: Optional[str] = Header(default=None),
) -> Response:
    """Delete the current user's account and cascade-remove their ledger rows."""
    account_id = resolve_account_id(
        authorization=authorization,
        x_account_id=x_account_id,
    )
    with session_scope() as session:
        row = get_current_user_row(session, account_id=account_id)
        user_id = row.id
        # Cascade ledger first
        session.execute(
            delete(CreditLedgerRow).where(CreditLedgerRow.user_id == user_id)
        )
        session.delete(row)
    return Response(status_code=204)




# ---------------------------------------------------------------------------
# Password + email verification scaffold
# ---------------------------------------------------------------------------


class SetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=4, max_length=256)


class SetPasswordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: UserDTO
    updated: bool


class VerifyQueuedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queued: bool


class VerifyResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verified: bool


@router.post("/set-password", response_model=SetPasswordResponse)
async def set_password(
    payload: SetPasswordRequest,
    request: Request,
    x_account_id: Optional[str] = Header(default=None, alias="X-Account-Id"),
    authorization: Optional[str] = Header(default=None),
) -> SetPasswordResponse:
    """Set or change the caller's password (scrypt salt:hash, stdlib only)."""
    account_id = resolve_account_id(
        authorization=authorization,
        x_account_id=x_account_id,
    )
    with session_scope() as session:
        row = get_current_user_row(session, account_id=account_id)
        row.password_hash = _hash_password(payload.password)
        row.last_seen_at = datetime.utcnow()
        return SetPasswordResponse(user=_row_to_dto(row), updated=True)


@router.post("/request-verify-email", response_model=VerifyQueuedResponse)
async def request_verify_email(
    request: Request,
    x_account_id: Optional[str] = Header(default=None, alias="X-Account-Id"),
    authorization: Optional[str] = Header(default=None),
) -> VerifyQueuedResponse:
    """Generate a verification token and log a debug "email" line.

    No real SMTP yet; the magic link is printed to the server logs so devs
    can copy/paste it. Old tokens for this user are cleared first.
    """
    account_id = resolve_account_id(
        authorization=authorization,
        x_account_id=x_account_id,
    )
    with session_scope() as session:
        row = get_current_user_row(session, account_id=account_id)
        # Drop any stale tokens for this user (one outstanding link is plenty).
        session.execute(
            delete(EmailVerifyTokenRow).where(
                EmailVerifyTokenRow.user_id == row.id
            )
        )
        token = secrets.token_hex(16)  # 32 hex chars
        session.add(
            EmailVerifyTokenRow(
                token=token,
                user_id=row.id,
                created_at=datetime.utcnow(),
            )
        )
        # SMTP placeholder. Replace with a real mailer when ready.
        logger.info(
            "[email] verify link: /auth/verify-email?token=%s (to=%s)",
            token,
            row.email,
        )
    return VerifyQueuedResponse(queued=True)


@router.get("/verify-email", response_model=VerifyResultResponse)
async def verify_email(token: str) -> VerifyResultResponse:
    """Consume a verification token, mark the user verified, return ok."""
    if not token or len(token) > 64:
        raise HTTPException(status_code=410, detail="token_expired")
    with session_scope() as session:
        row = session.execute(
            select(EmailVerifyTokenRow).where(EmailVerifyTokenRow.token == token)
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=410, detail="token_expired")
        user = session.execute(
            select(UserRow).where(UserRow.id == row.user_id)
        ).scalar_one_or_none()
        if user is None:
            session.execute(
                delete(EmailVerifyTokenRow).where(
                    EmailVerifyTokenRow.token == token
                )
            )
            raise HTTPException(status_code=410, detail="token_expired")
        user.email_verified_at = datetime.utcnow()
        session.execute(
            delete(EmailVerifyTokenRow).where(
                EmailVerifyTokenRow.token == token
            )
        )
    return VerifyResultResponse(verified=True)
