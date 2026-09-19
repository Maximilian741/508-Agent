"""api_keys.revoked_reason — say WHY a key was revoked (e.g. a password reset)

Revision ID: 0016_api_key_revoked_reason
Revises: 0015_token_version_grant_claims
Create Date: 2026-09-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_api_key_revoked_reason"
down_revision = "0015_token_version_grant_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "api_keys" in set(inspector.get_table_names()):
        cols = {col["name"] for col in inspector.get_columns("api_keys")}
        if "revoked_reason" not in cols:
            # NULL = revoked by the owner from Settings (or never revoked).
            op.add_column("api_keys", sa.Column("revoked_reason", sa.String(length=32), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "api_keys" in set(inspector.get_table_names()):
        cols = {col["name"] for col in inspector.get_columns("api_keys")}
        if "revoked_reason" in cols:
            with op.batch_alter_table("api_keys") as batch:
                batch.drop_column("revoked_reason")
