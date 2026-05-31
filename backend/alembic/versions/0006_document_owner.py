"""documents.owner_id for tenant isolation

Revision ID: 0006_document_owner
Revises: 0005_user_password_and_verification
Create Date: 2026-05-30
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_document_owner"
down_revision = "0005_user_password_and_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "documents" not in inspector.get_table_names():
        return

    existing_cols = {col["name"] for col in inspector.get_columns("documents")}
    if "owner_id" not in existing_cols:
        op.add_column(
            "documents",
            sa.Column("owner_id", sa.String(length=128), nullable=True),
        )

    existing_indexes = {row.get("name") for row in inspector.get_indexes("documents")}
    if "idx_documents_owner" not in existing_indexes:
        op.create_index("idx_documents_owner", "documents", ["owner_id"])


def downgrade() -> None:
    op.drop_index("idx_documents_owner", table_name="documents", if_exists=True)
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("owner_id")
