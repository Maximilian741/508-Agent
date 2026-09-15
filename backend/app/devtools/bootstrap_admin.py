"""Make yourself an admin. Ops-only: run it with a shell on the server.

This is the ONLY way to become admin. ``app.api.deps.is_admin_user`` needs
``role == 'admin'`` (set here and nowhere else), a verified email, and that
email listed in ``ADMIN_EMAILS``. Nothing reachable over HTTP sets the role, so
signing up as a listed address, moving an account onto one, or getting its
inbox owner to click a verify link grants nothing. A shell on the server is
the proof of ownership. It needs no SMTP.

For an address in ``ADMIN_EMAILS`` it:
- creates the account if nobody has signed up with it yet, or
- takes over the existing account: sets the password you type and revokes
  every session on it, so if someone registered your address before you,
  their password and tokens stop working;
- marks the email verified and the account admin.

Revoke by removing the address from ``ADMIN_EMAILS`` (and restarting); an
email change on the account also drops admin.

Usage (on the server):
    docker compose exec backend python -m app.devtools.bootstrap_admin you@yourdomain.com

Scripted (password on stdin, first line):
    printf '%s\\n' "$PW" | docker compose exec -T backend \\
        python -m app.devtools.bootstrap_admin --password-stdin you@yourdomain.com
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import delete, select

from app.api.auth import _MIN_PASSWORD_LEN, _bump_token_version, _hash_password
from app.config import get_settings
from app.db.base import Base
from app.db.models import EmailVerifyTokenRow, UserRow
from app.db.session_sqlalchemy import ENGINE, session_scope


class BootstrapError(Exception):
    """A refusal whose message tells the operator what to do."""


def _require_listed(email: str) -> str:
    email_norm = (email or "").strip().lower()
    if not get_settings().is_admin(email_norm):
        raise BootstrapError(
            f"{email_norm or '(empty)'} is not in ADMIN_EMAILS. Add it to .env, restart the "
            "backend (docker compose up -d backend), then run this again."
        )
    return email_norm


def bootstrap_admin(email: str, password: str) -> dict:
    """Create or take over the ADMIN_EMAILS account ``email``: verified, admin.

    Returns ``{"userId", "email", "created"}``; raises :class:`BootstrapError`.
    """
    email_norm = _require_listed(email)
    # sign-in strips the password it is given; match it or the login fails.
    password = (password or "").strip()
    if len(password) < _MIN_PASSWORD_LEN:
        raise BootstrapError(f"password must be at least {_MIN_PASSWORD_LEN} characters")
    if get_settings().environment == "development":
        # Mirror main.py: dev sqlite has no migrations; elsewhere Alembic owns the schema.
        Base.metadata.create_all(bind=ENGINE)

    now = datetime.utcnow()
    with session_scope() as session:
        row = session.execute(
            select(UserRow).where(UserRow.email == email_norm)
        ).scalar_one_or_none()
        created = row is None
        if created:
            row = UserRow(
                id=uuid.uuid4().hex,
                email=email_norm,
                display_name=(email_norm.split("@")[0] or email_norm)[:120],
                created_at=now,
                last_seen_at=None,
                role="admin",
                credits_balance=0,
                password_hash=_hash_password(password),
                email_verified_at=now,
                token_version=0,
            )
            session.add(row)
        else:
            row.password_hash = _hash_password(password)
            row.email_verified_at = row.email_verified_at or now
            row.role = "admin"
            _bump_token_version(row)
            # Verify/reset links someone else may have requested for it.
            session.execute(
                delete(EmailVerifyTokenRow).where(EmailVerifyTokenRow.user_id == row.id)
            )
        return {"userId": row.id, "email": email_norm, "created": created}


def _read_password(from_stdin: bool) -> str:
    if from_stdin or not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("Password for this admin account: ")
    if getpass.getpass("Repeat it: ") != first:
        raise BootstrapError("the passwords did not match")
    return first


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.devtools.bootstrap_admin",
        description="Create or take over a verified ADMIN_EMAILS account.",
    )
    parser.add_argument("email", help="an address listed in ADMIN_EMAILS")
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from the first line of stdin instead of prompting",
    )
    args = parser.parse_args(argv)
    try:
        _require_listed(args.email)  # refuse before prompting
        result = bootstrap_admin(args.email, _read_password(args.password_stdin))
    except BootstrapError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    verb = "Created" if result["created"] else "Took over"
    base = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    print(f"{verb} {result['email']}: admin, email verified, password set, every other session signed out.")
    print(f"Sign in at {base or 'the app'} with that password, then open {base}/admin.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
