"""scan_history table — remember each URL scan so a re-scan can report changes

Revision ID: 0013_scan_history
Revises: 0012_api_keys
Create Date: 2026-06-24
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_scan_history"
down_revision = "0012_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "scan_history" not in set(inspector.get_table_names()):
        op.create_table(
            "scan_history",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("url_key", sa.String(length=600), nullable=False),
            sa.Column("url", sa.Text(), nullable=False),
            sa.Column("issue_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("grade", sa.String(length=8), nullable=False, server_default=""),
            sa.Column("fingerprints", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("idx_scan_history_user_url", "scan_history", ["user_id", "url_key"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "scan_history" in set(inspector.get_table_names()):
        op.drop_index("idx_scan_history_user_url", table_name="scan_history")
        op.drop_table("scan_history")
