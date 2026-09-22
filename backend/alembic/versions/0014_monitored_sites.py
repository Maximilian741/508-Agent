"""monitored_sites table — scheduled re-scans with a multi-worker lease

Revision ID: 0014_monitored_sites
Revises: 0013_scan_history
Create Date: 2026-06-24
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_monitored_sites"
down_revision = "0013_scan_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "monitored_sites" not in set(inspector.get_table_names()):
        op.create_table(
            "monitored_sites",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("url", sa.Text(), nullable=False),
            sa.Column("frequency", sa.String(length=16), nullable=False, server_default="weekly"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("notify_email", sa.String(length=320), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("next_run_at", sa.DateTime(), nullable=False),
            sa.Column("last_run_at", sa.DateTime(), nullable=True),
            sa.Column("last_issue_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_status", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("claimed_by", sa.String(length=64), nullable=True),
            sa.Column("claimed_at", sa.DateTime(), nullable=True),
        )
        op.create_index("idx_monitor_user", "monitored_sites", ["user_id"])
        op.create_index("idx_monitor_due", "monitored_sites", ["enabled", "next_run_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "monitored_sites" in set(inspector.get_table_names()):
        op.drop_index("idx_monitor_due", table_name="monitored_sites")
        op.drop_index("idx_monitor_user", table_name="monitored_sites")
        op.drop_table("monitored_sites")
