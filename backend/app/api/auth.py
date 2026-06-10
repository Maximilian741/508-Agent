"""Auth router.

Self-serve email + password accounts. ``/sign-in`` registers a new user (a
password is required) or authenticates an existing one, and returns an
HS256-signed session JWT (see :mod:`app.security.sessions`). Identity for every
subsequent request is carried via ``Authorization: Bearer <jwt>`` and resolved
by the dependencies in :mod:`app.api.deps`. There is no passwordless path and
no client-supplied account header.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from app.api.deps import require_user_id
from app.db.models import CreditLedgerRow, EmailVerifyTokenRow, UserRow
from app.db.session_sqlalchemy import session_scope
from app.persistence import audit_log as _audit
from app.security.sessions import mint_session
from app.services.mailer import send_email

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

# scrypt cost params. N=2**15 (~32 MiB, tens of ms per hash) is a reasonable
# interactive-login cost. The stored ``salt:hash`` format does not encode N, so
# changing it invalidates existing hashes — acceptable on a fresh DB.
_SCRYPT_N = 2 ** 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
# scrypt needs ~128*N*r bytes (~32 MiB here); OpenSSL's default maxmem (32 MiB)
# is too tight, so set an explicit ceiling with headroom or it raises
# "memory limit exceeded".
_SCRYPT_MAXMEM = 128 * _SCRYPT_N * _SCRYPT_R * 2

# Minimum password length for self-serve accounts.
_MIN_PASSWORD_LEN = 8


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
        maxmem=_SCRYPT_MAXMEM,
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
        maxmem=_SCRYPT_MAXMEM,
    )
    return hmac.compare_digest(derived, expected)



def get_current_user_row(
    session,
    *,
    account_id: Optional[str],
) -> UserRow:
    """Load a ``UserRow`` by its (already-verified) id within ``session``.

    ``account_id`` must originate from a verified session token (see
    ``app.api.deps.require_user_id``) — never from a client-supplied header.
    Raises HTTP 401 if the id is missing or does not match a user.
    """
    if not account_id:
        raise HTTPException(status_code=401, detail="authentication_required")
    row = session.execute(
        select(UserRow).where(UserRow.id == account_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=401, detail="unknown_account")
    return row



# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/sign-in", response_model=SignInResponse)
async def sign_in(payload: SignInRequest, request: Request) -> SignInResponse:
    """Register-or-authenticate a user by email + password.

    - New email: a password (>= 8 chars) is REQUIRED; the account is created
      with that password (self-serve sign-up).
    - Existing email with a password on file: the password must verify.
    - Existing email with no password (legacy/dev rows): the supplied password
      is adopted as the account password (one-time migration), then sign-in
      proceeds.

    There is no passwordless path — identity must be provable.
    """
    email_norm = payload.email.strip().lower()
    display = (payload.displayName or email_norm.split("@")[0]).strip()
    if not display:
        display = email_norm
    password = (payload.password or "").strip()
    now = datetime.utcnow()

    with session_scope() as session:
        row = session.execute(
            select(UserRow).where(UserRow.email == email_norm)
        ).scalar_one_or_none()
        if row is None:
            # Sign-up: a password is mandatory.
            if len(password) < _MIN_PASSWORD_LEN:
                raise HTTPException(status_code=400, detail="password_required")
            row = UserRow(
                id=uuid.uuid4().hex,
                email=email_norm,
                display_name=display[:120],
                created_at=now,
                last_seen_at=now,
                role="user",
                credits_balance=0,
                password_hash=_hash_password(password),
            )
            session.add(row)
            session.flush()
        else:
            if row.password_hash:
                if not password or not _verify_password(password, row.password_hash):
                    raise HTTPException(status_code=401, detail="invalid_credentials")
            else:
                # Legacy passwordless row: adopt the supplied password once.
                if len(password) < _MIN_PASSWORD_LEN:
                    raise HTTPException(status_code=400, detail="password_required")
                row.password_hash = _hash_password(password)
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
    user_id: str = Depends(require_user_id),
) -> UserDTO:
    """Return the calling user (requires a valid session token)."""
    with session_scope() as session:
        row = get_current_user_row(session, account_id=user_id)
        # Update last_seen on every /me hit so admin views can show activity.
        row.last_seen_at = datetime.utcnow()
        return _row_to_dto(row)


@router.post("/sign-out")
async def sign_out() -> Response:
    """Stateless sessions — nothing to clear server-side; just 204.

    The client discards its token. No auth required: signing out with an
    already-expired token should still succeed.
    """
    return Response(status_code=204)


@router.post("/grant-starter", response_model=GrantStarterResponse)
async def grant_starter(
    request: Request,
    user_id: str = Depends(require_user_id),
) -> GrantStarterResponse:
    """Grant 25 credits exactly once per user (idempotent).

    Abuse guard: when an email pipeline is configured (SMTP_HOST set), the
    grant requires a VERIFIED email — otherwise throwaway addresses can farm
    25 free credits per signup. In dev (no SMTP) the gate is off so local
    flows and tests keep working unchanged.
    """
    GRANT_AMOUNT = 25
    GRANT_KIND = "grant"
    GRANT_DESC = "starter_grant"

    import os as _os

    smtp_configured = bool((_os.environ.get("SMTP_HOST") or "").strip())

    with session_scope() as session:
        row = get_current_user_row(session, account_id=user_id)

        if smtp_configured and not row.email_verified_at:
            raise HTTPException(status_code=403, detail="verify_email_first")

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
    user_id: str = Depends(require_user_id),
) -> UserDTO:
    """Update the current user's display name and/or email.

    Requires a valid session token. Email collisions return 409.
    """
    with session_scope() as session:
        row = get_current_user_row(session, account_id=user_id)

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
    user_id: str = Depends(require_user_id),
) -> StreamingResponse:
    """Stream a JSON dump of {user, credit_history, audit_log_entries_for_this_user}.

    Returned as application/json with Content-Disposition attachment so the
    browser triggers a file save dialog.
    """
    import io
    import json as _json

    with session_scope() as session:
        row = get_current_user_row(session, account_id=user_id)
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
    user_id: str = Depends(require_user_id),
) -> Response:
    """Delete the current user's account and cascade-remove their ledger rows."""
    with session_scope() as session:
        row = get_current_user_row(session, account_id=user_id)
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

    password: str = Field(min_length=8, max_length=256)


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
    user_id: str = Depends(require_user_id),
) -> SetPasswordResponse:
    """Set or change the caller's password (scrypt salt:hash, stdlib only)."""
    with session_scope() as session:
        row = get_current_user_row(session, account_id=user_id)
        row.password_hash = _hash_password(payload.password)
        row.last_seen_at = datetime.utcnow()
        return SetPasswordResponse(user=_row_to_dto(row), updated=True)


@router.post("/request-verify-email", response_model=VerifyQueuedResponse)
async def request_verify_email(
    request: Request,
    user_id: str = Depends(require_user_id),
) -> VerifyQueuedResponse:
    """Generate a verification token and log a debug "email" line.

    No real SMTP yet; the magic link is printed to the server logs so devs
    can copy/paste it. Old tokens for this user are cleared first.
    """
    with session_scope() as session:
        row = get_current_user_row(session, account_id=user_id)
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
        to_email = row.email

    base = (os.getenv("PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    link = f"{base}/auth/verify-email?token={token}" if base else f"/auth/verify-email?token={token}"
    send_email(
        to=to_email,
        subject="Verify your 508 Agent email",
        body=(
            "Confirm your email address by opening this link:\n\n"
            f"{link}\n\nThis link expires in 24 hours."
        ),
    )
    return VerifyQueuedResponse(queued=True)


@router.get("/verify-email", response_model=VerifyResultResponse)
async def verify_email(token: str) -> VerifyResultResponse:
    """Consume a verification token, mark the user verified, return ok."""
    if not token or len(token) > 64:
        raise HTTPException(status_code=410, detail="token_expired")
    # Password-reset tokens live in the same table under a "pr_" prefix; they
    # must never be usable to verify an email address.
    if token.startswith("pr_"):
        raise HTTPException(status_code=410, detail="token_expired")
    with session_scope() as session:
        row = session.execute(
            select(EmailVerifyTokenRow).where(EmailVerifyTokenRow.token == token)
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=410, detail="token_expired")
        # Enforce a 24-hour expiry window.
        if row.created_at is None or (datetime.utcnow() - row.created_at) > timedelta(hours=24):
            session.execute(
                delete(EmailVerifyTokenRow).where(EmailVerifyTokenRow.token == token)
            )
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


# ---------------------------------------------------------------------------
# Password reset (forgot password)
#
# Reuses the email-verify token table; reset tokens are namespaced with a
# "pr_" prefix so neither token kind can be consumed by the other endpoint
# (verify_email rejects pr_ tokens via the exact-match + the guard below;
# reset endpoints REQUIRE the prefix). Reset links expire after 1 hour and
# are single-use; requesting a new one invalidates older ones.
# ---------------------------------------------------------------------------

_RESET_PREFIX = "pr_"
_RESET_TTL = timedelta(hours=1)


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)


class PasswordResetQueuedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queued: bool


class PasswordResetConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=8, max_length=64)
    password: str = Field(min_length=8, max_length=256)


class PasswordResetResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reset: bool


@router.post("/request-password-reset", response_model=PasswordResetQueuedResponse)
async def request_password_reset(payload: PasswordResetRequest) -> PasswordResetQueuedResponse:
    """Email a single-use password-reset link.

    Always returns ``{"queued": true}`` regardless of whether the address has
    an account — anything else is an account-enumeration oracle. Unauthenticated
    by design (the caller has forgotten their password); covered by the /auth
    rate limit.
    """
    email = payload.email.strip().lower()
    token: Optional[str] = None
    to_email: Optional[str] = None
    with session_scope() as session:
        user = session.execute(
            select(UserRow).where(UserRow.email == email)
        ).scalar_one_or_none()
        if user is not None:
            # One outstanding reset link at a time.
            session.execute(
                delete(EmailVerifyTokenRow).where(
                    EmailVerifyTokenRow.user_id == user.id,
                    EmailVerifyTokenRow.token.like(f"{_RESET_PREFIX}%"),
                )
            )
            token = _RESET_PREFIX + secrets.token_hex(16)
            session.add(
                EmailVerifyTokenRow(
                    token=token,
                    user_id=user.id,
                    created_at=datetime.utcnow(),
                )
            )
            to_email = user.email

    if token and to_email:
        base = (os.getenv("PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
        link = f"{base}/reset-password?token={token}" if base else f"/reset-password?token={token}"
        send_email(
            to=to_email,
            subject="Reset your 508 Agent password",
            body=(
                "Someone (hopefully you) asked to reset the password for this "
                "account. Open this link to choose a new password:\n\n"
                f"{link}\n\n"
                "The link expires in 1 hour and can be used once. If you did "
                "not request this, you can ignore this email — your password "
                "is unchanged."
            ),
        )
    return PasswordResetQueuedResponse(queued=True)


@router.post("/reset-password", response_model=PasswordResetResultResponse)
async def reset_password(payload: PasswordResetConfirm) -> PasswordResetResultResponse:
    """Consume a reset token and set the new password (single-use, 1h TTL)."""
    token = payload.token.strip()
    if not token.startswith(_RESET_PREFIX):
        raise HTTPException(status_code=410, detail="token_expired")
    with session_scope() as session:
        row = session.execute(
            select(EmailVerifyTokenRow).where(EmailVerifyTokenRow.token == token)
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=410, detail="token_expired")
        if row.created_at is None or (datetime.utcnow() - row.created_at) > _RESET_TTL:
            session.execute(
                delete(EmailVerifyTokenRow).where(EmailVerifyTokenRow.token == token)
            )
            raise HTTPException(status_code=410, detail="token_expired")
        user = session.execute(
            select(UserRow).where(UserRow.id == row.user_id)
        ).scalar_one_or_none()
        if user is None:
            session.execute(
                delete(EmailVerifyTokenRow).where(EmailVerifyTokenRow.token == token)
            )
            raise HTTPException(status_code=410, detail="token_expired")
        user.password_hash = _hash_password(payload.password)
        user.last_seen_at = datetime.utcnow()
        # Single-use: clear every outstanding reset token for this user.
        session.execute(
            delete(EmailVerifyTokenRow).where(
                EmailVerifyTokenRow.user_id == user.id,
                EmailVerifyTokenRow.token.like(f"{_RESET_PREFIX}%"),
            )
        )
    return PasswordResetResultResponse(reset=True)
