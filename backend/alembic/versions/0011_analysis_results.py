"""analysis_results table — server-computed scores for certificate binding

Revision ID: 0011_analysis_results
Revises: 0010_teams
Create Date: 2026-05-31
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_analysis_results"
down_revision = "0010_teams"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "analysis_results" not in set(inspector.get_table_names()):
        op.create_table(
            "analysis_results",
            sa.Column("id", sa.String(length=260), primary_key=True),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("document_id", sa.String(length=128), nullable=False),
            sa.Column("filename", sa.Text(), nullable=False),
            sa.Column("source_format", sa.String(length=16), nullable=False, server_default=""),
            sa.Column("initial_issues", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("fixed_automatically", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("pending_manual", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("grade", sa.String(length=8), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("user_id", "document_id", name="uq_analysis_user_doc"),
        )
        op.create_index("idx_analysis_user", "analysis_results", ["user_id"])


def downgrade() -> None:
    op.drop_table("analysis_results", if_exists=True)
