"""HMAC-signed download URLs.

The previous ``GET /pipeline/files/{job_id}/{filename}`` endpoint relied on
the unguessability of the job id alone.  That's fine against random
scraping but doesn't survive a leaked URL or a log line getting copy-pasted
somewhere it shouldn't.

This module gives us short-lived, HMAC-SHA256-signed URLs:

    /pipeline/files/<job>/<file>?exp=<unix-ts>&sig=<hex>

The signature is over the canonical string ``f"{job}/{file}/{exp}"`` keyed
by ``settings.app_secret``.  ``exp`` is a unix timestamp; requests after
that point are rejected.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Tuple
from urllib.parse import quote

from app.config import get_settings


def _canonical(job_id: str, filename: str, exp: int) -> str:
    return f"{job_id}/{filename}/{int(exp)}"


def _sign(secret: str, msg: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()


def sign_file_url(job_id: str, filename: str, ttl: int = 3600) -> str:
    """Return a relative signed URL for the given pipeline artifact.

    Caller can prepend a base URL if it needs an absolute one.  ``ttl`` is
    in seconds; default is one hour.
    """

    settings = get_settings()
    exp = int(time.time()) + max(60, int(ttl))
    sig = _sign(settings.app_secret, _canonical(job_id, filename, exp))
    return (
        f"/pipeline/files/{quote(job_id, safe='')}/{quote(filename, safe='')}"
        f"?exp={exp}&sig={sig}"
    )


def verify_file_signature(job_id: str, filename: str, exp: int, sig: str) -> Tuple[bool, str]:
    """Validate a previously-signed URL.

    Returns ``(ok, reason)`` — the second element is a short string suitable
    for use as an HTTP 403/410 detail.  We never echo the secret or the
    expected signature back to the client.
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
    expected = _sign(settings.app_secret, _canonical(job_id, filename, exp_int))
    if not hmac.compare_digest(expected, sig):
        return False, "bad_signature"
    return True, "ok"


__all__ = ["sign_file_url", "verify_file_signature"]
