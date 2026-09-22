"""Monitored sites — schedule re-scans of a URL and get alerted on regressions.

Analyze-only like the rest of the scanning surface: monitors never remediate,
persist a document, or spend credits. The scheduled worker is in
:mod:`app.services.monitoring` (see it for the multi-worker lease).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import require_user_id
from app.security.url_fetch import SsrfError, UrlFetchError, validate_scan_target
from app.services.monitoring import (
    DEFAULT_FREQUENCY,
    FREQUENCY_INTERVALS,
    MAX_MONITORS_PER_USER,
    list_monitors,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/monitors")


class MonitorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)
    frequency: str = Field(default=DEFAULT_FREQUENCY)
    notifyEmail: str = Field(default="", max_length=320)


class MonitorUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: Optional[bool] = None
    frequency: Optional[str] = None
    notifyEmail: Optional[str] = None


class Monitor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    url: str
    frequency: str
    enabled: bool
    notifyEmail: str = ""
    lastRunAt: Optional[str] = None
    nextRunAt: Optional[str] = None
    lastIssueCount: int = 0
    lastStatus: str = ""


@router.get("", response_model=List[Monitor])
async def get_monitors(user_id: str = Depends(require_user_id)) -> List[Monitor]:
    return [Monitor(**m) for m in list_monitors(user_id)]


@router.post("", response_model=Monitor, status_code=201)
async def create_monitor(
    request: Request,
    payload: MonitorCreate,
    user_id: str = Depends(require_user_id),
) -> Monitor:
    """Start watching a URL.

    The URL goes through the SAME SSRF validation as an interactive scan at
    CREATE time, so an internal address can never be parked in the scheduler and
    fetched later by a background worker with no user watching. (It is validated
    again at fetch time — this is defence in depth plus an immediate error.)
    """
    freq = (payload.frequency or DEFAULT_FREQUENCY).lower()
    if freq not in FREQUENCY_INTERVALS:
        raise HTTPException(status_code=400, detail="Frequency must be 'daily' or 'weekly'.")

    try:
        # Full pre-flight (scheme AND resolved-IP), so an internal address can
        # never be parked in the scheduler for a background worker to fetch
        # later — and the user gets the error now instead of a monitor that
        # silently never works.
        url = validate_scan_target(payload.url)
    except SsrfError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except UrlFetchError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        from app.db.models import MonitoredSiteRow
        from app.db.session_sqlalchemy import session_scope

        now = datetime.utcnow()
        with session_scope() as session:
            existing = (
                session.query(MonitoredSiteRow)
                .filter(MonitoredSiteRow.user_id == user_id)
                .count()
            )
            if existing >= MAX_MONITORS_PER_USER:
                raise HTTPException(
                    status_code=400,
                    detail=f"You can monitor up to {MAX_MONITORS_PER_USER} pages.",
                )
            dupe = (
                session.query(MonitoredSiteRow)
                .filter(MonitoredSiteRow.user_id == user_id, MonitoredSiteRow.url == url)
                .first()
            )
            if dupe is not None:
                raise HTTPException(status_code=400, detail="You're already monitoring that URL.")
            row = MonitoredSiteRow(
                id=uuid.uuid4().hex,
                user_id=user_id,
                url=url,
                frequency=freq,
                enabled=True,
                notify_email=(payload.notifyEmail or "").strip()[:320],
                created_at=now,
                next_run_at=now,  # check promptly so the user sees it work
            )
            session.add(row)
            session.flush()
            created = {
                "id": row.id,
                "url": row.url,
                "frequency": row.frequency,
                "enabled": True,
                "notifyEmail": row.notify_email,
                "lastRunAt": None,
                "nextRunAt": row.next_run_at.isoformat(),
                "lastIssueCount": 0,
                "lastStatus": "",
            }
        return Monitor(**created)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("create_monitor failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not create that monitor.")


@router.patch("/{monitor_id}", response_model=Monitor)
async def update_monitor(
    monitor_id: str,
    payload: MonitorUpdate,
    user_id: str = Depends(require_user_id),
) -> Monitor:
    from app.db.models import MonitoredSiteRow
    from app.db.session_sqlalchemy import session_scope

    with session_scope() as session:
        row = session.get(MonitoredSiteRow, monitor_id)
        # 404 (not 403) on someone else's monitor so ids can't be enumerated.
        if row is None or row.user_id != user_id:
            raise HTTPException(status_code=404, detail="Monitor not found.")
        if payload.frequency is not None:
            freq = payload.frequency.lower()
            if freq not in FREQUENCY_INTERVALS:
                raise HTTPException(status_code=400, detail="Frequency must be 'daily' or 'weekly'.")
            row.frequency = freq
        if payload.enabled is not None:
            row.enabled = bool(payload.enabled)
            if row.enabled:
                row.consecutive_failures = 0  # re-enabling clears the failure streak
        if payload.notifyEmail is not None:
            row.notify_email = payload.notifyEmail.strip()[:320]
        updated = {
            "id": row.id,
            "url": row.url,
            "frequency": row.frequency,
            "enabled": bool(row.enabled),
            "notifyEmail": row.notify_email,
            "lastRunAt": row.last_run_at.isoformat() if row.last_run_at else None,
            "nextRunAt": row.next_run_at.isoformat() if row.next_run_at else None,
            "lastIssueCount": int(row.last_issue_count or 0),
            "lastStatus": row.last_status or "",
        }
    return Monitor(**updated)


@router.delete("/{monitor_id}", status_code=204, response_class=Response)
async def delete_monitor(monitor_id: str, user_id: str = Depends(require_user_id)) -> Response:
    from app.db.models import MonitoredSiteRow
    from app.db.session_sqlalchemy import session_scope

    with session_scope() as session:
        row = session.get(MonitoredSiteRow, monitor_id)
        if row is None or row.user_id != user_id:
            raise HTTPException(status_code=404, detail="Monitor not found.")
        session.delete(row)
    # 204 must carry no body — return the bare Response so FastAPI doesn't try
    # to build a response model for it.
    return Response(status_code=204)
