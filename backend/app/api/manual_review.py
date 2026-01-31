"""Manual review queue routes."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.api import state

router = APIRouter()


class ManualReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    issueId: str
    targetNodeId: str
    reason: str
    notes: str | None = None
    createdAt: str | None = None


@router.get("/manual-review", response_model=List[ManualReviewItem])
async def manual_review() -> List[ManualReviewItem]:
    print(f"[api] GET /manual-review count={len(state.manual_review_queue)}")
    return [ManualReviewItem(**item) for item in state.manual_review_queue]


class ManualReviewClearResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cleared: int


@router.delete("/manual-review", response_model=ManualReviewClearResponse)
async def clear_manual_review() -> ManualReviewClearResponse:
    cleared = len(state.manual_review_queue)
    state.manual_review_queue.clear()
    print(f"[api] DELETE /manual-review cleared={cleared}")
    return ManualReviewClearResponse(cleared=cleared)
