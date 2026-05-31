from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocumentRow(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # Owner (UserRow.id) for tenant isolation. Nullable for legacy rows.
    owner_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False, default="pdf")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_path: Mapped[str] = mapped_column(Text, nullable=False)
    fixed_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    rebuilt_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    tag_tree_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScanJobRow(Base):
    __tablename__ = "scan_jobs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class IssueRow(Base):
    __tablename__ = "issues"
    __table_args__ = (
        Index("idx_issues_doc_phase", "doc_id", "phase"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(String(128), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    issue_json: Mapped[str] = mapped_column(Text, nullable=False)
    issue_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class FixReportRow(Base):
    __tablename__ = "fix_reports"
    __table_args__ = (
        Index("idx_fix_reports_doc_id", "doc_id"),
    )

    doc_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    report_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class ManualReviewRow(Base):
    __tablename__ = "manual_review"
    __table_args__ = (
        Index("idx_manual_review_doc_id", "doc_id"),
    )

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(128), nullable=False)
    item_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ai_decision_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    validator_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ai_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PolicyPackRow(Base):
    __tablename__ = "policy_packs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    policy_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class JobPolicySnapshotRow(Base):
    __tablename__ = "job_policy_snapshot"

    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    policy_pack_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    policy_name: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class JobScoringRow(Base):
    __tablename__ = "job_scoring"
    __table_args__ = (
        UniqueConstraint("job_id", "pass_type", name="uq_job_scoring_job_pass"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    pass_type: Mapped[str] = mapped_column(String(32), nullable=False)
    score_total: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    counts_by_severity: Mapped[str] = mapped_column(Text, nullable=False)
    points_by_category: Mapped[str | None] = mapped_column(Text, nullable=True)
    coverage: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class EvidenceBundleRow(Base):
    __tablename__ = "evidence_bundles"
    __table_args__ = (
        Index("idx_evidence_bundles_doc_id", "doc_id"),
        Index("idx_evidence_bundles_job_id", "job_id"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(128), nullable=False)
    doc_id: Mapped[str] = mapped_column(String(128), nullable=False)
    bundle_path: Mapped[str] = mapped_column(Text, nullable=False)
    bundle_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    options_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    credits_balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Optional password hash in "salt:hash" hex format (scrypt). NULL means
    # legacy/passwordless user; sign-in still works for them by email alone.
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when the user has clicked the verify-email link.
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CreditLedgerRow(Base):
    __tablename__ = "credit_ledger"
    __table_args__ = (
        Index("idx_ledger_user_at", "user_id", "at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    related_doc_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class EmailVerifyTokenRow(Base):
    __tablename__ = "email_verify_tokens"
    __table_args__ = (
        Index("idx_email_verify_user", "user_id"),
    )

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class SubscriptionRow(Base):
    """A recurring Stripe subscription that grants a monthly credit allowance."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        Index("idx_subscriptions_user", "user_id"),
    )

    # Stripe subscription id (sub_...).
    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    plan: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # When on, exceeding the monthly allowance auto-charges an overage pack
    # instead of blocking the user.
    overage_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CertificateRow(Base):
    """An issued, third-party-verifiable accessibility conformance certificate."""

    __tablename__ = "certificates"
    __table_args__ = (
        Index("idx_certificates_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    conformance_claim: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fixed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    remaining_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    paid_with: Mapped[str] = mapped_column(String(32), nullable=False, default="credits")
    issued_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class TeamRow(Base):
    """A team that shares its owner's subscription benefit and credit wallet.

    The team's shared credit pool *is* the owner's ``UserRow.credits_balance``:
    a member's spends, overage top-ups, and free-certificate eligibility all
    resolve to the owner (the payer). This keeps the per-user credit core
    unchanged — only non-owner members are redirected to the owner's wallet.
    """

    __tablename__ = "teams"
    __table_args__ = (
        Index("idx_teams_owner", "owner_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    seat_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TeamMemberRow(Base):
    """Membership of a user in a team. A user belongs to at most one team."""

    __tablename__ = "team_members"
    __table_args__ = (
        # One team per user — makes credit-wallet resolution unambiguous.
        UniqueConstraint("user_id", name="uq_team_members_user"),
        Index("idx_team_members_team", "team_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class TeamInviteRow(Base):
    """A pending invitation to join a team, redeemable by token."""

    __tablename__ = "team_invites"
    __table_args__ = (
        Index("idx_team_invites_team", "team_id"),
        Index("idx_team_invites_email", "email"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    token: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    invited_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    accepted_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
