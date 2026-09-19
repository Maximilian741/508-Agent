"""credit_ledger.stripe_ref (refund clawback) + re-canonicalise starter_grants

Two money fixes share this revision:

1. ``credit_ledger.stripe_ref`` records the PaymentIntent / Invoice whose money
   paid for a grant. Refund and dispute webhooks name that object, not our
   checkout-session id, so without it a reversal cannot find the rows it has
   to claw back.
2. ``starter_grants`` rows are re-hashed under the hardened
   ``canonical_mailbox``. A trailing dot on the domain ('alice@gmail.com.')
   used to defeat the Gmail dot/googlemail fold, so one inbox could hold many
   claims; those collapse to one here, earliest claim winning, and the farmed
   variants can no longer be re-granted.

Revision ID: 0017_ledger_stripe_ref_mailbox_recanon
Revises: 0016_api_key_revoked_reason
Create Date: 2026-09-19
"""

import hashlib
import unicodedata

from alembic import op
import sqlalchemy as sa


revision = "0017_ledger_stripe_ref_mailbox_recanon"
down_revision = "0016_api_key_revoked_reason"
branch_labels = None
depends_on = None

_GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})
_MAX_LOCAL_LEN = 64
_MAX_DOMAIN_LEN = 255


def _mailbox_hash(email):
    # Frozen copy of app.api.auth.canonical_mailbox + mailbox_hash as of this
    # revision: a migration must not import app code that can change after it
    # ships. Keep in step with auth.py if the folding rules change again.
    addr = unicodedata.normalize("NFKC", email or "").strip().lower()
    local, sep, domain = addr.rpartition("@")
    if sep and local and domain:
        if len(local) >= 2 and local[0] == '"' and local[-1] == '"':
            inner = local[1:-1]
            if inner and all(ch.isalnum() or ch in "._+-" for ch in inner):
                local = inner
        local = (local.split("+", 1)[0] or local)[:_MAX_LOCAL_LEN]
        labels = [label for label in domain.split(".") if label]
        domain = ".".join(labels)
        if domain:
            try:
                domain = domain.encode("idna").decode("ascii")
            except Exception:
                pass
            domain = domain[:_MAX_DOMAIN_LEN]
        if local and domain:
            if domain in _GMAIL_DOMAINS:
                local = local.replace(".", "") or local
                domain = "gmail.com"
            addr = f"{local}@{domain}"
    return hashlib.sha256(addr.encode("utf-8")).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "credit_ledger" in tables:
        cols = {col["name"] for col in inspector.get_columns("credit_ledger")}
        if "stripe_ref" not in cols:
            op.add_column("credit_ledger", sa.Column("stripe_ref", sa.String(length=255), nullable=True))
        indexes = {ix["name"] for ix in inspector.get_indexes("credit_ledger")}
        if "idx_ledger_stripe_ref" not in indexes:
            op.create_index("idx_ledger_stripe_ref", "credit_ledger", ["stripe_ref"])

    # Re-canonicalise the mailbox claims. Rows whose hash is unchanged stay
    # put; rows that now fold onto an already-claimed mailbox are dropped
    # (the earliest grant keeps the claim), which is what stops the farmed
    # variants from being granted again. Nothing is clawed back here: the
    # credits already handed out stay, and each farmed user's own ledger row
    # still blocks a repeat grant for that account.
    if "starter_grants" in tables:
        rows = bind.execute(
            sa.text(
                "SELECT g.mailbox_hash, g.user_id, g.granted_at, u.email "
                "FROM starter_grants g LEFT JOIN users u ON u.id = g.user_id "
                "ORDER BY g.granted_at"
            )
        ).fetchall()
        claimed = set()
        for old_hash, user_id, granted_at, email in rows:
            # No user row (deleted account): the old hash is all we have, and
            # it must keep holding its claim.
            new_hash = _mailbox_hash(email) if email else old_hash
            if new_hash == old_hash:
                claimed.add(old_hash)
                continue
            if new_hash in claimed:
                # Folds onto a mailbox that already holds the claim.
                bind.execute(
                    sa.text("DELETE FROM starter_grants WHERE mailbox_hash = :h"),
                    {"h": old_hash},
                )
                continue
            existing = bind.execute(
                sa.text("SELECT 1 FROM starter_grants WHERE mailbox_hash = :h"),
                {"h": new_hash},
            ).first()
            if existing is not None:
                bind.execute(
                    sa.text("DELETE FROM starter_grants WHERE mailbox_hash = :h"),
                    {"h": old_hash},
                )
            else:
                bind.execute(
                    sa.text(
                        "UPDATE starter_grants SET mailbox_hash = :new WHERE mailbox_hash = :old"
                    ),
                    {"new": new_hash, "old": old_hash},
                )
            claimed.add(new_hash)


def downgrade() -> None:
    # The mailbox re-hash is not reversible (merged claims cannot be split
    # back apart), so only the column is dropped.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "credit_ledger" in set(inspector.get_table_names()):
        indexes = {ix["name"] for ix in inspector.get_indexes("credit_ledger")}
        if "idx_ledger_stripe_ref" in indexes:
            op.drop_index("idx_ledger_stripe_ref", table_name="credit_ledger")
        cols = {col["name"] for col in inspector.get_columns("credit_ledger")}
        if "stripe_ref" in cols:
            with op.batch_alter_table("credit_ledger") as batch:
                batch.drop_column("stripe_ref")
