"""Security primitives package.

Originally a single security.py module; converted to a package so we can
add focused submodules (uploads, signing, cf_access) without one giant file.
The previous import surface (RequestIdLoggingMiddleware and
SecurityHeadersMiddleware) is preserved via re-export.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_log = logging.getLogger(__name__)


class RequestIdLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        response.headers["X-Request-Id"] = request_id
        print(
            f"[request] id={request_id} method={request.method} path={request.url.path} status={response.status_code} latency_ms={elapsed_ms:.2f}"
        )
        return response


_DEFAULT_CSP = (
    "default-src 'self'; "
    "img-src 'self' data: blob:; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Tightened security headers.

    Headers are applied to every response. The CSP is intentionally strict
    (no inline scripts) because the served HTML bundle is built with hashed
    asset URLs. Adjust via subclass or patched env var if a downstream site
    embeds 508-Agent in an iframe (currently disallowed).
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Content-Security-Policy"] = _DEFAULT_CSP
        response.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains; preload"
        )
        return response


_AUTH_BYPASS_PATHS = {"/healthz"}


class CFAccessAuthMiddleware(BaseHTTPMiddleware):
    """Enforce a valid Cloudflare Access JWT on every request.

    No-op when settings.cloudflare_access_aud is empty (dev mode).
    On success, attaches request.state.user = {"sub": ..., "email": ...}.
    """

    def __init__(self, app, *, expected_aud: str, team_domain: str) -> None:
        super().__init__(app)
        self._aud = expected_aud
        self._team_domain = team_domain

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self._aud:
            return await call_next(request)

        if request.url.path in _AUTH_BYPASS_PATHS:
            return await call_next(request)

        token = request.headers.get("Cf-Access-Jwt-Assertion") or request.headers.get(
            "cf-access-jwt-assertion"
        )
        if not token:
            self._record_auth_failure(request, reason="missing_cf_access_token")
            return JSONResponse(
                status_code=401,
                content={"detail": "missing_cf_access_token"},
            )

        from app.security.cf_access import (
            verify_cf_access_jwt,
            CFAccessVerificationError,
        )

        try:
            claims = verify_cf_access_jwt(
                token=token,
                expected_aud=self._aud,
                team_domain=self._team_domain,
            )
        except CFAccessVerificationError as exc:
            _log.warning(
                "[auth] CF Access rejection on %s: %s",
                request.url.path,
                exc,
            )
            self._record_auth_failure(request, reason=f"cf_access_invalid: {exc}")
            return JSONResponse(
                status_code=401,
                content={"detail": f"cf_access_invalid: {exc}"},
            )

        request.state.user = {
            "sub": claims.get("sub"),
            "email": claims.get("email"),
            "claims": claims,
        }
        return await call_next(request)

    @staticmethod
    def _record_auth_failure(request: Request, *, reason: str) -> None:
        """Best-effort audit_log write on JWT rejection. Never raises -
        this runs from inside a middleware that returned the user a 401.
        """

        try:
            request_id = getattr(request.state, "request_id", None)
            client = getattr(request, "client", None)
            ip = getattr(client, "host", None) if client is not None else None
            from app.persistence import audit_log as _audit

            _audit.record_event(
                event="auth_fail",
                request_id=request_id,
                ip=ip,
                details={
                    "reason": reason,
                    "path": str(request.url.path),
                    "method": request.method,
                },
            )
        except Exception:
            pass


__all__ = [
    "RequestIdLoggingMiddleware",
    "SecurityHeadersMiddleware",
    "CFAccessAuthMiddleware",
]
