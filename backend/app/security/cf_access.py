"""Cloudflare Access JWT verification.

When the app sits behind Cloudflare Access, every authenticated request
arrives with a ``Cf-Access-Jwt-Assertion`` header.  We verify three things:

1. The JWT signature is valid against one of the JWKs published at
   ``https://{team_domain}/cdn-cgi/access/certs``.
2. The ``aud`` claim equals our application AUD tag.
3. ``exp`` is in the future.

Stretch design constraints:

- **No new pip dependencies.**  We try ``PyJWT`` if it happens to be on
  ``sys.path`` (it currently isn't, but pinning it would be the obvious
  follow-up).  Otherwise we fall back to ``cryptography`` if available.  If
  neither is available, we still decode the unverified payload and check
  ``aud`` / ``iss`` / ``exp`` so dev/test environments aren't broken — but
  emit a CRITICAL log line on every call so the operator can't miss it.
- **JWKS caching.**  Refresh at most once per ``_JWKS_TTL`` seconds.

Tracking: replacing the unverified-fallback path requires adding either
``PyJWT[crypto]`` or ``cryptography`` to ``backend/requirements.txt``.  See
``deploy/cloudflare-tunnel.md`` for the release-blocker note.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Capability detection — done once at import time.  These flags are also
# exposed for diagnostics, so operators can see which path is active.
# ---------------------------------------------------------------------------

_HAVE_PYJWT = False
_HAVE_CRYPTO = False

try:  # pragma: no cover - import path dependent on env
    import jwt as _pyjwt  # type: ignore  # noqa: F401

    _HAVE_PYJWT = True
except Exception:
    _pyjwt = None  # type: ignore

if not _HAVE_PYJWT:
    try:  # pragma: no cover - same
        from cryptography.hazmat.primitives.asymmetric import rsa, padding  # type: ignore
        from cryptography.hazmat.primitives import hashes, serialization  # type: ignore
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers  # type: ignore

        _HAVE_CRYPTO = True
    except Exception:
        _HAVE_CRYPTO = False


# ---------------------------------------------------------------------------
# JWKS cache
# ---------------------------------------------------------------------------


_JWKS_TTL = 60 * 60  # 1 hour
_jwks_lock = threading.Lock()
_jwks_cache: Dict[str, Dict[str, Any]] = {}  # team_domain -> {expires_at, keys}


def _fetch_jwks(team_domain: str) -> Dict[str, Any]:
    url = f"https://{team_domain}/cdn-cgi/access/certs"
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - https only
        body = resp.read()
    return json.loads(body)


def _get_jwks(team_domain: str) -> Dict[str, Any]:
    now = time.time()
    with _jwks_lock:
        cached = _jwks_cache.get(team_domain)
        if cached and cached.get("expires_at", 0) > now:
            return cached["data"]
    try:
        data = _fetch_jwks(team_domain)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        logger.warning("[cf_access] JWKS fetch failed for %s: %s", team_domain, exc)
        with _jwks_lock:
            cached = _jwks_cache.get(team_domain)
            if cached:
                return cached["data"]
        raise
    with _jwks_lock:
        _jwks_cache[team_domain] = {"expires_at": now + _JWKS_TTL, "data": data}
    return data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _b64url_decode(data: str) -> bytes:
    padding_needed = (-len(data)) % 4
    return base64.urlsafe_b64decode(data + ("=" * padding_needed))


def _decode_segment(segment: str) -> Dict[str, Any]:
    return json.loads(_b64url_decode(segment).decode("utf-8"))


def _find_key(jwks: Dict[str, Any], kid: Optional[str]) -> Optional[Dict[str, Any]]:
    keys = jwks.get("keys") or []
    if kid:
        for k in keys:
            if k.get("kid") == kid:
                return k
    # Fall back to first RSA key
    for k in keys:
        if k.get("kty") == "RSA":
            return k
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class CFAccessVerificationError(Exception):
    pass


def verify_cf_access_jwt(
    token: str,
    expected_aud: str,
    team_domain: str,
) -> Dict[str, Any]:
    """Verify a Cloudflare Access JWT and return its claims.

    Raises ``CFAccessVerificationError`` on failure.
    """

    if not token or not expected_aud or not team_domain:
        raise CFAccessVerificationError("missing token / aud / team_domain")

    parts = token.split(".")
    if len(parts) != 3:
        raise CFAccessVerificationError("malformed_jwt")

    header_seg, payload_seg, sig_seg = parts
    try:
        header = _decode_segment(header_seg)
        claims = _decode_segment(payload_seg)
    except Exception as exc:
        raise CFAccessVerificationError(f"jwt_decode_failed: {exc}") from exc

    # Claim checks.
    if int(claims.get("exp", 0)) < int(time.time()):
        raise CFAccessVerificationError("token_expired")

    expected_iss = f"https://{team_domain}"
    if claims.get("iss") not in (expected_iss, expected_iss + "/"):
        raise CFAccessVerificationError(
            f"bad_issuer: got={claims.get('iss')!r} expected={expected_iss!r}"
        )

    raw_aud = claims.get("aud")
    aud_list = raw_aud if isinstance(raw_aud, list) else [raw_aud]
    if expected_aud not in aud_list:
        raise CFAccessVerificationError("bad_audience")

    # Signature path.
    alg = (header.get("alg") or "").upper()
    if alg != "RS256":
        raise CFAccessVerificationError(f"unsupported_alg: {alg}")

    if _HAVE_PYJWT:
        return _verify_with_pyjwt(token, expected_aud, team_domain)

    if _HAVE_CRYPTO:
        return _verify_with_cryptography(
            header=header,
            claims=claims,
            signing_input=f"{header_seg}.{payload_seg}".encode("ascii"),
            signature=_b64url_decode(sig_seg),
            team_domain=team_domain,
        )

    # Last resort: claims-only verification.  Log loudly so this never
    # ships to production unnoticed.
    logger.critical(
        "[cf_access] RS256 signature NOT verified — neither PyJWT nor "
        "cryptography is installed.  Claims-only mode is INSECURE and must "
        "not be used in production.  Add PyJWT[crypto] or cryptography to "
        "requirements.txt."
    )
    return claims


def _verify_with_pyjwt(token: str, expected_aud: str, team_domain: str) -> Dict[str, Any]:
    jwks = _get_jwks(team_domain)
    # PyJWT >= 2.0 has PyJWKClient — but we don't want a remote roundtrip
    # per call.  Build the keyset manually.
    from jwt.algorithms import RSAAlgorithm  # type: ignore

    last_err: Optional[Exception] = None
    for k in jwks.get("keys", []):
        try:
            public_key = RSAAlgorithm.from_jwk(json.dumps(k))
            return _pyjwt.decode(  # type: ignore[union-attr]
                token,
                public_key,
                algorithms=["RS256"],
                audience=expected_aud,
                issuer=f"https://{team_domain}",
            )
        except Exception as exc:  # try next key
            last_err = exc
            continue
    raise CFAccessVerificationError(f"jwt_verify_failed: {last_err}")


def _verify_with_cryptography(
    *,
    header: Dict[str, Any],
    claims: Dict[str, Any],
    signing_input: bytes,
    signature: bytes,
    team_domain: str,
) -> Dict[str, Any]:
    jwks = _get_jwks(team_domain)
    key = _find_key(jwks, header.get("kid"))
    if not key:
        raise CFAccessVerificationError("no_matching_jwk")

    try:
        n = int.from_bytes(_b64url_decode(key["n"]), "big")
        e = int.from_bytes(_b64url_decode(key["e"]), "big")
        public_key = RSAPublicNumbers(e=e, n=n).public_key()
    except Exception as exc:
        raise CFAccessVerificationError(f"jwk_parse_failed: {exc}") from exc

    try:
        public_key.verify(
            signature,
            signing_input,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception as exc:
        raise CFAccessVerificationError("bad_signature") from exc

    return claims


__all__ = ["verify_cf_access_jwt", "CFAccessVerificationError"]
