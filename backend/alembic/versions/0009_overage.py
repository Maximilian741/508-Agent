"""subscriptions.overage_enabled for usage-based overage

Revision ID: 0009_overage
Revises: 0008_certificates
Create Date: 2026-05-30
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_overage"
down_revision = "0008_certificates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "subscriptions" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("subscriptions")}
    if "overage_enabled" not in cols:
        op.add_column(
            "subscriptions",
            sa.Column("overage_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade() -> None:
    with op.batch_alter_table("subscriptions") as batch:
        batch.drop_column("overage_enabled")
