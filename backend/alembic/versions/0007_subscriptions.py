"""subscriptions table for recurring credit-allowance plans

Revision ID: 0007_subscriptions
Revises: 0006_document_owner
Create Date: 2026-05-30
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_subscriptions"
down_revision = "0006_document_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "subscriptions" not in inspector.get_table_names():
        op.create_table(
            "subscriptions",
            sa.Column("id", sa.String(length=255), primary_key=True),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("plan", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
            sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
            sa.Column("current_period_end", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )

    existing_indexes = (
        {row.get("name") for row in inspector.get_indexes("subscriptions")}
        if "subscriptions" in inspector.get_table_names()
        else set()
    )
    if "idx_subscriptions_user" not in existing_indexes:
        op.create_index("idx_subscriptions_user", "subscriptions", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_subscriptions_user", table_name="subscriptions", if_exists=True)
    op.drop_table("subscriptions", if_exists=True)
