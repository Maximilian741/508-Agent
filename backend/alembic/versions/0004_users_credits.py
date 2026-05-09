"""users + credit ledger

Revision ID: 0004_users_credits
Revises: 0003_audit_log
Create Date: 2026-05-03
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_users_credits"
down_revision = "0003_audit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "users" not in inspector.get_table_names():
        op.create_table(
            "users",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("email", sa.String(length=320), nullable=False),
            sa.Column("display_name", sa.String(length=120), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=True),
            sa.Column("role", sa.String(length=32), nullable=False, server_default="user"),
            sa.Column("credits_balance", sa.Integer(), nullable=False, server_default="0"),
        )

    user_indexes = (
        {row.get("name") for row in inspector.get_indexes("users")}
        if "users" in inspector.get_table_names()
        else set()
    )
    if "ix_users_email" not in user_indexes:
        op.create_index("ix_users_email", "users", ["email"], unique=True)

    if "credit_ledger" not in inspector.get_table_names():
        op.create_table(
            "credit_ledger",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("at", sa.DateTime(), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("amount", sa.Integer(), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("related_doc_id", sa.String(length=128), nullable=True),
        )

    ledger_indexes = (
        {row.get("name") for row in inspector.get_indexes("credit_ledger")}
        if "credit_ledger" in inspector.get_table_names()
        else set()
    )
    if "idx_ledger_user_at" not in ledger_indexes:
        op.create_index("idx_ledger_user_at", "credit_ledger", ["user_id", "at"])


def downgrade() -> None:
    op.drop_index("idx_ledger_user_at", table_name="credit_ledger", if_exists=True)
    op.drop_table("credit_ledger", if_exists=True)
    op.drop_index("ix_users_email", table_name="users", if_exists=True)
    op.drop_table("users", if_exists=True)
