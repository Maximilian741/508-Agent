"""Smoke: one starter grant per real mailbox.

'+tag' subaddressing and Gmail's ignored dots / googlemail.com alias all
deliver to one inbox, so verifying each variant proved nothing, and each got
25 credits: 4 variants farmed 100 credits with the SMTP verification gate ON.
Pins:

1. canonical_mailbox() folds those variants and leaves non-Gmail dots alone.
2. SMTP gate ON: alice@gmail.com + 3 variants, each verified -> ONE grant.
3. Order doesn't matter (a +tag address granted first blocks the bare one);
   non-Gmail dots are real, distinct mailboxes.
4. Changing the account email or deleting the account doesn't free the
   mailbox; the per-user repeat grant is still a no-op.
5. Without SMTP (dev) the mailbox rule still applies.
6. Migration 0015 backfills existing grants: pre-existing users keep their
   balance and get token_version 0, and a farmed mailbox can't be re-granted.

Usage:
    python -m app.devtools.smoke_starter_grant_mailbox
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="508_smoke_grant_mailbox_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/g.db"
os.environ.pop("SMTP_HOST", None)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

import app.api.auth as auth_mod  # noqa: E402
from app.main import app  # noqa: E402
from app.db.models import CreditLedgerRow, EmailVerifyTokenRow  # noqa: E402
from app.db.session_sqlalchemy import session_scope  # noqa: E402


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, detail: object = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, "" if cond else f"  [{detail}]")
        if not cond:
            failures += 1

    def client(ip: str) -> TestClient:
        # One rate-limit bucket per section (/auth allows 60 req/min per IP).
        return TestClient(app, headers={"X-Forwarded-For": ip})

    def onboard(c: TestClient, email: str, verify: bool = True):
        """Sign up, prove the inbox (token read from the DB), ask for the grant."""
        r = c.post("/auth/sign-in", json={"email": email, "password": "mailboxpass1"})
        assert r.status_code == 200, r.text
        tok, uid = r.json()["token"], r.json()["user"]["id"]
        hdr = {"Authorization": f"Bearer {tok}"}
        if verify:
            gated = c.post("/auth/grant-starter", headers=hdr)
            assert gated.status_code == 403 and "verify_email_first" in gated.text, gated.text
            c.post("/auth/request-verify-email", headers=hdr)
            with session_scope() as s:
                link = s.execute(
                    select(EmailVerifyTokenRow.token).where(EmailVerifyTokenRow.user_id == uid)
                ).scalars().first()
            assert c.get("/auth/verify-email", params={"token": link}).status_code == 200
        g = c.post("/auth/grant-starter", headers=hdr)
        assert g.status_code == 200, g.text
        return tok, g.json()

    # --- 1. canonicalization --------------------------------------------------
    cases = {
        "Alice@Gmail.com": "alice@gmail.com",
        "a.l.i.c.e+promo@googlemail.com": "alice@gmail.com",
        " alice+1@gmail.com ": "alice@gmail.com",
        "first.last+news@example.com": "first.last@example.com",
        "First.Last@Example.com": "first.last@example.com",
        "+only@example.com": "+only@example.com",
        "not-an-email": "not-an-email",
    }
    for raw, want in cases.items():
        got = auth_mod.canonical_mailbox(raw)
        check(f"canonical_mailbox({raw!r}) == {want!r}", got == want, got)

    # --- 2. SMTP gate ON: four variants of one Gmail inbox --------------------
    os.environ["SMTP_HOST"] = "127.0.0.1"
    auth_mod.send_email = lambda **kw: True  # never touch a network
    c = client("10.0.0.1")
    grants = {e: onboard(c, e)[1] for e in ("alice@gmail.com", "alice+1@gmail.com", "a.lice@gmail.com", "Al.I.ce+zz@googlemail.com")}
    first = grants.pop("alice@gmail.com")
    check("alice@gmail.com gets 25", first["granted"] is True and first["amount"] == 25, first)
    check(
        "+tag / dot / googlemail variants: granted False, amount 0, balance 0",
        all(g["granted"] is False and g["amount"] == 0 and g["user"]["creditsBalance"] == 0 for g in grants.values()),
        grants,
    )
    with session_scope() as s:
        total = s.execute(
            select(func.coalesce(func.sum(CreditLedgerRow.amount), 0)).where(CreditLedgerRow.description == "starter_grant")
        ).scalar()
    check("ledger: 25 starter credits total for the one mailbox (was 100)", total == 25, total)

    # --- 3. order independence; non-Gmail dots are distinct -------------------
    c = client("10.0.0.2")
    g1 = onboard(c, "bob+signup@example.com")[1]
    g2 = onboard(c, "bob@example.com")[1]
    check("a +tag address granted first blocks the bare address", (g1["amount"], g2["amount"]) == (25, 0), (g1, g2))
    g3 = onboard(c, "first.last@example.com")[1]
    g4 = onboard(c, "firstlast@example.com")[1]
    check("dots matter outside Gmail: two mailboxes, two grants", (g3["amount"], g4["amount"]) == (25, 25), (g3, g4))

    # --- 4. email change / deletion don't free the mailbox ----------------------
    c = client("10.0.0.3")
    carol_tok, g = onboard(c, "carol@example.com")
    check("carol gets 25", g["amount"] == 25, g)
    carol_h = {"Authorization": f"Bearer {carol_tok}"}
    r = c.post("/auth/grant-starter", headers=carol_h)
    check("repeat grant for the same user -> granted False", r.status_code == 200 and r.json()["granted"] is False and r.json()["amount"] == 0, r.text)
    r = c.patch("/auth/me", headers=carol_h, json={"email": "carol-renamed@example.com"})
    check("carol moves her account to another address", r.status_code == 200, r.text)
    g = onboard(c, "carol@example.com")[1]
    check("a new account on carol's old mailbox gets 0", g["amount"] == 0, g)
    dave_tok, g = onboard(c, "dave@example.com")
    check("dave gets 25", g["amount"] == 25, g)
    r = c.delete("/auth/me", headers={"Authorization": f"Bearer {dave_tok}"})
    check("dave deletes his account", r.status_code == 204, r.status_code)
    g = onboard(c, "dave+again@example.com")[1]
    check("re-registering a deleted account's mailbox gets 0", g["amount"] == 0, g)

    # --- 5. no SMTP (dev): gate off, mailbox rule still on ----------------------
    os.environ.pop("SMTP_HOST", None)
    c = client("10.0.0.4")
    g5 = onboard(c, "erin@example.com", verify=False)[1]
    g6 = onboard(c, "erin+2@example.com", verify=False)[1]
    check("without SMTP: first grants, +tag variant gets 0", (g5["amount"], g6["amount"]) == (25, 0), (g5, g6))

    # --- 6. migration 0015 backfill on a pre-fix database -----------------------
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_dir = Path(__file__).resolve().parents[2]
    mig_db = Path(_TMP) / "migrate.db"
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    app_db_url = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = f"sqlite:///{mig_db.as_posix()}"
    try:
        command.upgrade(cfg, "0014_monitored_sites")
        con = sqlite3.connect(mig_db)
        # Pre-fix state: two farmed variants of one mailbox, both granted, and
        # one variant that never asked.
        for uid, email, granted_at in (
            ("u1", "zed@gmail.com", "2026-01-01 00:00:00"),
            ("u2", "z.ed+2@gmail.com", "2026-01-02 00:00:00"),
            ("u3", "zed+3@gmail.com", None),
        ):
            con.execute(
                "INSERT INTO users (id, email, display_name, created_at, role, credits_balance) VALUES (?,?,?,?,?,?)",
                (uid, email, uid, "2026-01-01 00:00:00", "user", 25 if granted_at else 0),
            )
            if granted_at:
                con.execute(
                    "INSERT INTO credit_ledger (user_id, at, kind, amount, description) VALUES (?,?,?,?,?)",
                    (uid, granted_at, "grant", 25, "starter_grant"),
                )
        con.commit()
        con.close()
        command.upgrade(cfg, "head")
        con = sqlite3.connect(mig_db)
        claims = con.execute("SELECT mailbox_hash, user_id FROM starter_grants").fetchall()
        users = con.execute("SELECT id, token_version, credits_balance FROM users ORDER BY id").fetchall()
        head = con.execute("SELECT version_num FROM alembic_version").fetchall()
        con.close()
    finally:
        os.environ["DATABASE_URL"] = app_db_url
    # Resolve the head from the migration scripts rather than naming a
    # revision, so a later migration doesn't fail this smoke.
    expected_head = ScriptDirectory.from_config(cfg).get_current_head()
    check("0014 -> head upgrade lands on the repo head", head == [(expected_head,)], (head, expected_head))
    check(
        "backfill: one claim for the farmed mailbox, held by the earliest grant",
        claims == [(auth_mod.mailbox_hash("zed@gmail.com"), "u1")],
        claims,
    )
    check(
        "backfill: existing users keep balances, token_version 0",
        users == [("u1", 0, 25), ("u2", 0, 25), ("u3", 0, 0)],
        users,
    )

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
