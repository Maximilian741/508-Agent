"""Smoke test for the optional password + email verification scaffold.

Walks both flows on a fresh sqlite DB:

  Password flow:
    1) sign-in (no password yet, back-compat) -> token
    2) set-password
    3) sign-in without password -> 401 invalid_credentials
    4) sign-in with WRONG password -> 401
    5) sign-in with the correct password -> 200

  Verification flow:
    6) request-verify-email -> {queued: true}, log captured contains link
    7) GET /auth/verify-email?token=BAD -> 410
    8) GET /auth/verify-email?token=<good> -> {verified: true}
    9) /auth/me reflects emailVerifiedAt set + hasPassword true
   10) Second use of the same (now-deleted) token -> 410

Usage:
    python -m app.devtools.smoke_password_and_verify
"""

from __future__ import annotations

import logging
import os
import re
import sys
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="508_smoke_pw_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DIR}/smoke.db"

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.auth import router as auth_router  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session_sqlalchemy import ENGINE  # noqa: E402
from app.db import models as _models  # noqa: F401, E402


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(self.format(record))
        except Exception:
            self.records.append(record.getMessage())


def _bootstrap() -> tuple[TestClient, _ListHandler]:
    Base.metadata.create_all(bind=ENGINE)
    app = FastAPI(title="smoke-password-verify")
    app.include_router(auth_router)
    handler = _ListHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    auth_logger = logging.getLogger("app.api.auth")
    auth_logger.addHandler(handler)
    auth_logger.setLevel(logging.INFO)
    return TestClient(app), handler


def main() -> int:
    client, log_handler = _bootstrap()
    email = "pw-smoke@example.com"

    print("[1] sign-in (no password yet, back-compat)")
    r = client.post(
        "/auth/sign-in", json={"email": email, "displayName": "Pw Smoke"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    token = body["token"]
    user = body["user"]
    user_id = user["id"]
    assert user["hasPassword"] is False, user
    assert user["emailVerifiedAt"] is None, user
    headers = {"Authorization": f"Bearer {token}"}

    print("[2] set-password")
    r = client.post(
        "/auth/set-password", headers=headers, json={"password": "hunter2!"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["updated"] is True
    assert r.json()["user"]["hasPassword"] is True

    print("[3] sign-in WITHOUT password -> 401")
    r = client.post("/auth/sign-in", json={"email": email})
    assert r.status_code == 401, r.text

    print("[4] sign-in with WRONG password -> 401")
    r = client.post(
        "/auth/sign-in", json={"email": email, "password": "wrong"}
    )
    assert r.status_code == 401, r.text

    print("[5] sign-in with correct password -> 200")
    r = client.post(
        "/auth/sign-in", json={"email": email, "password": "hunter2!"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["hasPassword"] is True
    new_token = body["token"]
    headers = {"Authorization": f"Bearer {new_token}"}

    print("[6] request-verify-email")
    log_handler.records.clear()
    r = client.post("/auth/request-verify-email", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"queued": True}
    joined = "\n".join(log_handler.records)
    m = re.search(r"verify link: /auth/verify-email\?token=([0-9a-f]{32})", joined)
    assert m, f"verify link not found in logs: {joined!r}"
    good_token = m.group(1)
    print("    captured token=", good_token[:8], "...")

    print("[7] verify-email with BAD token -> 410")
    r = client.get("/auth/verify-email", params={"token": "deadbeef"})
    assert r.status_code == 410, r.text

    print("[8] verify-email with good token -> 200 verified")
    r = client.get("/auth/verify-email", params={"token": good_token})
    assert r.status_code == 200, r.text
    assert r.json() == {"verified": True}

    print("[9] /auth/me reflects emailVerifiedAt + hasPassword")
    r = client.get("/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["hasPassword"] is True, me
    assert isinstance(me["emailVerifiedAt"], str) and me["emailVerifiedAt"], me
    assert me["id"] == user_id

    print("[10] reusing the consumed token -> 410")
    r = client.get("/auth/verify-email", params={"token": good_token})
    assert r.status_code == 410, r.text

    print("ALL ASSERTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
