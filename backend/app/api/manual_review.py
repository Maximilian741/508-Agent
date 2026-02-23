"""Manual review queue routes."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from app.api import state
from app.repositories.factory import get_repository

router = APIRouter()
REPO = get_repository()


class ManualReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    issueId: str
    targetNodeId: str
    reason: str
    notes: str | None = None
    createdAt: str | None = None
    pages: list[int] = []
    anchors: list[Any] = []
    instructions: str | None = None
    suggestedFix: str | None = None
    suggestedText: str | None = None
    approvedText: str | None = None
    status: str | None = None
    aiSuggested: bool | None = None
    confidence: float | None = None
    requiresHuman: bool = True


@router.get("/manual-review", response_model=List[ManualReviewItem])
async def manual_review() -> List[ManualReviewItem]:
    items = REPO.list_manual_review_items()
    state.manual_review_queue = list(items)
    print(f"[api] GET /manual-review count={len(items)}")
    return [ManualReviewItem(**item) for item in items]


class ManualReviewClearResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cleared: int


class ManualReviewUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    approvedText: str | None = None


@router.delete("/manual-review", response_model=ManualReviewClearResponse)
async def clear_manual_review() -> ManualReviewClearResponse:
    cleared = REPO.clear_manual_review_items()
    state.manual_review_queue.clear()
    print(f"[api] DELETE /manual-review cleared={cleared}")
    return ManualReviewClearResponse(cleared=cleared)


@router.patch("/manual-review/{item_id}", response_model=ManualReviewItem)
async def update_manual_review(item_id: str, request: ManualReviewUpdateRequest) -> ManualReviewItem:
    current = REPO.get_manual_review_item(item_id)
    if not current:
        raise HTTPException(status_code=404, detail="Manual review item not found")
    status = request.status.strip().lower()
    if status not in {"pending", "approved", "rejected"}:
        raise HTTPException(status_code=400, detail="status must be pending|approved|rejected")
    current["status"] = status
    if request.approvedText is not None:
        current["approvedText"] = request.approvedText
    resolved = status in {"approved", "rejected"}
    ok = REPO.update_manual_review_item(item_id, current, resolved=resolved)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update manual review item")
    return ManualReviewItem(**current)
