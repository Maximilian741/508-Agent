"""Admin dashboard metrics.

A single ``GET /admin/metrics`` endpoint (admins only) that aggregates revenue,
usage, and certificate-issuance signals from the existing tables. Read-only.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.api.deps import require_admin
from app.api.stripe_billing import _OVERAGE_PRICE_CENTS, plan_price_usd
from app.db.models import (
    CertificateRow,
    CreditLedgerRow,
    SubscriptionRow,
    TeamMemberRow,
    TeamRow,
    UserRow,
)
from app.db.session_sqlalchemy import session_scope

router = APIRouter(prefix="/admin")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class UsersMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: int = 0
    verified: int = 0
    newLast30d: int = 0


class SubscriptionsMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: int = 0
    byPlan: Dict[str, int] = Field(default_factory=dict)
    estimatedMrrUsd: int = 0


class CreditsMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    granted: int = 0
    spent: int = 0
    outstanding: int = 0


class CertificatesMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: int = 0
    last30d: int = 0
    bySubscription: int = 0
    byCredits: int = 0


class TeamsMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = 0
    seatsTotal: int = 0
    seatsUsed: int = 0


class OverageMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    charges: int = 0
    revenueUsd: int = 0


class RecentCertificateDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    issuedTo: Optional[str] = None
    filename: str
    paidWith: str
    issuedAt: str


class RecentSubscriptionDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: str
    status: str
    createdAt: str


class DeploymentMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment: str = "development"
    appVersion: str = ""
    # OCR for scanned PDFs: enabled = the env flag; available = the flag AND
    # a working Tesseract install (what remediation will actually do).
    ocrEnabled: bool = False
    ocrAvailable: bool = False


class AdminMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    users: UsersMetrics
    subscriptions: SubscriptionsMetrics
    credits: CreditsMetrics
    certificates: CertificatesMetrics
    teams: TeamsMetrics
    overage: OverageMetrics
    deployment: DeploymentMetrics = Field(default_factory=DeploymentMetrics)
    recentCertificates: List[RecentCertificateDTO] = Field(default_factory=list)
    recentSubscriptions: List[RecentSubscriptionDTO] = Field(default_factory=list)
    generatedAt: str


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


def _count(session, stmt) -> int:
    return int(session.execute(stmt).scalar_one() or 0)


@router.get("/metrics", response_model=AdminMetricsResponse)
async def admin_metrics(_: UserRow = Depends(require_admin)) -> AdminMetricsResponse:
    now = datetime.utcnow()
    cutoff = now - timedelta(days=30)

    with session_scope() as session:
        # Users
        users = UsersMetrics(
            total=_count(session, select(func.count()).select_from(UserRow)),
            verified=_count(
                session,
                select(func.count()).select_from(UserRow).where(UserRow.email_verified_at.is_not(None)),
            ),
            newLast30d=_count(
                session,
                select(func.count()).select_from(UserRow).where(UserRow.created_at >= cutoff),
            ),
        )

        # Subscriptions (active) + MRR estimate
        active_subs = session.execute(
            select(SubscriptionRow).where(SubscriptionRow.status == "active")
        ).scalars().all()
        by_plan: Dict[str, int] = {}
        mrr = 0
        for s in active_subs:
            by_plan[s.plan] = by_plan.get(s.plan, 0) + 1
            mrr += plan_price_usd(s.plan)
        subs = SubscriptionsMetrics(active=len(active_subs), byPlan=by_plan, estimatedMrrUsd=mrr)

        # Credits: granted (positive ledger), spent (abs of negative), outstanding (sum balances)
        granted = int(
            session.execute(
                select(func.coalesce(func.sum(CreditLedgerRow.amount), 0)).where(CreditLedgerRow.amount > 0)
            ).scalar_one()
            or 0
        )
        spent_neg = int(
            session.execute(
                select(func.coalesce(func.sum(CreditLedgerRow.amount), 0)).where(CreditLedgerRow.amount < 0)
            ).scalar_one()
            or 0
        )
        outstanding = int(
            session.execute(
                select(func.coalesce(func.sum(UserRow.credits_balance), 0))
            ).scalar_one()
            or 0
        )
        credits = CreditsMetrics(granted=granted, spent=abs(spent_neg), outstanding=outstanding)

        # Certificates
        certs = CertificatesMetrics(
            total=_count(session, select(func.count()).select_from(CertificateRow)),
            last30d=_count(
                session,
                select(func.count()).select_from(CertificateRow).where(CertificateRow.issued_at >= cutoff),
            ),
            bySubscription=_count(
                session,
                select(func.count()).select_from(CertificateRow).where(CertificateRow.paid_with == "subscription"),
            ),
            byCredits=_count(
                session,
                select(func.count()).select_from(CertificateRow).where(CertificateRow.paid_with == "credits"),
            ),
        )

        # Teams
        team_rows = session.execute(select(TeamRow)).scalars().all()
        seats_total = sum(int(t.seat_limit) for t in team_rows)
        seats_used = _count(session, select(func.count()).select_from(TeamMemberRow))
        teams = TeamsMetrics(count=len(team_rows), seatsTotal=seats_total, seatsUsed=seats_used)

        # Overage (one ledger entry of kind="overage" per off-session charge)
        overage_charges = _count(
            session,
            select(func.count()).select_from(CreditLedgerRow).where(CreditLedgerRow.kind == "overage"),
        )
        overage = OverageMetrics(
            charges=overage_charges,
            revenueUsd=int(overage_charges * (_OVERAGE_PRICE_CENTS // 100)),
        )

        # Recent activity
        recent_cert_rows = session.execute(
            select(CertificateRow).order_by(CertificateRow.issued_at.desc()).limit(10)
        ).scalars().all()
        recent_certificates = [
            RecentCertificateDTO(
                id=c.id,
                issuedTo=c.issued_email,
                filename=c.filename,
                paidWith=c.paid_with,
                issuedAt=(c.issued_at.isoformat() if c.issued_at else ""),
            )
            for c in recent_cert_rows
        ]
        recent_sub_rows = session.execute(
            select(SubscriptionRow).order_by(SubscriptionRow.created_at.desc()).limit(10)
        ).scalars().all()
        recent_subscriptions = [
            RecentSubscriptionDTO(
                plan=s.plan,
                status=s.status,
                createdAt=(s.created_at.isoformat() if s.created_at else ""),
            )
            for s in recent_sub_rows
        ]

        from app.config import get_settings as _gs
        from app.services.ocr import get_ocr_provider as _gop

        _settings = _gs()
        deployment = DeploymentMetrics(
            environment=_settings.environment,
            appVersion=_settings.app_version,
            ocrEnabled=bool(getattr(_settings, "ocr_enabled", False)),
            ocrAvailable=_gop() is not None,
        )
        return AdminMetricsResponse(
            users=users,
            subscriptions=subs,
            credits=credits,
            certificates=certs,
            teams=teams,
            overage=overage,
            deployment=deployment,
            recentCertificates=recent_certificates,
            recentSubscriptions=recent_subscriptions,
            generatedAt=now.isoformat(),
        )


__all__ = ["router"]
