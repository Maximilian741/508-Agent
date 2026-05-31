"""Manual review queue routes."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.api.deps import require_admin, require_user_id
from app.db.models import UserRow
from app.persistence import audit_log as _audit
from app.persistence.db import get_repo

router = APIRouter()
REPO = get_repo()
logger = logging.getLogger(__name__)


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
    docId: str | None = None
    readyToFinalize: bool | None = None
    aiDecision: Dict[str, Any] | None = None
    aiConfidence: float | None = None
    aiStatus: str | None = None
    validatorStatus: str | None = None
    aiModel: str | None = None
    aiUpdatedAt: str | None = None
    aiSuggested: bool | None = None
    confidence: float | None = None
    requiresHuman: bool = True


@router.get("/manual-review", response_model=List[ManualReviewItem])
async def manual_review(_admin: UserRow = Depends(require_admin)) -> List[ManualReviewItem]:
    # The cross-tenant global queue is an admin/maintenance view. Normal users
    # read their own items via the owner-protected
    # GET /documents/{doc_id}/manual-review route.
    items = REPO.list_manual_review_items()
    logger.info("GET /manual-review count=%s", len(items))
    return [ManualReviewItem(**item) for item in items]


class ManualReviewClearResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cleared: int


class ManualReviewUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    approvedText: str | None = None


@router.delete("/manual-review", response_model=ManualReviewClearResponse)
async def clear_manual_review(
    _admin: UserRow = Depends(require_admin),
) -> ManualReviewClearResponse:
    # Clearing the entire global queue is a destructive cross-tenant action,
    # so it is admin-only. Per-item resolution uses PATCH /manual-review/{id}.
    cleared = REPO.clear_manual_review_items()
    logger.info("DELETE /manual-review cleared=%s", cleared)
    return ManualReviewClearResponse(cleared=cleared)


@router.patch("/manual-review/{item_id}", response_model=ManualReviewItem)
async def update_manual_review(
    item_id: str,
    body: ManualReviewUpdateRequest,
    request: Request,
    user_id: str = Depends(require_user_id),
) -> ManualReviewItem:
    current = REPO.get_manual_review_item(item_id)
    if not current:
        raise HTTPException(status_code=404, detail="Manual review item not found")
    # Tenant isolation: the item's document must belong to the caller.
    _doc_id = str(current.get("docId") or "")
    _doc = REPO.get_document(_doc_id) if _doc_id else None
    if _doc is None or (_doc.get("ownerId") or None) != user_id:
        raise HTTPException(status_code=404, detail="Manual review item not found")
    status = body.status.strip().lower()
    if status not in {"pending", "approved", "rejected"}:
        raise HTTPException(status_code=400, detail="status must be pending|approved|rejected")
    current["status"] = status
    if body.approvedText is not None:
        current["approvedText"] = body.approvedText
    resolved = status in {"approved", "rejected"}
    ok = REPO.update_manual_review_item(item_id, current, resolved=resolved)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update manual review item")
    doc_id = str(current.get("docId") or "").strip()
    if doc_id:
        pending = REPO.list_manual_review_items_for_doc(doc_id, include_resolved=False)
        all_items = REPO.list_manual_review_items_for_doc(doc_id, include_resolved=True)
        current["readyToFinalize"] = len(all_items) > 0 and len(pending) == 0

    # Audit log: record the resolution.  Never write the suggested or approved
    # text — just the metadata.
    try:
        ctx = _audit.context_from_request(request)
        _audit.record_event(
            event="manual_review_resolve",
            request_id=ctx.get("request_id"),
            actor_email=ctx.get("actor_email"),
            actor_sub=ctx.get("actor_sub"),
            ip=ctx.get("ip"),
            doc_id=doc_id or None,
            details={
                "itemId": item_id,
                "status": status,
                "resolved": bool(resolved),
            },
        )
    except Exception:
        pass

    return ManualReviewItem(**current)
