"""Per-user / per-team accessibility metrics dashboard.

A single ``GET /metrics/overview`` (authenticated) that aggregates the server's
own persisted analysis results (:class:`AnalysisResultRow`, written by
``/pipeline/analyze``) into headline KPIs, a weekly timeline, and breakdowns by
format and grade.

Why this is distinct from the existing surfaces:
  * ``/admin/metrics`` is admin-only and tracks revenue/users/certs, not a
    customer's own document accessibility.
  * the on-device ``ActivityDashboard`` reads ``localStorage`` — it is per
    browser, lost on cache-clear, and never team-wide. This endpoint is the
    server's source of truth: persistent, cross-device, and aggregated across
    the caller's whole team when they belong to one.

Read-only. It never writes, never charges, and only reports numbers the server
already measured and stored (honesty invariant preserved). Scoped to the
caller's own ``user_id``, or to every member of their team if they are in one —
a member can never see another tenant's data outside their team.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import require_user
from app.db.models import AnalysisResultRow, TeamMemberRow, TeamRow, UserRow
from app.db.session_sqlalchemy import session_scope

router = APIRouter(prefix="/metrics")

# How many trailing weeks the timeline spans (Monday-anchored buckets).
_TIMELINE_WEEKS = 12
_GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "F": 5}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class MetricsTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documentsAnalyzed: int = 0
    issuesFound: int = 0
    issuesAutoFixed: int = 0
    issuesPendingManual: int = 0
    avgScore: int = 0
    autoFixablePct: int = 0


class FormatBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: str
    documents: int
    avgScore: int


class GradeBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grade: str
    documents: int


class TimelinePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weekStart: str
    documents: int
    avgScore: int
    issuesFound: int
    issuesAutoFixed: int


class RecentDocDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documentId: str
    filename: str
    format: str
    score: int
    grade: str
    issuesFound: int
    fixedAutomatically: int
    analyzedAt: str


class MetricsScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str  # "self" | "team"
    teamName: Optional[str] = None
    memberCount: int = 1


class MetricsOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    totals: MetricsTotals
    byFormat: List[FormatBreakdown] = Field(default_factory=list)
    byGrade: List[GradeBreakdown] = Field(default_factory=list)
    timeline: List[TimelinePoint] = Field(default_factory=list)
    recentDocuments: List[RecentDocDTO] = Field(default_factory=list)
    scope: MetricsScope
    generatedAt: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scope_user_ids(session, user_id: str) -> tuple[List[str], MetricsScope]:
    """Return the set of user_ids to aggregate and a scope descriptor.

    Solo users → just themselves. A team member → every member of their team
    (plus the team owner), so the dashboard is the whole team's accessibility
    posture. Defensive: any lookup failure degrades to per-user.
    """
    try:
        member = session.execute(
            select(TeamMemberRow).where(TeamMemberRow.user_id == user_id)
        ).scalar_one_or_none()
        if member is None:
            return [user_id], MetricsScope(kind="self", memberCount=1)
        team = session.execute(
            select(TeamRow).where(TeamRow.id == member.team_id)
        ).scalar_one_or_none()
        member_ids = [
            m.user_id
            for m in session.execute(
                select(TeamMemberRow).where(TeamMemberRow.team_id == member.team_id)
            ).scalars().all()
        ]
        if team and team.owner_id and team.owner_id not in member_ids:
            member_ids.append(team.owner_id)
        member_ids = sorted(set(member_ids)) or [user_id]
        return member_ids, MetricsScope(
            kind="team",
            teamName=(team.name if team else None),
            memberCount=len(member_ids),
        )
    except Exception:  # pragma: no cover - defensive: never block the dashboard
        return [user_id], MetricsScope(kind="self", memberCount=1)


def _week_start(d: datetime) -> date:
    """Monday of the week containing ``d`` (date only)."""
    dd = d.date()
    return dd - timedelta(days=dd.weekday())


def _avg(values: List[int]) -> int:
    return round(sum(values) / len(values)) if values else 0


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get("/overview", response_model=MetricsOverviewResponse)
async def metrics_overview(user: UserRow = Depends(require_user)) -> MetricsOverviewResponse:
    now = datetime.utcnow()
    with session_scope() as session:
        user_ids, scope = _scope_user_ids(session, user.id)
        rows = list(
            session.execute(
                select(AnalysisResultRow).where(AnalysisResultRow.user_id.in_(user_ids))
            ).scalars().all()
        )

        # --- headline totals ---
        n = len(rows)
        issues_found = sum(int(r.initial_issues or 0) for r in rows)
        auto_fixed = sum(int(r.fixed_automatically or 0) for r in rows)
        pending = sum(int(r.pending_manual or 0) for r in rows)
        totals = MetricsTotals(
            documentsAnalyzed=n,
            issuesFound=issues_found,
            issuesAutoFixed=auto_fixed,
            issuesPendingManual=pending,
            avgScore=_avg([int(r.score or 0) for r in rows]),
            autoFixablePct=(round(100 * auto_fixed / issues_found) if issues_found else 0),
        )

        # --- by format ---
        fmt_scores: Dict[str, List[int]] = defaultdict(list)
        for r in rows:
            key = (r.source_format or "").lower() or "other"
            fmt_scores[key].append(int(r.score or 0))
        by_format = sorted(
            [
                FormatBreakdown(format=k, documents=len(v), avgScore=_avg(v))
                for k, v in fmt_scores.items()
            ],
            key=lambda x: (-x.documents, x.format),
        )

        # --- by grade ---
        grade_counts: Dict[str, int] = defaultdict(int)
        for r in rows:
            grade_counts[(r.grade or "?").upper() or "?"] += 1
        by_grade = [
            GradeBreakdown(grade=g, documents=c)
            for g, c in sorted(grade_counts.items(), key=lambda kv: _GRADE_ORDER.get(kv[0], 99))
        ]

        # --- weekly timeline (last _TIMELINE_WEEKS weeks, zero-filled) ---
        first_monday = _week_start(now) - timedelta(weeks=_TIMELINE_WEEKS - 1)
        buckets: Dict[date, Dict[str, int]] = defaultdict(
            lambda: {"docs": 0, "score": 0, "found": 0, "fixed": 0}
        )
        for r in rows:
            ts = r.created_at or now
            ws = _week_start(ts)
            if ws < first_monday:
                continue
            b = buckets[ws]
            b["docs"] += 1
            b["score"] += int(r.score or 0)
            b["found"] += int(r.initial_issues or 0)
            b["fixed"] += int(r.fixed_automatically or 0)
        timeline: List[TimelinePoint] = []
        for i in range(_TIMELINE_WEEKS):
            ws = first_monday + timedelta(weeks=i)
            b = buckets.get(ws)
            docs = b["docs"] if b else 0
            timeline.append(
                TimelinePoint(
                    weekStart=ws.isoformat(),
                    documents=docs,
                    avgScore=(round(b["score"] / docs) if docs else 0),
                    issuesFound=(b["found"] if b else 0),
                    issuesAutoFixed=(b["fixed"] if b else 0),
                )
            )

        # --- recent documents ---
        recent_sorted = sorted(
            rows, key=lambda r: (r.updated_at or r.created_at or now), reverse=True
        )[:10]
        recent = [
            RecentDocDTO(
                documentId=str(r.document_id),
                filename=r.filename or r.document_id,
                format=(r.source_format or "").upper(),
                score=int(r.score or 0),
                grade=r.grade or "",
                issuesFound=int(r.initial_issues or 0),
                fixedAutomatically=int(r.fixed_automatically or 0),
                analyzedAt=((r.updated_at or r.created_at).isoformat() if (r.updated_at or r.created_at) else ""),
            )
            for r in recent_sorted
        ]

        return MetricsOverviewResponse(
            totals=totals,
            byFormat=by_format,
            byGrade=by_grade,
            timeline=timeline,
            recentDocuments=recent,
            scope=scope,
            generatedAt=now.isoformat(),
        )


__all__ = ["router"]
