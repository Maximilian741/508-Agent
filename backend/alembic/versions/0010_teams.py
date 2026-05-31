"""teams, team_members, team_invites for multi-seat team plans

Revision ID: 0010_teams
Revises: 0009_overage
Create Date: 2026-05-30
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_teams"
down_revision = "0009_overage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "teams" not in tables:
        op.create_table(
            "teams",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("owner_id", sa.String(length=128), nullable=False),
            sa.Column("seat_limit", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("idx_teams_owner", "teams", ["owner_id"])

    if "team_members" not in tables:
        op.create_table(
            "team_members",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("team_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=False, server_default="member"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("user_id", name="uq_team_members_user"),
        )
        op.create_index("idx_team_members_team", "team_members", ["team_id"])

    if "team_invites" not in tables:
        op.create_table(
            "team_invites",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("team_id", sa.String(length=64), nullable=False),
            sa.Column("email", sa.String(length=320), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=False, server_default="member"),
            sa.Column("token", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("invited_by", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("accepted_user_id", sa.String(length=128), nullable=True),
            sa.Column("accepted_at", sa.DateTime(), nullable=True),
        )
        op.create_index("idx_team_invites_team", "team_invites", ["team_id"])
        op.create_index("idx_team_invites_email", "team_invites", ["email"])
        op.create_index("idx_team_invites_token", "team_invites", ["token"])


def downgrade() -> None:
    op.drop_table("team_invites", if_exists=True)
    op.drop_table("team_members", if_exists=True)
    op.drop_table("teams", if_exists=True)
