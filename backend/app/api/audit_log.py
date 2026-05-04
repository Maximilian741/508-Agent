"""Audit log API.

Two endpoints:

* ``GET /audit-log`` — list recent entries with optional filters.  Open to
  any authenticated user when CF Access is enabled (so anyone can see their
  own activity), but the frontend only surfaces it to admins.  We do NOT
  filter to the requester's own email here — partly because in dev mode
  there's no user, partly because admins need cross-user visibility.  If
  per-user privacy becomes a requirement, gate this on ``settings.is_admin``
  the same way ``/audit-log/purge`` does.

* ``POST /audit-log/purge`` — DELETE entries older than ``days``.  Admin-only
  (``settings.admin_emails``).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
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


def _requester_email(request: Request) -> Optional[str]:
    try:
        user = getattr(request.state, "user", None) or {}
        if isinstance(user, dict):
            email = user.get("email")
            if isinstance(email, str):
                return email
    except Exception:
        return None
    return None


def _require_admin(request: Request) -> str:
    """Return the requester's email or raise 403.  In dev mode (no CF Access)
    we let the call through ONLY when ``settings.admin_emails`` is empty,
    which preserves the "anything goes locally" UX.
    """

    settings = get_settings()
    email = _requester_email(request)
    if not settings.admin_emails:
        # Dev mode — no admins configured, no enforcement.  Returning the
        # email (or "" in dev) lets us still log who triggered the purge.
        return email or ""
    if not settings.is_admin(email):
        raise HTTPException(status_code=403, detail="admin_only")
    return email or ""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/admin/whoami", response_model=WhoAmIResponse)
async def whoami(request: Request) -> WhoAmIResponse:
    """Tell the frontend whether to surface the Admin tab.

    Always 200; never throws — the absence of CF Access just yields
    ``isAdmin=False`` unless the email happens to match ``ADMIN_EMAILS``.
    """

    settings = get_settings()
    email = _requester_email(request)
    return WhoAmIResponse(email=email, isAdmin=settings.is_admin(email))


@router.get("/audit-log", response_model=List[AuditLogEntryModel])
async def get_audit_log(
    actor_email: Optional[str] = Query(default=None),
    event: Optional[str] = Query(default=None),
    since: Optional[str] = Query(default=None),
    until: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
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
) -> AuditLogPurgeResponse:
    actor = _require_admin(request)
    purged = _audit.purge_older_than(days)
    # Record the purge itself as an audit event.
    ctx = _audit.context_from_request(request)
    _audit.record_event(
        event="manual_review_resolve",  # closest existing event for now
        request_id=ctx.get("request_id"),
        actor_email=actor or ctx.get("actor_email"),
        actor_sub=ctx.get("actor_sub"),
        ip=ctx.get("ip"),
        details={"purgedCount": purged, "olderThanDays": days, "kind": "audit_log_purge"},
    )
    return AuditLogPurgeResponse(purged=purged, days=days)
