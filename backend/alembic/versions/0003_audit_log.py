"""audit log table

Revision ID: 0003_audit_log
Revises: 0002_manual_review_ai_fields
Create Date: 2026-05-03
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_audit_log"
down_revision = "0002_manual_review_ai_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "audit_log" not in inspector.get_table_names():
        op.create_table(
            "audit_log",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("at", sa.Text(), nullable=False),
            sa.Column("request_id", sa.Text()),
            sa.Column("actor_email", sa.Text()),
            sa.Column("actor_sub", sa.Text()),
            sa.Column("ip", sa.Text()),
            sa.Column("event", sa.Text(), nullable=False),
            sa.Column("doc_id", sa.Text()),
            sa.Column("job_id", sa.Text()),
            sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        )

    existing_indexes = (
        {row.get("name") for row in inspector.get_indexes("audit_log")}
        if "audit_log" in inspector.get_table_names()
        else set()
    )
    if "idx_audit_log_at" not in existing_indexes:
        op.create_index("idx_audit_log_at", "audit_log", ["at"])
    if "idx_audit_log_actor_email" not in existing_indexes:
        op.create_index("idx_audit_log_actor_email", "audit_log", ["actor_email"])
    if "idx_audit_log_event" not in existing_indexes:
        op.create_index("idx_audit_log_event", "audit_log", ["event"])


def downgrade() -> None:
    op.drop_index("idx_audit_log_event", table_name="audit_log", if_exists=True)
    op.drop_index("idx_audit_log_actor_email", table_name="audit_log", if_exists=True)
    op.drop_index("idx_audit_log_at", table_name="audit_log", if_exists=True)
    op.drop_table("audit_log", if_exists=True)
