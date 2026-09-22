"""HMAC-signed download URLs.

The previous ``GET /pipeline/files/{job_id}/{filename}`` endpoint relied on
the unguessability of the job id alone.  That's fine against random
scraping but doesn't survive a leaked URL or a log line getting copy-pasted
somewhere it shouldn't.

This module gives us short-lived, HMAC-SHA256-signed URLs:

    /pipeline/files/<job>/<file>?exp=<unix-ts>&sig=<hex>

The signature is over the canonical string ``f"{job}/{file}/{exp}/{owner}"``
keyed by ``settings.app_secret``.  ``exp`` is a unix timestamp; requests after
that point are rejected.

``owner`` is the REVOCATION binding and never travels in the URL — the
download route re-derives it from the job manifest plus the live user row, so
a URL only verifies while the session generation it was minted under is still
current.  Without it a signed URL was an unrevocable bearer token: it kept
serving the document after sign-out, after a password reset, and after the
account was deleted, because the signature covered nothing that any of those
events changes.  Now bumping ``users.token_version`` (what sign-out, set
password and reset-password all do) invalidates every outstanding artifact URL
the same way it already invalidates session JWTs and API keys, and
``GET /pipeline/jobs`` re-mints a fresh one for the owner on demand.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Tuple
from urllib.parse import quote

from app.config import get_settings


def _canonical(job_id: str, filename: str, exp: int, owner: str = "") -> str:
    return f"{job_id}/{filename}/{int(exp)}/{owner or ''}"


def _sign(secret: str, msg: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()


def sign_file_url(job_id: str, filename: str, ttl: int = 3600, owner: str = "") -> str:
    """Return a relative signed URL for the given pipeline artifact.

    Caller can prepend a base URL if it needs an absolute one.  ``ttl`` is
    in seconds; default is one hour.  ``owner`` is an opaque revocation token
    (see the module docstring) that is covered by the signature but is NOT
    placed in the URL — the verifier re-derives it server-side.
    """

    settings = get_settings()
    exp = int(time.time()) + max(60, int(ttl))
    sig = _sign(settings.app_secret, _canonical(job_id, filename, exp, owner))
    return (
        f"/pipeline/files/{quote(job_id, safe='')}/{quote(filename, safe='')}"
        f"?exp={exp}&sig={sig}"
    )


def verify_file_signature(
    job_id: str, filename: str, exp: int, sig: str, owner: str = ""
) -> Tuple[bool, str]:
    """Validate a previously-signed URL.

    Returns ``(ok, reason)`` — the second element is a short string suitable
    for use as an HTTP 403/410 detail.  We never echo the secret or the
    expected signature back to the client.  ``owner`` must be the same
    revocation token the URL was minted with; a stale one fails closed as
    ``url_revoked`` so the caller can say why rather than implying tampering.
    """

    if not sig or not exp:
        return False, "missing_signature"
    try:
        exp_int = int(exp)
    except (TypeError, ValueError):
        return False, "bad_expiry"
    if exp_int < int(time.time()):
        return False, "url_expired"

    settings = get_settings()
    expected = _sign(settings.app_secret, _canonical(job_id, filename, exp_int, owner))
    if hmac.compare_digest(expected, sig):
        return True, "ok"
    # Distinguish "signed for an older session generation" from "forged". Both
    # are refused; only the reason differs, and neither leaks the secret.
    if owner:
        for older in _previous_owner_tokens(owner):
            if hmac.compare_digest(_sign(settings.app_secret, _canonical(job_id, filename, exp_int, older)), sig):
                return False, "url_revoked"
    return False, "bad_signature"


def _previous_owner_tokens(owner: str) -> Tuple[str, ...]:
    """Owner tokens for earlier session generations of the same account.

    Only used to pick the honest 403 reason. The token is ``"<user>:<n>"``;
    we probe a short window of earlier ``n`` rather than every value, which is
    enough to recognise a just-revoked URL without turning verification into a
    scan.
    """
    user, _, version = str(owner).rpartition(":")
    if not user or not version.isdigit():
        return ()
    n = int(version)
    return tuple(f"{user}:{v}" for v in range(max(0, n - 5), n))


__all__ = ["sign_file_url", "verify_file_signature"]
