"""FastAPI application entrypoint."""

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.admin_metrics import router as admin_metrics_router
from app.api.api_keys import router as api_keys_router
from app.api.audit_log import router as audit_log_router
from app.api.auth import router as auth_router
from app.api.credits import router as credits_router
from app.api.stripe_billing import router as stripe_billing_router
from app.api.teams import router as teams_router
from app.api.health import router as health_router
from app.api.documents import router as documents_router
from app.api.evidence_bundles import router as evidence_bundles_router
from app.api.manual_review import router as manual_review_router
from app.api.pipeline import router as pipeline_router
from app.api.policies import router as policies_router
from app.api.remediate import router as remediate_router
from app.api.scan import router as scan_router
from app.config import get_settings
from app.persistence.db import init_db
# Importing app.db.models registers every ORM table on the shared Base
# metadata so Base.metadata.create_all below actually creates them.
from app.db import models as _orm_models  # noqa: F401
from app.db.base import Base
from app.db.session_sqlalchemy import ENGINE
from app.security import (
    CFAccessAuthMiddleware,
    RequestIdLoggingMiddleware,
    SecurityHeadersMiddleware,
)
from app.security.rate_limit import RateLimitMiddleware
from app.storage.router import router as storage_router
from app.tasks.cleanup import schedule_cleanup_task

_log = logging.getLogger(__name__)

settings = get_settings()
app = FastAPI(title="508-Agent", version=settings.app_version)
# init_db is idempotent and safe in all environments: in dev (sqlite) it
# creates the raw tables and seeds default policy packs; in production
# (Postgres) it runs Alembic migrations and seeds policy packs.
init_db()
# create_all is DEV-ONLY (sqlite). It adds the ORM-only tables (users,
# credit_ledger, email_verify_tokens) that init_db's raw path does not create.
# Anywhere else (staging/prod on Postgres) Alembic owns the schema — auto-
# creating would mask drift between the models and the migrations.
if settings.environment == "development":
    Base.metadata.create_all(bind=ENGINE)

app.add_middleware(RequestIdLoggingMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware, trust_proxy_headers=settings.trust_proxy_headers)
app.add_middleware(
    CFAccessAuthMiddleware,
    expected_aud=settings.cloudflare_access_aud,
    team_domain=settings.cloudflare_access_team_domain,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a generic 500 (plus request id) for any unhandled error so
    internal exception text never reaches the client."""
    request_id = getattr(request.state, "request_id", None)
    _log.exception("[error] unhandled exception id=%s path=%s", request_id, request.url.path)
    # This handler runs in Starlette's ServerErrorMiddleware, OUTSIDE the
    # BaseHTTPMiddleware stack, so set the baseline security headers here too.
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
    }
    if request_id:
        headers["X-Request-Id"] = str(request_id)
    return JSONResponse(
        status_code=500,
        content={"detail": "internal_error", "requestId": request_id},
        headers=headers,
    )


@app.on_event("startup")
async def _on_startup() -> None:
    if settings.cloudflare_access_aud:
        _log.info(
            "[auth] CF Access enforcement is ON for aud=%s",
            settings.cloudflare_access_aud,
        )
    else:
        _log.info("[auth] CF Access enforcement is OFF (dev mode)")
    schedule_cleanup_task()


app.include_router(health_router, tags=["health"])
app.include_router(auth_router, tags=["auth"])
app.include_router(credits_router, tags=["credits"])
app.include_router(stripe_billing_router, tags=["billing"])
app.include_router(teams_router, tags=["teams"])
app.include_router(admin_metrics_router, tags=["admin"])
app.include_router(api_keys_router, tags=["api-keys"])
app.include_router(documents_router, tags=["documents"])
app.include_router(scan_router, tags=["scan"])
app.include_router(remediate_router, tags=["remediate"])
app.include_router(manual_review_router, tags=["manual-review"])
app.include_router(policies_router, tags=["policies"])
app.include_router(evidence_bundles_router, tags=["evidence-bundles"])
app.include_router(storage_router, tags=["storage"])
app.include_router(pipeline_router, tags=["pipeline"])
app.include_router(audit_log_router, tags=["audit-log"])
