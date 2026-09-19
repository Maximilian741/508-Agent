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
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.api.deps import optional_user, require_user_id
from app.config import get_settings
from app.db.models import (
    ApiKeyRow,
    CreditLedgerRow,
    EmailVerifyTokenRow,
    StarterGrantRow,
    UserRow,
)
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
    # Required to change the EMAIL when the account has a password (see
    # _require_current_password). A name-only change never needs it.
    currentPassword: Optional[str] = Field(default=None, max_length=256)


class UpdateProfileResponse(UserDTO):
    # Set only when the email changed. That revokes every session, the
    # caller's included, so the caller must switch to this fresh token.
    token: Optional[str] = None
    # API keys revoked by an email change (they outlive session tokens).
    apiKeysRevoked: int = 0


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


def _bump_token_version(row: UserRow) -> int:
    """Revoke every outstanding session for ``row``; returns the new version.

    Each token carries the version it was minted at (app.security.sessions),
    so incrementing it kills all of them at once: every device is signed out.
    """
    row.token_version = int(row.token_version or 0) + 1
    return row.token_version


def _require_current_password(row: UserRow, provided: Optional[str]) -> None:
    """Re-authenticate before an identity change. No-op without a password.

    A session token alone must not be enough to TAKE OVER an account.
    Changing the email moves where every future reset link goes; changing
    the password locks the owner out. Both were reachable with a stolen or
    borrowed session, which turned "someone used my laptop" into a
    permanent loss of the account.

    An account with no password yet (legacy, or created before passwords)
    has nothing to prove, so it can set one without this — that path adds
    a credential rather than replacing one.
    """
    stored = row.password_hash
    if not stored:
        return
    if not provided:
        raise HTTPException(status_code=403, detail="current_password_required")
    if not _verify_password(provided, stored):
        raise HTTPException(status_code=403, detail="invalid_current_password")


def _revoke_api_keys(session, user_id: str, reason: str) -> int:
    """Revoke the user's active API keys; returns how many.

    Bumping token_version kills session JWTs but not API keys, and a stolen
    session can mint one (POST /api-keys). Account recovery has to end every
    credential, so reset/set-password revoke them too. The owner sees them in
    Settings marked with ``reason`` and can create replacements.
    """
    result = session.execute(
        update(ApiKeyRow)
        .where(ApiKeyRow.user_id == user_id, ApiKeyRow.revoked_at.is_(None))
        .values(revoked_at=datetime.utcnow(), revoked_reason=reason)
    )
    return int(result.rowcount or 0)


_GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})


def canonical_mailbox(email: str) -> str:
    """The inbox an address really delivers to, for once-per-mailbox limits.

    Lowercase; drop a ``+tag`` from the local part (``alice+1@x.com`` lands in
    ``alice@x.com``); for Gmail also drop dots and fold googlemail.com into
    gmail.com, since Gmail ignores both. Used only for starter-grant
    eligibility; the login email itself stays exactly as typed.
    """
    addr = (email or "").strip().lower()
    local, sep, domain = addr.rpartition("@")
    if not sep or not local or not domain:
        return addr
    local = local.split("+", 1)[0] or local
    if domain in _GMAIL_DOMAINS:
        local = local.replace(".", "") or local
        domain = "gmail.com"
    return f"{local}@{domain}"


def mailbox_hash(email: str) -> str:
    """SHA-256 of :func:`canonical_mailbox`, stored instead of the address so
    the one-grant-per-mailbox record can outlive account deletion without
    keeping the email. (Frozen copy in alembic 0015 for the backfill.)"""
    return hashlib.sha256(canonical_mailbox(email).encode("utf-8")).hexdigest()


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
                _bump_token_version(row)
            row.last_seen_at = now
            if payload.displayName and payload.displayName.strip():
                row.display_name = payload.displayName.strip()[:120]
        dto = _row_to_dto(row)
        version = int(row.token_version or 0)

    # Mint an HS256-signed session JWT.  The token survives backend
    # restarts (assuming APP_SECRET is set), resists tampering, and dies when
    # the user's token_version is bumped.
    token = mint_session(dto.id, version=version)
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
async def sign_out(user: Optional[UserRow] = Depends(optional_user)) -> Response:
    """Revoke the caller's sessions server-side, then 204.

    A valid token bumps the user's ``token_version``, which ends EVERY session
    on the account (all devices), not just this one — so a copied token dies
    too. No auth required: signing out with a missing, expired or already
    revoked token still succeeds and changes nothing.
    """
    if user is not None:
        with session_scope() as session:
            row = session.get(UserRow, user.id)
            if row is not None:
                _bump_token_version(row)
    return Response(status_code=204)


@router.post("/grant-starter", response_model=GrantStarterResponse)
async def grant_starter(
    request: Request,
    user_id: str = Depends(require_user_id),
) -> GrantStarterResponse:
    """Grant 25 credits exactly once per user AND once per mailbox (idempotent).

    Abuse guards:
    - when an email pipeline is configured (SMTP_HOST set), the grant requires
      a VERIFIED email — otherwise throwaway addresses can farm 25 free
      credits per signup. In dev (no SMTP) this gate is off.
    - one grant per real inbox: ``alice+1@gmail.com``, ``a.lice@gmail.com``
      and ``alice@googlemail.com`` all deliver to ``alice@gmail.com``, so
      verifying each proves nothing new. The grant is recorded in
      ``starter_grants`` under :func:`mailbox_hash` (primary key), which
      survives email changes and account deletion. A mailbox that already
      got its grant answers ``granted=False`` exactly like a repeat call.
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
        not_granted = GrantStarterResponse(user=_row_to_dto(row), granted=False, amount=0)
        if existing is not None:
            return not_granted

        mailbox = mailbox_hash(row.email)
        if session.get(StarterGrantRow, mailbox) is not None:
            return not_granted

        now = datetime.utcnow()
        session.add(StarterGrantRow(mailbox_hash=mailbox, user_id=row.id, granted_at=now))
        session.add(
            CreditLedgerRow(
                user_id=row.id,
                at=now,
                kind=GRANT_KIND,
                amount=GRANT_AMOUNT,
                description=GRANT_DESC,
                related_doc_id=None,
            )
        )
        row.credits_balance = int(row.credits_balance or 0) + GRANT_AMOUNT
        try:
            session.flush()
        except IntegrityError:
            # A concurrent request claimed this mailbox first (primary key).
            session.rollback()
            return not_granted

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


@router.patch("/me", response_model=UpdateProfileResponse)
async def update_me(
    payload: UpdateProfileRequest,
    request: Request,
    user_id: str = Depends(require_user_id),
) -> UpdateProfileResponse:
    """Update the current user's display name and/or email.

    Requires a valid session token. An address that belongs to another
    account, or that is listed in ADMIN_EMAILS, returns 409 ``email_in_use`` —
    one answer for both, so this is no oracle for which addresses are admin.

    Changing the email changes identity, so it:
    - clears ``emailVerifiedAt`` (the new address must prove itself; the
      starter grant keys off verification) and drops admin (``role``);
    - discards outstanding verify/reset links, which went to the OLD inbox;
    - revokes every session and returns a fresh ``token`` for this caller.
    """
    token: Optional[str] = None
    revoked = 0
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
                if clash is not None or get_settings().is_admin(new_email):
                    raise HTTPException(status_code=409, detail="email_in_use")
                # Prove it is the owner, not a borrowed session.
                _require_current_password(row, payload.currentPassword)
                row.email = new_email
                row.email_verified_at = None
                # Admin is bound to the address the operator promoted.
                if row.role == "admin":
                    row.role = "user"
                session.execute(
                    delete(EmailVerifyTokenRow).where(EmailVerifyTokenRow.user_id == row.id)
                )
                token = mint_session(row.id, version=_bump_token_version(row))
                # Keys outlive session tokens, and the new address now owns
                # account recovery: end every credential minted before it.
                revoked = _revoke_api_keys(session, row.id, "email_changed")

        row.last_seen_at = datetime.utcnow()
        return UpdateProfileResponse(
            **_row_to_dto(row).model_dump(), token=token, apiKeysRevoked=revoked
        )


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
    # Required when the account already HAS a password: proving you know it
    # is what stops a stolen session from locking the owner out.
    currentPassword: Optional[str] = Field(default=None, max_length=256)


class SetPasswordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: UserDTO
    updated: bool
    # Changing the password revokes every session, the caller's included;
    # the caller switches to this fresh token.
    token: Optional[str] = None
    # How many active API keys were revoked with it, so the UI can say so.
    apiKeysRevoked: int = 0


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
    """Set or change the caller's password (scrypt salt:hash, stdlib only).

    Changing an existing password requires ``currentPassword``: a stolen
    session must not be able to lock the owner out. Setting a first password
    does not — there is no credential to prove yet.

    Revokes every session on the account AND its API keys — a new password
    must lock out whoever else was signed in, and a key they minted would
    outlive the sessions — then returns a fresh ``token`` for the caller.
    """
    with session_scope() as session:
        row = get_current_user_row(session, account_id=user_id)
        # Changing an EXISTING password requires knowing it; setting a first
        # one does not (there is nothing to prove, and no owner to lock out).
        _require_current_password(row, payload.currentPassword)
        row.password_hash = _hash_password(payload.password)
        row.last_seen_at = datetime.utcnow()
        token = mint_session(row.id, version=_bump_token_version(row))
        revoked = _revoke_api_keys(session, row.id, "password_changed")
        return SetPasswordResponse(
            user=_row_to_dto(row),
            updated=True,
            token=token,
            apiKeysRevoked=revoked,
        )


@router.post("/request-verify-email", response_model=VerifyQueuedResponse)
async def request_verify_email(
    request: Request,
    user_id: str = Depends(require_user_id),
) -> VerifyQueuedResponse:
    """Email a single-use verification link (24h TTL).

    Without SMTP the mailer logs the message instead, so a dev can copy the
    link out of the server log. Old tokens for this user are cleared first.
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

    # PUBLIC_BASE_URL is the APP (frontend) origin, so the link must be a page
    # route there — /auth/verify-email was an API path the web app never served,
    # so every emailed link 404'd. /verify-email mirrors /reset-password?token=…
    # and stays clear of /verify?cert=… (certificate verification). The page
    # calls GET {API}/auth/verify-email?token=… below.
    base = (os.getenv("PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    link = f"{base}/verify-email?token={token}" if base else f"/verify-email?token={token}"
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
    # API keys revoked along with the sessions, so the page can say so.
    apiKeysRevoked: int = 0


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
    """Consume a reset token and set the new password (single-use, 1h TTL).

    Recovery ends every credential an attacker could hold: sessions (via
    token_version) and API keys alike.
    """
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
        # Account recovery must end every session, a stolen one included.
        _bump_token_version(user)
        revoked = _revoke_api_keys(session, user.id, "password_reset")
        # Single-use: clear every outstanding reset token for this user.
        session.execute(
            delete(EmailVerifyTokenRow).where(
                EmailVerifyTokenRow.user_id == user.id,
                EmailVerifyTokenRow.token.like(f"{_RESET_PREFIX}%"),
            )
        )
    return PasswordResetResultResponse(reset=True, apiKeysRevoked=revoked)
