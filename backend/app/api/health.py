"""Health check routes."""

from fastapi import APIRouter

router = APIRouter()

@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}

@router.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
