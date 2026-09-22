"""Smoke: hostile mailboxes and unknown env labels can't mint free credits.

Three pre-launch audit findings, all reproduced on the shipped code:

1. CRITICAL — a trailing dot on the domain defeated canonical_mailbox.
   'gmail.com.' is not in _GMAIL_DOMAINS, so one dot skipped the dot-fold AND
   the googlemail fold AND produced a distinct mailbox_hash. Every dot
   placement in the local part then became its own grantable mailbox:
   2^(len(local)-1) variants per domain. Measured: 7 verified grants (175
   credits) from ONE real Gmail inbox; a 12-char local part yields ~4096.

2. HIGH — the starter-grant gate was keyed on SMTP_HOST being set, and
   deploy/.env.example ships SMTP_HOST= empty alongside APP_ENV=production.
   A launch with no mail sender therefore handed 25 credits to any typed
   address, deliverable or not ('throwaway@nope.invalid' granted).

3. MEDIUM — credit guards tested `== "production"` / `!= "production"`, so
   APP_ENV=staging (a real secret-bearing deploy) enabled unlimited mock
   credit purchases and no-charge overage packs.

Nothing here touches Stripe or a mail server.

Usage:
    python -m app.devtools.smoke_money_guards_fail_closed
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="508_smoke_money_guards_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/guards.db"
os.environ["STORAGE_LOCAL_ROOT"] = str(_TMP / "storage")
os.environ["MATERIALIZED_ROOT"] = str(_TMP / "materialized")
os.environ.pop("SMTP_HOST", None)
os.environ.pop("STRIPE_SECRET_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

import app.api.auth as auth_mod  # noqa: E402
import app.config as config_mod  # noqa: E402
from app.db.models import EmailVerifyTokenRow, StarterGrantRow, UserRow  # noqa: E402
from app.db.session_sqlalchemy import session_scope  # noqa: E402
from app.main import app  # noqa: E402


def _pre_fix_hash(email: str) -> str:
    """The mailbox_hash the SHIPPED (pre-0017) code wrote — no domain
    normalisation, so every trailing-dot variant got its own claim. Used to
    seed a realistic pre-fix database for the 0017 backfill test."""
    import hashlib

    addr = (email or "").strip().lower()
    local, sep, domain = addr.rpartition("@")
    if sep and local and domain:
        local = local.split("+", 1)[0] or local
        if domain in {"gmail.com", "googlemail.com"}:
            local = local.replace(".", "") or local
            domain = "gmail.com"
        addr = f"{local}@{domain}"
    return hashlib.sha256(addr.encode("utf-8")).hexdigest()


def _reload_settings(environment: str | None) -> None:
    """Swap the env label and drop the cached Settings so the guards re-read it."""
    if environment is None:
        os.environ.pop("APP_ENV", None)
        os.environ.pop("ENVIRONMENT", None)
    else:
        os.environ["APP_ENV"] = environment
        # A non-development label demands a stable APP_SECRET.
        os.environ.setdefault("APP_SECRET", "s" * 64)
    config_mod.get_settings.cache_clear()


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: object = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, "" if cond else f"  [{extra}]")
        if not cond:
            failures += 1

    # --- 1. canonical_mailbox on hostile input --------------------------------
    # Everything on the left of a group is the SAME inbox and must fold to one
    # canonical string, or it is a second free grant.
    same_inbox = [
        "alice@gmail.com",
        "Alice@Gmail.com",
        "  alice@gmail.com  ",
        "a.l.i.c.e@gmail.com",
        "alice+farm@gmail.com",
        "alice@googlemail.com",
        # RFC 1035 absolute form: same fully-qualified name, same MX.
        "alice@gmail.com.",
        "alice@gmail.com..",
        "alice@GMAIL.COM.",
        "a.lice@googlemail.com.",
        "a.l.i.c.e+promo@googlemail.com.",
        # quoted-string spelling of a plain dot-atom
        '"alice"@gmail.com',
        '"a.lice"@gmail.com.',
        # NFKC folds fullwidth characters onto ASCII
        "alice@ｇｍａｉｌ.ｃｏｍ",
    ]
    folded = {auth_mod.canonical_mailbox(a) for a in same_inbox}
    check(
        "every hostile spelling of one Gmail inbox folds to ONE canonical string",
        folded == {"alice@gmail.com"},
        sorted(folded),
    )

    # Genuinely different mailboxes must NOT be merged — over-folding would
    # wrongly refuse real users their starter credits.
    distinct = [
        "alice@example.com",
        "a.lice@example.com",        # dots are real outside Gmail
        "bob@gmail.com",
        "alice@gmail.co",            # different registrable domain
        "alice@mail.gmail.com",      # subdomain is a different host
        '"a@lice"@gmail.com',        # quoted string containing '@' is not a dot-atom
    ]
    canon = [auth_mod.canonical_mailbox(a) for a in distinct]
    check(
        "genuinely different mailboxes stay distinct",
        len(set(canon)) == len(distinct) and "alice@gmail.com" not in canon,
        canon,
    )

    # Never raise on junk, however hostile.
    junk = ["", "   ", "not-an-email", "@", "@gmail.com", "alice@", "alice@.", "alice@..",
            "a" * 500 + "@gmail.com", "alice@" + "b" * 500 + ".com", "\x00@gmail.com",
            "alice@xn--80ak6aa92e.com", "alice@аррӏе.com"]
    for raw in junk:
        try:
            auth_mod.canonical_mailbox(raw)
        except Exception as exc:  # noqa: BLE001
            check(f"canonical_mailbox({raw[:24]!r}) does not raise", False, exc)
    check("canonical_mailbox survives every junk/over-long input", True)
    check(
        "an over-long local part is capped, not unbounded",
        len(auth_mod.canonical_mailbox("a" * 500 + "@example.com")) < 100,
        len(auth_mod.canonical_mailbox("a" * 500 + "@example.com")),
    )

    # --- 2. the farm, end to end, with the verification gate ON ---------------
    _reload_settings("production")
    os.environ["SMTP_HOST"] = "smtp.example.invalid"
    client = TestClient(app, headers={"X-Forwarded-For": "10.66.0.1"})

    def onboard(email: str) -> dict:
        """Sign up, prove the inbox (token read from the DB), ask for the grant."""
        r = client.post("/auth/sign-in", json={"email": email, "password": "guardspass1"})
        assert r.status_code == 200, r.text
        uid = r.json()["user"]["id"]
        hdr = {"Authorization": f"Bearer {r.json()['token']}"}
        client.post("/auth/request-verify-email", headers=hdr)
        with session_scope() as s:
            link = s.execute(
                select(EmailVerifyTokenRow.token).where(EmailVerifyTokenRow.user_id == uid)
            ).scalars().first()
        if link:
            client.get("/auth/verify-email", params={"token": link})
        g = client.post("/auth/grant-starter", headers=hdr)
        return g.json() if g.status_code == 200 else {"status": g.status_code}

    farm = [
        "victimfarm@gmail.com",
        "victimfarm@gmail.com.",
        "v.ictimfarm@gmail.com.",
        "vi.ctimfarm@gmail.com.",
        "victimfarm@googlemail.com.",
        "victimfarm@gmail.com..",
        '"victimfarm"@gmail.com',
    ]
    results = [onboard(email) for email in farm]
    granted = [r for r in results if r.get("granted")]
    check(
        "7 verified spellings of one Gmail inbox yield exactly ONE grant",
        len(granted) == 1,
        [(e, r.get("granted")) for e, r in zip(farm, results)],
    )
    with session_scope() as s:
        total = int(s.execute(select(func.sum(UserRow.credits_balance))).scalar() or 0)
        rows = int(s.execute(select(func.count()).select_from(StarterGrantRow)).scalar() or 0)
    check("the farm minted 25 credits, not 175", total == 25, total)
    check("and claimed one mailbox, not seven", rows == 1, rows)

    # --- 3. SMTP unset must NOT switch the verification gate off --------------
    os.environ.pop("SMTP_HOST", None)
    for env_label in ("production", "staging", "prod-eu"):
        _reload_settings(env_label)
        r = client.post(
            "/auth/sign-in",
            json={"email": f"throwaway-{env_label}@nope.invalid", "password": "guardspass1"},
        )
        hdr = {"Authorization": f"Bearer {r.json()['token']}"}
        g = client.post("/auth/grant-starter", headers=hdr)
        check(
            f"APP_ENV={env_label} + SMTP unset still demands a verified email",
            g.status_code == 403 and "verify_email_first" in g.text,
            (g.status_code, g.text[:80]),
        )

    with session_scope() as s:
        after = int(s.execute(select(func.sum(UserRow.credits_balance))).scalar() or 0)
    check("no unverifiable address got credits", after == total, (total, after))

    # Development with no mail sender keeps the local convenience bypass.
    _reload_settings(None)
    r = client.post("/auth/sign-in", json={"email": "devbox@example.com", "password": "guardspass1"})
    hdr = {"Authorization": f"Bearer {r.json()['token']}"}
    g = client.post("/auth/grant-starter", headers=hdr)
    check("a dev box with no SMTP still grants (local convenience)", g.json().get("granted") is True, g.text[:120])

    # --- 4. money guards fail CLOSED on an unknown env label ------------------
    check("Settings.is_dev is true only for 'development'", config_mod.get_settings().is_dev)
    for env_label in ("production", "staging", "prod", "prod-eu", "PRODUCTION", "test"):
        _reload_settings(env_label)
        check(f"is_dev is False for APP_ENV={env_label}", not config_mod.get_settings().is_dev)
        r = client.post(
            "/auth/sign-in",
            json={"email": f"buyer-{env_label.lower()}@example.com", "password": "guardspass1"},
        )
        hdr = {"Authorization": f"Bearer {r.json()['token']}"}
        p = client.post("/credits/purchase", headers=hdr, json={"tier": "studio"})
        check(
            f"the dev mock purchase is refused under APP_ENV={env_label}",
            p.status_code == 503 and "billing_not_configured" in p.text,
            (p.status_code, p.text[:80]),
        )

    # OVERAGE_TEST_MODE grants credits off a synthetic charge; outside
    # development it must be ignored so no free pack can land.
    from app.api import stripe_billing

    os.environ["OVERAGE_TEST_MODE"] = "succeed"
    for env_label in ("production", "staging", "prod-eu"):
        _reload_settings(env_label)
        check(
            f"OVERAGE_TEST_MODE is ignored under APP_ENV={env_label}",
            stripe_billing._charge_overage("cus_x", "user_x") is None,
        )
    _reload_settings(None)
    check(
        "but a dev box can still exercise the overage flow",
        str(stripe_billing._charge_overage("cus_x", "user_x") or "").startswith("pi_test_"),
    )
    os.environ.pop("OVERAGE_TEST_MODE", None)

    # The mock purchase path still works on a dev box.
    _reload_settings(None)
    r = client.post("/auth/sign-in", json={"email": "devbuyer@example.com", "password": "guardspass1"})
    hdr = {"Authorization": f"Bearer {r.json()['token']}"}
    p = client.post("/credits/purchase", headers=hdr, json={"tier": "studio"})
    check("the dev mock purchase still works on a dev box", p.status_code == 200, (p.status_code, p.text[:80]))

    # --- 5. migration 0017 re-canonicalises mailboxes farmed before the fix ---
    # A database that ran 0015 holds one starter_grants row PER trailing-dot
    # variant. Upgrading must collapse them so the farmer can't come back for
    # another grant on any of those spellings.
    import sqlite3

    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_dir = Path(__file__).resolve().parents[2]
    mig_db = _TMP / "migrate0017.db"
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    app_db_url = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = f"sqlite:///{mig_db.as_posix()}"
    farmed = [
        ("f1", "zed@gmail.com", "2026-01-01 00:00:00"),
        ("f2", "zed@gmail.com.", "2026-01-02 00:00:00"),
        ("f3", "z.ed@gmail.com.", "2026-01-03 00:00:00"),
        ("f4", "zed@googlemail.com.", "2026-01-04 00:00:00"),
        ("f5", "other@example.com", "2026-01-05 00:00:00"),
    ]
    try:
        command.upgrade(cfg, "0016_api_key_revoked_reason")
        con = sqlite3.connect(mig_db)
        for uid, email, at in farmed:
            con.execute(
                "INSERT INTO users (id, email, display_name, created_at, role, credits_balance, token_version) "
                "VALUES (?,?,?,?,?,?,?)",
                (uid, email, uid, at, "user", 25, 0),
            )
            con.execute(
                "INSERT INTO credit_ledger (user_id, at, kind, amount, description) VALUES (?,?,?,?,?)",
                (uid, at, "grant", 25, "starter_grant"),
            )
            # The PRE-fix hash: exactly what 0015 would have written.
            con.execute(
                "INSERT INTO starter_grants (mailbox_hash, user_id, granted_at) VALUES (?,?,?)",
                (_pre_fix_hash(email), uid, at),
            )
        con.commit()
        before = con.execute("SELECT COUNT(*) FROM starter_grants").fetchone()[0]
        con.close()
        command.upgrade(cfg, "head")
        con = sqlite3.connect(mig_db)
        claims = con.execute(
            "SELECT mailbox_hash, user_id FROM starter_grants ORDER BY granted_at"
        ).fetchall()
        balances = con.execute("SELECT id, credits_balance FROM users ORDER BY id").fetchall()
        ledger_cols = {r[1] for r in con.execute("PRAGMA table_info(credit_ledger)").fetchall()}
        head = con.execute("SELECT version_num FROM alembic_version").fetchall()
        con.close()
    finally:
        os.environ["DATABASE_URL"] = app_db_url

    expected_head = ScriptDirectory.from_config(cfg).get_current_head()
    check("0016 -> head upgrade lands on the repo head", head == [(expected_head,)], (head, expected_head))
    check("the pre-fix database really did hold 5 separate claims", before == 5, before)
    check(
        "0017 collapses the 4 farmed variants into ONE claim (plus the real other mailbox)",
        len(claims) == 2,
        claims,
    )
    check(
        "the earliest grant keeps the claim, under the NEW canonical hash",
        (auth_mod.mailbox_hash("zed@gmail.com"), "f1") in claims,
        claims,
    )
    check(
        "the unrelated mailbox is untouched",
        (auth_mod.mailbox_hash("other@example.com"), "f5") in claims,
        claims,
    )
    check(
        "nothing is clawed back: the farmed users keep the credits already issued",
        balances == [("f1", 25), ("f2", 25), ("f3", 25), ("f4", 25), ("f5", 25)],
        balances,
    )
    check("0017 added credit_ledger.stripe_ref", "stripe_ref" in ledger_cols, sorted(ledger_cols))

    print()
    print("FAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
