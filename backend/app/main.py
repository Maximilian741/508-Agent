"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.manual_review import router as manual_review_router
from app.api.remediate import router as remediate_router
from app.api.scan import router as scan_router

app = FastAPI(title="508-Agent", version="0.1.0")

app.include_router(health_router, tags=["health"])
app.include_router(scan_router, tags=["scan"])
app.include_router(remediate_router, tags=["remediate"])
app.include_router(manual_review_router, tags=["manual-review"])
