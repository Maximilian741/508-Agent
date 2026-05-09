"""user password_hash + email_verified_at + email_verify_tokens

Revision ID: 0005_user_password_and_verification
Revises: 0004_users_credits
Create Date: 2026-05-08
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_user_password_and_verification"
down_revision = "0004_users_credits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_user_cols = (
        {col["name"] for col in inspector.get_columns("users")}
        if "users" in inspector.get_table_names()
        else set()
    )
    if "users" in inspector.get_table_names():
        if "password_hash" not in existing_user_cols:
            op.add_column(
                "users",
                sa.Column("password_hash", sa.Text(), nullable=True),
            )
        if "email_verified_at" not in existing_user_cols:
            op.add_column(
                "users",
                sa.Column("email_verified_at", sa.DateTime(), nullable=True),
            )

    if "email_verify_tokens" not in inspector.get_table_names():
        op.create_table(
            "email_verify_tokens",
            sa.Column("token", sa.String(length=64), primary_key=True),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    token_indexes = (
        {row.get("name") for row in inspector.get_indexes("email_verify_tokens")}
        if "email_verify_tokens" in inspector.get_table_names()
        else set()
    )
    if "idx_email_verify_user" not in token_indexes:
        op.create_index(
            "idx_email_verify_user",
            "email_verify_tokens",
            ["user_id"],
        )


def downgrade() -> None:
    op.drop_index(
        "idx_email_verify_user",
        table_name="email_verify_tokens",
        if_exists=True,
    )
    op.drop_table("email_verify_tokens", if_exists=True)
    with op.batch_alter_table("users") as batch:
        batch.drop_column("email_verified_at")
        batch.drop_column("password_hash")
