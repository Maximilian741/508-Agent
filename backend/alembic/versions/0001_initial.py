"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-02-25
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "documents" not in inspector.get_table_names():
        op.create_table(
            "documents",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("filename", sa.Text()),
            sa.Column("doc_type", sa.Text()),
            sa.Column("created_at", sa.Text()),
            sa.Column("updated_at", sa.Text()),
            sa.Column("status", sa.Text()),
            sa.Column("original_path", sa.Text()),
            sa.Column("fixed_path", sa.Text()),
            sa.Column("rebuilt_path", sa.Text()),
            sa.Column("tag_tree_path", sa.Text()),
            sa.Column("extra_json", sa.Text()),
        )
    if "issues" not in inspector.get_table_names():
        op.create_table(
            "issues",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("doc_id", sa.Text()),
            sa.Column("phase", sa.Text()),
            sa.Column("issue_json", sa.Text()),
            sa.Column("issue_key", sa.Text()),
            sa.Column("created_at", sa.Text()),
        )
    if "fix_reports" not in inspector.get_table_names():
        op.create_table(
            "fix_reports",
            sa.Column("doc_id", sa.Text(), primary_key=True),
            sa.Column("report_json", sa.Text()),
            sa.Column("created_at", sa.Text()),
        )
    if "manual_review" not in inspector.get_table_names():
        op.create_table(
            "manual_review",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("doc_id", sa.Text()),
            sa.Column("item_json", sa.Text()),
            sa.Column("created_at", sa.Text()),
            sa.Column("resolved", sa.Integer(), server_default="0"),
        )
    if "scan_jobs" not in inspector.get_table_names():
        op.create_table(
            "scan_jobs",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("doc_id", sa.Text()),
            sa.Column("status", sa.Text()),
            sa.Column("progress", sa.Float()),
            sa.Column("message", sa.Text()),
            sa.Column("started_at", sa.Text()),
            sa.Column("finished_at", sa.Text()),
        )
    if "policy_packs" not in inspector.get_table_names():
        op.create_table(
            "policy_packs",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("description", sa.Text()),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("is_active", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("policy_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
        )
    if "job_policy_snapshot" not in inspector.get_table_names():
        op.create_table(
            "job_policy_snapshot",
            sa.Column("job_id", sa.Text(), primary_key=True),
            sa.Column("policy_pack_id", sa.Text()),
            sa.Column("policy_name", sa.Text(), nullable=False),
            sa.Column("policy_version", sa.Integer(), nullable=False),
            sa.Column("policy_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
        )
    if "job_scoring" not in inspector.get_table_names():
        op.create_table(
            "job_scoring",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("job_id", sa.Text(), nullable=False),
            sa.Column("pass_type", sa.Text(), nullable=False),
            sa.Column("score_total", sa.Integer(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("counts_by_severity", sa.Text(), nullable=False),
            sa.Column("points_by_category", sa.Text()),
            sa.Column("coverage", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.UniqueConstraint("job_id", "pass_type", name="uq_job_scoring_job_pass"),
        )
    if "evidence_bundles" not in inspector.get_table_names():
        op.create_table(
            "evidence_bundles",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("job_id", sa.Text(), nullable=False),
            sa.Column("doc_id", sa.Text(), nullable=False),
            sa.Column("bundle_path", sa.Text(), nullable=False),
            sa.Column("bundle_hash", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("created_by", sa.Text()),
            sa.Column("options_json", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("error_text", sa.Text()),
        )

    existing_indexes = {row.get("name") for row in inspector.get_indexes("scan_jobs")} if "scan_jobs" in inspector.get_table_names() else set()
    if "idx_scan_jobs_doc_id" not in existing_indexes:
        op.create_index("idx_scan_jobs_doc_id", "scan_jobs", ["doc_id"])

    existing_indexes = {row.get("name") for row in inspector.get_indexes("fix_reports")} if "fix_reports" in inspector.get_table_names() else set()
    if "idx_fix_reports_doc_id" not in existing_indexes:
        op.create_index("idx_fix_reports_doc_id", "fix_reports", ["doc_id"])

    existing_indexes = {row.get("name") for row in inspector.get_indexes("manual_review")} if "manual_review" in inspector.get_table_names() else set()
    if "idx_manual_review_doc_id" not in existing_indexes:
        op.create_index("idx_manual_review_doc_id", "manual_review", ["doc_id"])

    existing_indexes = {row.get("name") for row in inspector.get_indexes("issues")} if "issues" in inspector.get_table_names() else set()
    if "idx_issues_doc_phase" not in existing_indexes:
        op.create_index("idx_issues_doc_phase", "issues", ["doc_id", "phase"])

    existing_indexes = {row.get("name") for row in inspector.get_indexes("evidence_bundles")} if "evidence_bundles" in inspector.get_table_names() else set()
    if "idx_evidence_bundles_doc_id" not in existing_indexes:
        op.create_index("idx_evidence_bundles_doc_id", "evidence_bundles", ["doc_id"])
    if "idx_evidence_bundles_job_id" not in existing_indexes:
        op.create_index("idx_evidence_bundles_job_id", "evidence_bundles", ["job_id"])


def downgrade() -> None:
    op.drop_table("evidence_bundles", if_exists=True)
    op.drop_table("job_scoring", if_exists=True)
    op.drop_table("job_policy_snapshot", if_exists=True)
    op.drop_table("policy_packs", if_exists=True)
    op.drop_table("scan_jobs", if_exists=True)
    op.drop_table("manual_review", if_exists=True)
    op.drop_table("fix_reports", if_exists=True)
    op.drop_table("issues", if_exists=True)
    op.drop_table("documents", if_exists=True)
