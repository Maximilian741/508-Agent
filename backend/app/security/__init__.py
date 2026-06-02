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
        _log.info(
            "[request] id=%s method=%s path=%s status=%s latency_ms=%.2f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
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


# Paths that must stay reachable even if a Cloudflare Access JWT is required.
# NOTE: the chosen product model is a PUBLIC self-serve SaaS, so CF Access should
# normally be OFF (cloudflare_access_aud empty). These bypasses are defense in
# depth for the endpoints that are public *by design* regardless: liveness +
# readiness probes (load balancer), the Stripe webhook (machine-to-machine, no
# JWT to present), and public certificate verification (links are shared).
_AUTH_BYPASS_PATHS = {"/healthz", "/readyz", "/billing/webhook"}
_AUTH_BYPASS_PREFIXES = ("/billing/certificate",)


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

        path = request.url.path
        if path in _AUTH_BYPASS_PATHS or path.startswith(_AUTH_BYPASS_PREFIXES):
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
