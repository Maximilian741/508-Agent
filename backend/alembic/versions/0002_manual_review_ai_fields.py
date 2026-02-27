"""manual review ai fields

Revision ID: 0002_manual_review_ai_fields
Revises: 0001_initial
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_manual_review_ai_fields"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "manual_review" not in inspector.get_table_names():
        return
    existing = {str(col.get("name")) for col in inspector.get_columns("manual_review")}
    if "ai_decision_json" not in existing:
        op.add_column("manual_review", sa.Column("ai_decision_json", sa.Text(), nullable=True))
    if "ai_confidence" not in existing:
        op.add_column("manual_review", sa.Column("ai_confidence", sa.Float(), nullable=True))
    if "ai_status" not in existing:
        op.add_column("manual_review", sa.Column("ai_status", sa.Text(), nullable=True))
    if "validator_status" not in existing:
        op.add_column("manual_review", sa.Column("validator_status", sa.Text(), nullable=True))
    if "ai_model" not in existing:
        op.add_column("manual_review", sa.Column("ai_model", sa.Text(), nullable=True))
    if "ai_updated_at" not in existing:
        op.add_column("manual_review", sa.Column("ai_updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "manual_review" not in inspector.get_table_names():
        return
    existing = {str(col.get("name")) for col in inspector.get_columns("manual_review")}
    if "ai_updated_at" in existing:
        op.drop_column("manual_review", "ai_updated_at")
    if "ai_model" in existing:
        op.drop_column("manual_review", "ai_model")
    if "validator_status" in existing:
        op.drop_column("manual_review", "validator_status")
    if "ai_status" in existing:
        op.drop_column("manual_review", "ai_status")
    if "ai_confidence" in existing:
        op.drop_column("manual_review", "ai_confidence")
    if "ai_decision_json" in existing:
        op.drop_column("manual_review", "ai_decision_json")
