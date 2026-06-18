"""api_keys table — developer API keys for programmatic scanning

Revision ID: 0012_api_keys
Revises: 0011_analysis_results
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_api_keys"
down_revision = "0011_analysis_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "api_keys" not in set(inspector.get_table_names()):
        op.create_table(
            "api_keys",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False, server_default="API key"),
            sa.Column("key_hash", sa.String(length=64), nullable=False),
            sa.Column("key_prefix", sa.String(length=24), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
        )
        op.create_index("idx_api_keys_user", "api_keys", ["user_id"])
        op.create_index("idx_api_keys_hash", "api_keys", ["key_hash"])


def downgrade() -> None:
    op.drop_table("api_keys", if_exists=True)
