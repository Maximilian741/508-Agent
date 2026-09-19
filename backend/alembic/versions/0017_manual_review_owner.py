"""manual_review.owner_id — a review item belongs to the tenant who queued it

Revision ID: 0017_manual_review_owner
Revises: 0016_api_key_revoked_reason
Create Date: 2026-09-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_manual_review_owner"
down_revision = "0016_api_key_revoked_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "manual_review" not in set(inspector.get_table_names()):
        return
    cols = {col["name"] for col in inspector.get_columns("manual_review")}
    if "owner_id" not in cols:
        # Nullable: rows written before this column existed keep working and
        # stay visible to the owner of their document.
        op.add_column("manual_review", sa.Column("owner_id", sa.String(length=128), nullable=True))
    indexes = {ix["name"] for ix in inspector.get_indexes("manual_review")}
    if "idx_manual_review_owner" not in indexes:
        op.create_index("idx_manual_review_owner", "manual_review", ["owner_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "manual_review" not in set(inspector.get_table_names()):
        return
    indexes = {ix["name"] for ix in inspector.get_indexes("manual_review")}
    if "idx_manual_review_owner" in indexes:
        op.drop_index("idx_manual_review_owner", table_name="manual_review")
    cols = {col["name"] for col in inspector.get_columns("manual_review")}
    if "owner_id" in cols:
        with op.batch_alter_table("manual_review") as batch:
            batch.drop_column("owner_id")
