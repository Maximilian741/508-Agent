"""Developer API keys — create / list / revoke.

These keys authenticate the programmatic accessibility **scanning** API (the
free, read-only ``POST /pipeline/analyze`` endpoint). Managing keys requires a
normal signed-in session (JWT) — you cannot mint or list keys with an API key,
only use them to scan.

Security model:
  * The plaintext key (``ak_live_<random>``) is shown EXACTLY ONCE, at creation.
  * Only a SHA-256 hash is stored; the plaintext is unrecoverable afterwards.
  * Keys are 256-bit random, so a hash lookup is not guessable.
  * Listing/revoking is strictly owner-scoped (a user only sees their own keys);
    revoking a key you don't own returns 404 (no existence disclosure).
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import API_KEY_PREFIX, hash_api_key, require_user_id
from app.db.models import ApiKeyRow
from app.db.session_sqlalchemy import session_scope

router = APIRouter(prefix="/api-keys", tags=["api-keys"])

_MAX_KEYS_PER_USER = 25


class CreateApiKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="API key", max_length=120)


class ApiKeyDTO(BaseModel):
    id: str
    name: str
    keyPrefix: str
    createdAt: str
    lastUsedAt: Optional[str] = None
    revoked: bool


class CreatedApiKeyDTO(ApiKeyDTO):
    # The full secret — returned ONLY here, once, never again.
    key: str


def _to_dto(row: ApiKeyRow) -> ApiKeyDTO:
    return ApiKeyDTO(
        id=row.id,
        name=row.name,
        keyPrefix=row.key_prefix,
        createdAt=row.created_at.isoformat(),
        lastUsedAt=row.last_used_at.isoformat() if row.last_used_at else None,
        revoked=row.revoked_at is not None,
    )


@router.post("", response_model=CreatedApiKeyDTO)
async def create_api_key(
    payload: CreateApiKeyRequest,
    user_id: str = Depends(require_user_id),
) -> CreatedApiKeyDTO:
    """Mint a new API key for the signed-in user; returns the plaintext once."""
    name = (payload.name or "API key").strip()[:120] or "API key"
    # ak_live_<43 url-safe chars> — 256 bits of entropy.
    plaintext = f"{API_KEY_PREFIX}live_{secrets.token_urlsafe(32)}"
    key_id = secrets.token_hex(8)
    prefix = plaintext[:16]
    now = datetime.utcnow()
    with session_scope() as session:
        active = session.execute(
            select(ApiKeyRow)
            .where(ApiKeyRow.user_id == user_id)
            .where(ApiKeyRow.revoked_at.is_(None))
        ).scalars().all()
        if len(active) >= _MAX_KEYS_PER_USER:
            raise HTTPException(status_code=400, detail="too_many_api_keys")
        row = ApiKeyRow(
            id=key_id,
            user_id=user_id,
            name=name,
            key_hash=hash_api_key(plaintext),
            key_prefix=prefix,
            created_at=now,
        )
        session.add(row)
        session.flush()
    return CreatedApiKeyDTO(
        id=key_id,
        name=name,
        keyPrefix=prefix,
        createdAt=now.isoformat(),
        lastUsedAt=None,
        revoked=False,
        key=plaintext,
    )


@router.get("", response_model=List[ApiKeyDTO])
async def list_api_keys(user_id: str = Depends(require_user_id)) -> List[ApiKeyDTO]:
    """List the signed-in user's API keys (never the secret)."""
    with session_scope() as session:
        rows = session.execute(
            select(ApiKeyRow)
            .where(ApiKeyRow.user_id == user_id)
            .order_by(ApiKeyRow.created_at.desc())
        ).scalars().all()
        return [_to_dto(r) for r in rows]


@router.post("/{key_id}/revoke", response_model=ApiKeyDTO)
async def revoke_api_key(key_id: str, user_id: str = Depends(require_user_id)) -> ApiKeyDTO:
    """Revoke one of the caller's keys. 404 if it isn't theirs (no disclosure)."""
    with session_scope() as session:
        row = session.get(ApiKeyRow, key_id)
        if row is None or row.user_id != user_id:
            raise HTTPException(status_code=404, detail="api_key_not_found")
        if row.revoked_at is None:
            row.revoked_at = datetime.utcnow()
        session.flush()
        return _to_dto(row)
