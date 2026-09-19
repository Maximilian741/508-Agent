"""Rate limiting: a per-IP middleware plus a per-identity credential throttle.

Currently in-memory; sufficient for single-instance deployments behind
Cloudflare. Per-IP rolling window of 60 requests per 60 seconds, applied to
sensitive/expensive paths (/auth/*, /credits/*, /pipeline/*, /documents/upload).

Deriving "the IP" is the whole security of the per-IP half: every forwarded-IP
header is written by whoever is talking to us, so trusting one that an attacker
can set turns the limiter off (rotate the header, get a fresh bucket per
request). See :meth:`RateLimitMiddleware._client_ip` for the rule.

The per-IP limiter also cannot see a botnet, a NAT, or — behind a proxy with
TRUST_PROXY_HEADERS off — any distinction between clients at all, so
:class:`CredentialThrottle` bounds guessing against a single *account*
independently of where the requests come from.

# TODO: swap for Redis in multi-instance deploys. The in-memory dicts
# below will allow N x limit total requests across N replicas, which
# defeats the purpose under horizontal scale. A Redis INCR + EXPIRE
# (or a token-bucket Lua script) is the standard fix.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, Tuple

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


def _is_rate_limited_path(path: str) -> bool:
    if path in _RATE_LIMIT_EXEMPT_EXACT:
        return False
    if path in _RATE_LIMITED_EXACT:
        return True
    for prefix in _RATE_LIMITED_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window per-IP rate limiter for sensitive endpoints."""

    def __init__(
        self,
        app,
        *,
        limit: int = _DEFAULT_LIMIT,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
        trust_proxy_headers: bool = False,
    ) -> None:
        super().__init__(app)
        self._limit = int(limit)
        self._window = float(window_seconds)
        self._trust_proxy = bool(trust_proxy_headers)
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

    def _check_and_record(self, ip: str, now: float) -> bool:
        cutoff = now - self._window
        with self._lock:
            if len(self._buckets) > _MAX_BUCKETS:
                self._buckets.clear()
            bucket = self._buckets.get(ip)
            if bucket is None:
                bucket = deque()
                self._buckets[ip] = bucket
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._limit:
                return False
            bucket.append(now)
            return True

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not _is_rate_limited_path(request.url.path):
            return await call_next(request)

        ip = self._client_ip(request)
        now = time.monotonic()
        if not self._check_and_record(ip, now):
            return JSONResponse(
                status_code=429,
                content={"detail": "rate_limited"},
                headers={"Retry-After": str(int(self._window))},
            )
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
