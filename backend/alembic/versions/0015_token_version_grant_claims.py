"""users.token_version (session revocation) + starter_grants (one grant per mailbox)

Revision ID: 0015_token_version_grant_claims
Revises: 0014_monitored_sites
Create Date: 2026-09-14
"""

import hashlib

from alembic import op
import sqlalchemy as sa


revision = "0015_token_version_grant_claims"
down_revision = "0014_monitored_sites"
branch_labels = None
depends_on = None

_GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})


def _mailbox_hash(email):
    # Frozen copy of app.api.auth.canonical_mailbox + mailbox_hash: a migration
    # must not import app code that can change after it ships.
    addr = (email or "").strip().lower()
    local, sep, domain = addr.rpartition("@")
    if sep and local and domain:
        local = local.split("+", 1)[0] or local
        if domain in _GMAIL_DOMAINS:
            local = local.replace(".", "") or local
            domain = "gmail.com"
        addr = f"{local}@{domain}"
    return hashlib.sha256(addr.encode("utf-8")).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "users" in tables:
        user_cols = {col["name"] for col in inspector.get_columns("users")}
        if "token_version" not in user_cols:
            # Default 0: every session minted before this (no "ver" claim,
            # which counts as 0) stays valid until that user's first bump.
            op.add_column(
                "users",
                sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
            )

    if "starter_grants" not in tables:
        op.create_table(
            "starter_grants",
            sa.Column("mailbox_hash", sa.String(length=64), primary_key=True),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("granted_at", sa.DateTime(), nullable=False),
        )
        op.create_index("idx_starter_grants_user", "starter_grants", ["user_id"])

        # Backfill: a mailbox that already received a starter grant can't get
        # another. When farmed variants share a mailbox the earliest grant
        # holds the claim; the others keep their credits (nothing is clawed
        # back) and their own ledger rows already block a repeat grant.
        if {"users", "credit_ledger"} <= tables:
            granted = bind.execute(
                sa.text(
                    "SELECT u.id, u.email, MIN(l.at) AS first_at "
                    "FROM users u JOIN credit_ledger l ON l.user_id = u.id "
                    "WHERE l.kind = 'grant' AND l.description = 'starter_grant' "
                    "GROUP BY u.id, u.email ORDER BY first_at"
                )
            ).fetchall()
            seen = set()
            for user_id, email, first_at in granted:
                digest = _mailbox_hash(email)
                if digest in seen:
                    continue
                seen.add(digest)
                bind.execute(
                    sa.text(
                        "INSERT INTO starter_grants (mailbox_hash, user_id, granted_at) "
                        "VALUES (:h, :u, :at)"
                    ),
                    {"h": digest, "u": user_id, "at": first_at},
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "starter_grants" in tables:
        op.drop_index("idx_starter_grants_user", table_name="starter_grants")
        op.drop_table("starter_grants")
    if "users" in tables and "token_version" in {c["name"] for c in inspector.get_columns("users")}:
        with op.batch_alter_table("users") as batch:
            batch.drop_column("token_version")
