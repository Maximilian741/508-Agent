"""FastAPI application entrypoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
from app.security import RequestIdLoggingMiddleware, SecurityHeadersMiddleware
from app.storage.router import router as storage_router

settings = get_settings()
app = FastAPI(title="508-Agent", version=settings.app_version)
init_db()

app.add_middleware(RequestIdLoggingMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, tags=["health"])
app.include_router(documents_router, tags=["documents"])
app.include_router(scan_router, tags=["scan"])
app.include_router(remediate_router, tags=["remediate"])
app.include_router(manual_review_router, tags=["manual-review"])
app.include_router(policies_router, tags=["policies"])
app.include_router(evidence_bundles_router, tags=["evidence-bundles"])
app.include_router(storage_router, tags=["storage"])
app.include_router(pipeline_router, tags=["pipeline"])
