"""Audit log API (admin-only).

* ``GET /audit-log`` — list recent entries with optional filters. Returns
  cross-user activity, so it is gated on admin (``require_admin``).
* ``POST /audit-log/purge`` — DELETE entries older than ``days``. Admin-only.
* ``GET /api/admin/whoami`` — tells the frontend whether to show the Admin tab
  for the current session user (never throws).

Admin = the authenticated session user whose ``role == 'admin'`` or whose email
is in ``ADMIN_EMAILS``. Authorization fails closed when neither holds.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import optional_user, require_admin
from app.config import get_settings
from app.db.models import UserRow
from app.persistence import audit_log as _audit

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AuditLogEntryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    at: str
    requestId: Optional[str] = None
    actorEmail: Optional[str] = None
    actorSub: Optional[str] = None
    ip: Optional[str] = None
    event: str
    docId: Optional[str] = None
    jobId: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class AuditLogPurgeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purged: int
    days: int


class WhoAmIResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Optional[str] = None
    isAdmin: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/admin/whoami", response_model=WhoAmIResponse)
async def whoami(user: Optional[UserRow] = Depends(optional_user)) -> WhoAmIResponse:
    """Tell the frontend whether to surface the Admin tab.

    Always 200; never throws. Anonymous or non-admin session users get
    ``isAdmin=False``.
    """
    if user is None:
        return WhoAmIResponse(email=None, isAdmin=False)
    settings = get_settings()
    is_admin = (user.role == "admin") or settings.is_admin(user.email)
    return WhoAmIResponse(email=user.email, isAdmin=is_admin)


@router.get("/audit-log", response_model=List[AuditLogEntryModel])
async def get_audit_log(
    actor_email: Optional[str] = Query(default=None),
    event: Optional[str] = Query(default=None),
    since: Optional[str] = Query(default=None),
    until: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    _admin: UserRow = Depends(require_admin),
) -> List[AuditLogEntryModel]:
    entries = _audit.list_events(
        filters={
            "actor_email": actor_email,
            "event": event,
            "since": since,
            "until": until,
        },
        limit=limit,
    )
    return [AuditLogEntryModel(**e.to_dict()) for e in entries]


@router.post("/audit-log/purge", response_model=AuditLogPurgeResponse)
async def post_audit_log_purge(
    request: Request,
    days: int = Query(default=90, ge=1, le=3650),
    admin: UserRow = Depends(require_admin),
) -> AuditLogPurgeResponse:
    purged = _audit.purge_older_than(days)
    # Record the purge itself as an audit event.
    ctx = _audit.context_from_request(request)
    _audit.record_event(
        event="manual_review_resolve",  # closest existing event for now
        request_id=ctx.get("request_id"),
        actor_email=admin.email or ctx.get("actor_email"),
        actor_sub=ctx.get("actor_sub"),
        ip=ctx.get("ip"),
        details={"purgedCount": purged, "olderThanDays": days, "kind": "audit_log_purge"},
    )
    return AuditLogPurgeResponse(purged=purged, days=days)
