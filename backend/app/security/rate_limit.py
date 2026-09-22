"""Rate limiting: one middleware with per-IP AND per-account buckets, plus a
per-identity credential throttle.

Currently in-memory; sufficient for single-instance deployments behind
Cloudflare. Applied to sensitive/expensive paths (/auth/*, /credits/*,
/pipeline/*, /billing/*, /teams/*, /tools/*, /documents/upload).

Who a request is billed to decides which bucket it spends:

* **Anonymous** requests, and every **credential endpoint** under /auth
  (sign-in, reset, verify, grant...) whatever token they carry, are keyed per
  IP: ``limit`` (60) per ``window`` (60 s). Password guessing and inbox
  flooding stay bounded by source address exactly as before.
* The **account-free scan** (anonymous ``POST /pipeline/analyze``) also spends
  a much stricter per-IP bucket: ``ANON_SCAN_PER_HOUR`` (10) per hour. It is
  free CPU with no account behind it.
* **Authenticated** requests (a session JWT whose signature verifies, or an API
  key that exists) are keyed per ACCOUNT, so a batch of 30 files or an office
  of 20 people behind one NAT no longer share one 60/min bucket. Document work
  (/pipeline, /credits, /documents/upload) gets ``RATE_LIMIT_USER_PER_MIN``
  (300); account/billing/team/tool calls keep ``limit`` (60) per account. A
  per-IP backstop (``RATE_LIMIT_AUTHENTICATED_IP_PER_MIN``, 1200) stops one
  address from multiplying buckets by minting accounts. The free routes that
  call a paid AI provider (/tools) ALSO keep the old per-IP ``limit`` (60)
  across every account on that address: there, minting accounts would mint
  AI spend.

A JWT is checked by signature only (no DB): it cannot be forged without
APP_SECRET, and a revoked one still 401s at the route. An API key is looked up
(read-only) because a random ``ak_`` string must NOT buy a fresh bucket; an
unknown key, like a bad token, falls back to the per-IP bucket.

Deriving "the IP" is the whole security of the per-IP half: every forwarded-IP
header is written by whoever is talking to us, so trusting one that an attacker
can set turns the limiter off (rotate the header, get a fresh bucket per
request). See :meth:`RateLimitMiddleware._client_ip` for the rule.

The per-IP limiter also cannot see a botnet, a NAT, or — behind a proxy with
TRUST_PROXY_HEADERS off — any distinction between clients at all, so
:class:`CredentialThrottle` bounds guessing against a single *account*
independently of where the requests come from.

A 429 carries ``Retry-After`` (seconds until the bucket frees a slot) and the
coded body every other error uses: ``{"detail": "rate_limited", "code", "message",
"retryAfter"}``.

# TODO: swap for Redis in multi-instance deploys. The in-memory dicts
# below will allow N x limit total requests across N replicas, which
# defeats the purpose under horizontal scale. A Redis INCR + EXPIRE
# (or a token-bucket Lua script) is the standard fix.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, Optional, Tuple

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


_RATE_LIMITED_PREFIXES: Tuple[str, ...] = (
    "/auth",
    "/credits",
    "/pipeline",
    "/billing",  # checkout / cert issuance / overage — abuse + Stripe-cost surface
    "/teams",  # invite endpoint sends email per call — block invite/email spam
    "/tools",  # standalone AI utilities (alt-text) — bound vision-AI cost/abuse
)
_RATE_LIMITED_EXACT: frozenset = frozenset({"/documents/upload"})
# Stripe POSTs webhooks here and retries on any non-2xx; a 429 would silently
# drop real payment events, so this exact path is never rate-limited.
_RATE_LIMIT_EXEMPT_EXACT: frozenset = frozenset({"/billing/webhook"})
_DEFAULT_LIMIT = 60
_DEFAULT_WINDOW_SECONDS = 60.0
# Cap the bucket dictionaries so a flood from many distinct sources (or many
# guessed emails) cannot grow them without bound. When full we drop the whole
# map rather than LRU-evicting: an evicted key would get a free reset anyway,
# and this keeps the hot path a plain dict lookup.
_MAX_BUCKETS = 50_000

# Document work an authenticated account may do per window. A batch page runs
# ~3 calls per file with three files in flight, so 60/min capped a batch at
# ~20 files a minute for the whole office behind one address.
_WORK_PREFIXES: Tuple[str, ...] = ("/pipeline", "/credits")
_WORK_EXACT: frozenset = frozenset({"/documents/upload"})
# The account-free scan: the only thing an anonymous caller can make us DO.
_ANON_SCAN_PATH = "/pipeline/analyze"
# Under /auth only this read is keyed per account; every other /auth route
# either checks a credential or sends an email, and stays per-IP whatever
# token the request carries.
_AUTH_PER_ACCOUNT: frozenset = frozenset({("GET", "/auth/me")})
# Free routes that call a paid AI provider (0 credits, no email verification):
# signed-in callers still share ONE per-address budget of ``limit`` for these.
_FREE_AI_PREFIXES: Tuple[str, ...] = ("/tools",)
# Signed artifact downloads carry no session (the HMAC is the credential), so
# they would otherwise spend the anonymous 60/min: a person saving a batch of
# files one by one is not an abuser. Guessing an HMAC is not a rate problem.
_SIGNED_DOWNLOAD_PREFIX = "/pipeline/files/"


def _env_int(name: str, default: int) -> int:
    try:
        value = int(str(os.environ.get(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _is_rate_limited_path(path: str) -> bool:
    if path in _RATE_LIMIT_EXEMPT_EXACT:
        return False
    if path in _RATE_LIMITED_EXACT:
        return True
    for prefix in _RATE_LIMITED_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def _has_prefix(path: str, prefixes: Tuple[str, ...]) -> bool:
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def _is_work_path(path: str) -> bool:
    return path in _WORK_EXACT or _has_prefix(path, _WORK_PREFIXES)


def _is_per_ip_only(method: str, path: str) -> bool:
    """Credential / email-sending endpoints: always keyed on the source IP."""
    return _has_prefix(path, ("/auth",)) and (method.upper(), path) not in _AUTH_PER_ACCOUNT


def _bearer(authorization: str) -> str:
    parts = (authorization or "").split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()


def _message_for(retry_after: int, *, anonymous_scan: bool) -> str:
    if anonymous_scan:
        minutes = max(1, int(math.ceil(retry_after / 60.0)))
        return (
            "You've used the free checks available without an account for now. "
            "Create a free account to keep checking files, or try again in "
            f"{minutes} minute{'s' if minutes != 1 else ''}."
        )
    return (
        f"Too many requests. Please wait {retry_after} second{'s' if retry_after != 1 else ''} "
        "and try again."
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter: per IP for anonymous/credential traffic,
    per account for authenticated work (see the module docstring)."""

    def __init__(
        self,
        app,
        *,
        limit: int = _DEFAULT_LIMIT,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
        trust_proxy_headers: bool = False,
        user_limit: Optional[int] = None,
        anon_scan_limit: Optional[int] = None,
        anon_scan_window_seconds: float = 3600.0,
        authenticated_ip_limit: Optional[int] = None,
        download_limit: Optional[int] = None,
    ) -> None:
        super().__init__(app)
        self._limit = int(limit)
        self._window = float(window_seconds)
        self._trust_proxy = bool(trust_proxy_headers)
        self._user_limit = int(user_limit if user_limit is not None else _env_int("RATE_LIMIT_USER_PER_MIN", 300))
        self._anon_scan_limit = int(
            anon_scan_limit if anon_scan_limit is not None else _env_int("ANON_SCAN_PER_HOUR", 10)
        )
        self._anon_scan_window = float(anon_scan_window_seconds)
        self._auth_ip_limit = int(
            authenticated_ip_limit
            if authenticated_ip_limit is not None
            else _env_int("RATE_LIMIT_AUTHENTICATED_IP_PER_MIN", 1200)
        )
        self._download_limit = int(download_limit if download_limit is not None else max(self._user_limit, self._limit))
        self._buckets: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def _client_ip(self, request: Request) -> str:
        """The address to bill this request to. Never a value a client chose.

        Forwarded-IP headers are only consulted when the operator has declared
        that the origin sits behind a trusted proxy (TRUST_PROXY_HEADERS) — and
        that declaration is only true when the origin is *unreachable* except
        through that proxy. Reachable directly, every header below is typed by
        the attacker and rotating one buys a fresh bucket per request.

        Even when trusted, which part of the header is load-bearing:

        - ``CF-Connecting-IP`` is *overwritten* by Cloudflare on every request
          it proxies, so behind the tunnel it is the real client and nothing
          downstream of the client can forge it. Preferred.
        - ``X-Forwarded-For`` is *appended* to, hop by hop — nginx's
          ``$proxy_add_x_forwarded_for`` yields "<whatever the client sent>,
          <the peer nginx actually saw>". So the LEFTMOST entry is attacker
          text and the RIGHTMOST is the address our own proxy observed. Reading
          ``split(",")[0]`` handed the key straight to the attacker; we take
          the last hop instead.
        """
        peer = getattr(getattr(request, "client", None), "host", "") or "unknown"
        if not self._trust_proxy:
            return peer
        cf_ip = request.headers.get("cf-connecting-ip")
        if cf_ip and cf_ip.strip():
            return cf_ip.strip()
        xff = request.headers.get("x-forwarded-for")
        if xff:
            hops = [part.strip() for part in xff.split(",") if part.strip()]
            if hops:
                return hops[-1]
        return peer

    def _hit(self, key: str, now: float, limit: int, window: float) -> int:
        """Record one request against ``key``. 0 = allowed, else seconds to wait."""
        cutoff = now - window
        with self._lock:
            if len(self._buckets) > _MAX_BUCKETS:
                self._buckets.clear()
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = deque()
                self._buckets[key] = bucket
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                # The slot frees when the oldest request in the window ages out.
                return max(1, int(math.ceil(bucket[0] + window - now)))
            bucket.append(now)
            return 0

    def _check_and_record(self, ip: str, now: float) -> bool:
        """The per-IP bucket at the constructor's ``limit`` / ``window``."""
        return self._hit(ip, now, self._limit, self._window) == 0

    async def _identity(self, request: Request) -> Optional[str]:
        """``"user:<id>"`` for a verifiable credential, else ``None``.

        Never raises: anything odd simply means "anonymous", which is the
        STRICTER bucket, so a failure here can only ever limit harder.
        """
        try:
            token = _bearer(request.headers.get("authorization") or "")
            if token and not token.startswith("ak_"):
                from app.security.sessions import verify_session

                claims = verify_session(token)
                sub = (claims or {}).get("sub")
                return f"user:{sub}" if sub else None
            candidate = (request.headers.get("x-api-key") or "").strip() or token
            if candidate.startswith("ak_"):
                from starlette.concurrency import run_in_threadpool

                from app.api.deps import api_key_owner_id

                owner = await run_in_threadpool(api_key_owner_id, candidate)
                return f"user:{owner}" if owner else None
        except Exception:
            return None
        return None

    @staticmethod
    def _limited(retry_after: int, *, anonymous_scan: bool = False) -> JSONResponse:
        # ``code`` is ALWAYS "rate_limited" (one stable code per condition, so a
        # client needs a single branch); ``scope`` says which budget ran out,
        # and ``message`` already tells the person what to do about it.
        return JSONResponse(
            status_code=429,
            content={
                "detail": "rate_limited",
                "code": "rate_limited",
                "scope": "anonymous_scan" if anonymous_scan else "requests",
                "message": _message_for(retry_after, anonymous_scan=anonymous_scan),
                "retryAfter": int(retry_after),
            },
            headers={"Retry-After": str(int(retry_after))},
        )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if not _is_rate_limited_path(path):
            return await call_next(request)

        method = request.method.upper()
        ip = self._client_ip(request)
        now = time.monotonic()

        identity: Optional[str] = None
        if not _is_per_ip_only(method, path):
            identity = await self._identity(request)

        if identity is not None:
            # One budget per ACCOUNT (a session and that account's API keys
            # share it), plus a generous per-address backstop.
            family = "work" if _is_work_path(path) else "account"
            limit = self._user_limit if family == "work" else self._limit
            wait = self._hit(f"{identity}|{family}", now, limit, self._window)
            if not wait:
                wait = self._hit(f"authip:{ip}", now, self._auth_ip_limit, self._window)
            if not wait and _has_prefix(path, _FREE_AI_PREFIXES):
                # Free paid-AI tools keep the old per-ADDRESS ceiling too:
                # accounts cost nothing to create, so per-account buckets alone
                # would let one address multiply vision-AI spend by minting
                # accounts (the 1200/min backstop is 20x the old bound).
                wait = self._hit(f"aiip:{ip}", now, self._limit, self._window)
            if wait:
                return self._limited(wait)
            return await call_next(request)

        if path.startswith(_SIGNED_DOWNLOAD_PREFIX) and "sig" in request.query_params:
            wait = self._hit(f"dl:{ip}", now, self._download_limit, self._window)
            if wait:
                return self._limited(wait)
            return await call_next(request)

        wait = self._hit(ip, now, self._limit, self._window)
        if wait:
            return self._limited(wait)
        if method == "POST" and path == _ANON_SCAN_PATH:
            wait = self._hit(f"anonscan:{ip}", now, self._anon_scan_limit, self._anon_scan_window)
            if wait:
                return self._limited(wait, anonymous_scan=True)
        return await call_next(request)


class CredentialThrottle:
    """Bound guessing against ONE identity, wherever it comes from.

    The per-IP limiter is blind to a botnet, to a shared NAT, and — behind a
    proxy with TRUST_PROXY_HEADERS off — to any difference between clients at
    all, because every request then keys on the proxy's own address. This
    limiter keys on the *account under attack* instead, so password guessing
    against one email is bounded no matter how the traffic is spread.

    Deliberately a cool-off, not a lockout. ``failures`` counted attempts inside
    ``window`` buy a ``cool_off`` pause; after it the counter keeps going, and
    :meth:`clear` (a *correct* password) ends it immediately. So the owner's
    worst case is a short wait, and the attacker's best case is ``failures``
    guesses per ``cool_off`` instead of unlimited. It also caps the scrypt work
    an unauthenticated caller can make the box do (N=2**15, ~32 MiB a try).

    Which attempts count is the caller's choice: sign-in counts only the wrong
    passwords, while the reset-email endpoint counts every request (the mail
    lands in someone else's inbox whether or not the address exists).
    """

    def __init__(
        self,
        *,
        failures: int = 10,
        window_seconds: float = 900.0,
        cool_off_seconds: float = 60.0,
    ) -> None:
        self._failures = int(failures)
        self._window = float(window_seconds)
        self._cool_off = float(cool_off_seconds)
        self._attempts: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> int:
        """Seconds the caller must wait, or 0 when this attempt may proceed."""
        if not key:
            return 0
        now = time.monotonic()
        with self._lock:
            bucket = self._attempts.get(key)
            if not bucket:
                return 0
            while bucket and bucket[0] < now - self._window:
                bucket.popleft()
            if len(bucket) < self._failures:
                return 0
            # Cool off measured from the most recent attempt, so a caller that
            # keeps hammering keeps waiting.
            remaining = self._cool_off - (now - bucket[-1])
            return max(1, int(remaining + 0.999)) if remaining > 0 else 0

    def record_attempt(self, key: str) -> None:
        if not key:
            return
        now = time.monotonic()
        with self._lock:
            if len(self._attempts) > _MAX_BUCKETS:
                self._attempts.clear()
            bucket = self._attempts.setdefault(key, deque())
            while bucket and bucket[0] < now - self._window:
                bucket.popleft()
            bucket.append(now)

    def clear(self, key: str) -> None:
        """Proving the credential ends the cool-off — the owner is not punished."""
        if not key:
            return
        with self._lock:
            self._attempts.pop(key, None)


__all__ = ["RateLimitMiddleware", "CredentialThrottle"]
