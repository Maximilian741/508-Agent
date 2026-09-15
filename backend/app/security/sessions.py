"""HS256-signed session JWTs.

Replaces the hackathon scaffold (where the "token" was simply the user's
account uuid).  Tokens are signed with ``settings.app_secret`` so they:

- survive a backend restart (provided ``APP_SECRET`` is set in the env)
- resist tampering (changing any claim invalidates the signature)
- expire after a fixed TTL
- can be REVOKED: each token carries the user's ``token_version`` at mint time

Claims are intentionally minimal:

    {"sub": "<user-id>", "ver": <int>, "iat": <unix>, "exp": <unix>}

Revocation: ``users.token_version`` is bumped on sign-out, password set/reset
and email change. A token whose ``ver`` no longer equals the user's current
version is dead, so a stolen token does not survive account recovery. Bumping
signs out EVERY device for that user — deliberate, it is the simplest correct
answer to "I think someone has my session". Tokens minted before ``ver``
existed carry no claim and count as version 0, so the deploy that introduced
it logged nobody out; the first bump retires them.

``verify_session`` checks only what the token itself can prove (signature,
expiry, issuer, audience). Comparing ``ver`` needs the user row, so it happens
where identity is resolved: :mod:`app.api.deps` via :func:`session_is_current`.
Nothing else should call ``verify_session`` to authenticate a request.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from app.config import get_settings

_log = logging.getLogger(__name__)

_ALGO = "HS256"
# Issuer/audience bind a token to this application so a token minted for a
# different service (sharing the secret by mistake) cannot be replayed here.
_ISS = "508-agent"
_AUD = "508-agent-session"
_VERSION_CLAIM = "ver"


def _default_ttl_seconds() -> int:
    try:
        return int(get_settings().session_ttl_seconds)
    except Exception:
        return 7 * 24 * 3600  # 7 days


def mint_session(user_id: str, ttl_seconds: Optional[int] = None, *, version: int = 0) -> str:
    """Mint an HS256-signed session JWT for ``user_id``.

    ``ttl_seconds`` defaults to ``settings.session_ttl_seconds`` (7 days),
    matching the lifetime of the token the frontend stores in localStorage.
    ``version`` must be the user's current ``token_version``.
    """

    if not user_id or not isinstance(user_id, str):
        raise ValueError("user_id must be a non-empty string")
    ttl = max(60, int(ttl_seconds if ttl_seconds is not None else _default_ttl_seconds()))
    now = datetime.now(tz=timezone.utc)
    claims = {
        "sub": user_id,
        _VERSION_CLAIM: int(version or 0),
        "iss": _ISS,
        "aud": _AUD,
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
    wrong issuer/audience, missing ``sub``).  Never raises.

    This does NOT check revocation — see :func:`session_is_current`.
    """

    if not token or not isinstance(token, str):
        return None
    secret = get_settings().app_secret
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[_ALGO],
            audience=_AUD,
            issuer=_ISS,
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


def session_version(claims: dict) -> Optional[int]:
    """The ``ver`` a token was minted with; a legacy token without one is 0.

    ``None`` for a present-but-malformed claim, which never matches.
    """
    raw = claims.get(_VERSION_CLAIM, 0)
    # bool is an int subclass; a signed token never carries one, reject anyway.
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def session_is_current(claims: dict, current_version: Optional[int]) -> bool:
    """True when the token was minted at the user's current ``token_version``."""
    ver = session_version(claims)
    return ver is not None and ver == int(current_version or 0)


__all__ = ["mint_session", "verify_session", "session_version", "session_is_current"]
