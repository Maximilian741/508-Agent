"""Per-IP rate limiter middleware.

Currently in-memory; sufficient for single-instance deployments behind
Cloudflare. Per-IP rolling window of 60 requests per 60 seconds, applied to
sensitive/expensive paths (/auth/*, /credits/*, /pipeline/*, /documents/upload).

# TODO: swap for Redis in multi-instance deploys. The in-memory dict
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


_RATE_LIMITED_PREFIXES: Tuple[str, ...] = ("/auth", "/credits", "/pipeline")
_RATE_LIMITED_EXACT: frozenset = frozenset({"/documents/upload"})
_DEFAULT_LIMIT = 60
_DEFAULT_WINDOW_SECONDS = 60.0


def _is_rate_limited_path(path: str) -> bool:
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
        trust_proxy_headers: bool = True,
    ) -> None:
        super().__init__(app)
        self._limit = int(limit)
        self._window = float(window_seconds)
        self._trust_proxy = bool(trust_proxy_headers)
        self._buckets: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def _client_ip(self, request: Request) -> str:
        # Only trust forwarded-IP headers when configured to sit behind a
        # trusted proxy (Cloudflare); otherwise they are client-spoofable and
        # an attacker can rotate them to defeat the per-IP limit.
        if self._trust_proxy:
            cf_ip = request.headers.get("cf-connecting-ip")
            if cf_ip:
                return cf_ip.strip()
            xff = request.headers.get("x-forwarded-for")
            if xff:
                return xff.split(",")[0].strip()
        client = getattr(request, "client", None)
        return getattr(client, "host", "") or "unknown"

    def _check_and_record(self, ip: str, now: float) -> bool:
        cutoff = now - self._window
        with self._lock:
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


__all__ = ["RateLimitMiddleware"]
