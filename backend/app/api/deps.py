"""Shared FastAPI authentication dependencies.

Identity is derived **only** from a verified ``Authorization: Bearer <jwt>``
session token (see :mod:`app.security.sessions`). The legacy, client-forgeable
``X-Account-Id`` header is no longer accepted as an authentication source — any
route that needs a caller identity depends on one of the helpers below.

- ``require_user_id``  -> the authenticated user id (str), or 401.
- ``require_user``     -> the authenticated ``UserRow`` (detached), or 401.
- ``require_admin``    -> the ``UserRow`` if the caller is an admin, else 403.
- ``optional_user``    -> the ``UserRow`` or ``None`` (never raises); for routes
                          that adapt their response to anonymous callers.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select

from app.config import get_settings
from app.db.models import ApiKeyRow, UserRow
from app.db.session_sqlalchemy import session_scope
from app.security.sessions import verify_session

# Developer API keys are prefixed so they're recognisable and never confused
# with a session JWT.
API_KEY_PREFIX = "ak_"


def _bearer_sub(authorization: Optional[str]) -> Optional[str]:
    """Extract a verified ``sub`` from an ``Authorization: Bearer`` header."""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    if not token:
        return None
    claims = verify_session(token)
    if not claims:
        return None
    sub = claims.get("sub")
    return sub if isinstance(sub, str) and sub else None


def require_user_id(authorization: Optional[str] = Header(default=None)) -> str:
    """Return the authenticated user id from a verified session JWT, else 401."""
    sub = _bearer_sub(authorization)
    if not sub:
        raise HTTPException(status_code=401, detail="authentication_required")
    return sub


def _load_user(user_id: str) -> UserRow:
    with session_scope() as session:
        row = session.execute(
            select(UserRow).where(UserRow.id == user_id)
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=401, detail="unknown_account")
        # Detach so attribute access is safe after the session closes.
        # UserRow has no lazy relationships, so this is safe.
        session.expunge(row)
        return row


def require_user(user_id: str = Depends(require_user_id)) -> UserRow:
    """Load and return the authenticated ``UserRow`` (detached), else 401."""
    return _load_user(user_id)


def require_admin(user_id: str = Depends(require_user_id)) -> UserRow:
    """Require the caller to be an admin, else 403.

    Fail-closed: an empty ``ADMIN_EMAILS`` list does NOT grant access. A caller
    is an admin only if their ``UserRow.role == 'admin'`` or their email is in
    ``ADMIN_EMAILS``.
    """
    row = _load_user(user_id)
    settings = get_settings()
    is_admin = (row.role == "admin") or settings.is_admin(row.email)
    if not is_admin:
        raise HTTPException(status_code=403, detail="admin_only")
    return row


def hash_api_key(plaintext: str) -> str:
    """SHA-256 of the full key. Keys are 256-bit random, so a direct hash lookup
    is not guessable and needs no constant-time compare (no low-entropy secret)."""
    return hashlib.sha256((plaintext or "").encode("utf-8")).hexdigest()


def _api_key_user_id(presented: Optional[str]) -> Optional[str]:
    """Resolve a presented API key to its owner's user id, or None.

    Looks up the key by its hash among non-revoked keys and stamps last-used.
    Never reveals whether a key exists (callers get a generic 401).
    """
    key = (presented or "").strip()
    if not key.startswith(API_KEY_PREFIX):
        return None
    digest = hash_api_key(key)
    with session_scope() as session:
        row = session.execute(
            select(ApiKeyRow)
            .where(ApiKeyRow.key_hash == digest)
            .where(ApiKeyRow.revoked_at.is_(None))
        ).scalar_one_or_none()
        if row is None:
            return None
        row.last_used_at = datetime.utcnow()  # committed on scope exit
        return row.user_id


def require_user_id_or_api_key(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
) -> str:
    """Authenticate via a session JWT (UI) OR a developer API key (programmatic).

    Deliberately SEPARATE from ``require_user_id`` so the API-key path can never
    weaken the core session auth used by every other route. Applied only to the
    free, read-only scanning endpoint. The key may arrive as ``X-API-Key`` or as
    ``Authorization: Bearer ak_…``.
    """
    sub = _bearer_sub(authorization)
    if sub:
        return sub
    candidate = x_api_key
    if not candidate and authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            candidate = parts[1].strip()
    uid = _api_key_user_id(candidate)
    if uid:
        return uid
    raise HTTPException(status_code=401, detail="authentication_required")


def optional_user(authorization: Optional[str] = Header(default=None)) -> Optional[UserRow]:
    """Return the authenticated ``UserRow`` if a valid token is present, else ``None``.

    Never raises — used by endpoints (e.g. ``/api/admin/whoami``) that must
    answer for anonymous callers too.
    """
    sub = _bearer_sub(authorization)
    if not sub:
        return None
    try:
        return _load_user(sub)
    except HTTPException:
        return None
