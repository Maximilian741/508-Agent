"""certificates table for verifiable conformance certificates

Revision ID: 0008_certificates
Revises: 0007_subscriptions
Create Date: 2026-05-30
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_certificates"
down_revision = "0007_subscriptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "certificates" not in inspector.get_table_names():
        op.create_table(
            "certificates",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("issued_email", sa.String(length=320), nullable=True),
            sa.Column("filename", sa.Text(), nullable=False),
            sa.Column("conformance_claim", sa.Text(), nullable=False),
            sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("fixed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("remaining_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("paid_with", sa.String(length=32), nullable=False, server_default="credits"),
            sa.Column("issued_at", sa.DateTime(), nullable=False),
        )

    existing_indexes = (
        {row.get("name") for row in inspector.get_indexes("certificates")}
        if "certificates" in inspector.get_table_names()
        else set()
    )
    if "idx_certificates_user" not in existing_indexes:
        op.create_index("idx_certificates_user", "certificates", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_certificates_user", table_name="certificates", if_exists=True)
    op.drop_table("certificates", if_exists=True)
