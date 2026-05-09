"""HS256-signed session JWTs.

Replaces the hackathon scaffold (where the "token" was simply the user's
account uuid).  Tokens are signed with ``settings.app_secret`` so they:

- survive a backend restart (provided ``APP_SECRET`` is set in the env)
- resist tampering (changing any claim invalidates the signature)
- expire after a fixed TTL

Claims are intentionally minimal:

    {"sub": "<user-id>", "iat": <unix>, "exp": <unix>}

When we move off the hackathon scaffold, swap the signing key + add an
audience claim ("aud") for the production CF Access flow.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from app.config import get_settings

_log = logging.getLogger(__name__)

_ALGO = "HS256"
_DEFAULT_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days


def mint_session(user_id: str, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> str:
    """Mint an HS256-signed session JWT for ``user_id``.

    ``ttl_seconds`` defaults to 30 days, matching the lifetime of the
    cookie/header the frontend stores in localStorage.
    """

    if not user_id or not isinstance(user_id, str):
        raise ValueError("user_id must be a non-empty string")
    ttl = max(60, int(ttl_seconds))
    now = datetime.now(tz=timezone.utc)
    claims = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    secret = get_settings().app_secret
    token = jwt.encode(claims, secret, algorithm=_ALGO)
    # PyJWT >=2 returns str; older returned bytes.  Normalize to str.
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token


def verify_session(token: str) -> Optional[dict]:
    """Verify an HS256 session JWT.  Returns the claim dict or ``None``.

    Returns ``None`` for any failure mode (bad signature, expired, malformed,
    missing ``sub``).  Never raises.
    """

    if not token or not isinstance(token, str):
        return None
    secret = get_settings().app_secret
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[_ALGO],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError as exc:
        _log.debug("[sessions] reject token: %s", exc)
        return None
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("[sessions] unexpected verify error: %s", exc)
        return None

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        return None
    return claims


__all__ = ["mint_session", "verify_session"]
