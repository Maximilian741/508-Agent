"""Smoke test for the auth + credits routers.

Spins up an in-memory FastAPI app with just the auth and credits routers,
points the SQLAlchemy engine at a temp sqlite file, creates the tables,
and walks through a full sign-in -> grant-starter -> balance -> spend ->
balance flow.  Asserts the balance dropped by the spent amount.

Usage:
    python -m app.devtools.smoke_auth_credits
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

# Use a fresh sqlite db for this run.  Must be set BEFORE we import the
# session module so the engine binds to the right URL.
_TMP_DIR = tempfile.mkdtemp(prefix="508_smoke_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DIR}/smoke.db"

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.auth import router as auth_router  # noqa: E402
from app.api.credits import router as credits_router  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session_sqlalchemy import ENGINE  # noqa: E402
from app.db import models as _models  # noqa: F401, E402


def _bootstrap() -> TestClient:
    Base.metadata.create_all(bind=ENGINE)
    app = FastAPI(title="smoke")
    app.include_router(auth_router)
    app.include_router(credits_router)
    return TestClient(app)


def main() -> int:
    client = _bootstrap()
    email = "smoke-tester@example.com"

    print("[1] POST /auth/sign-in")
    r = client.post(
        "/auth/sign-in",
        json={"email": email, "displayName": "Smoke", "password": "smokepass123"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    token = data["token"]
    user_id = data["user"]["id"]
    print("    token=", token[:8], "user_id=", user_id[:8], "balance=", data["user"]["creditsBalance"])

    headers = {"Authorization": f"Bearer {token}"}

    print("[2] POST /auth/grant-starter")
    r = client.post("/auth/grant-starter", headers=headers)
    assert r.status_code == 200, r.text
    grant = r.json()
    print("    granted=", grant["granted"], "amount=", grant["amount"], "balance=", grant["user"]["creditsBalance"])
    assert grant["granted"] is True
    assert grant["amount"] == 25
    assert grant["user"]["creditsBalance"] == 25

    # Idempotency: a second call should be a no-op.
    r2 = client.post("/auth/grant-starter", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["granted"] is False, r2.text

    print("[3] GET /credits/balance")
    r = client.get("/credits/balance", headers=headers)
    assert r.status_code == 200, r.text
    bal = r.json()
    print("    balance=", bal["balance"], "history_len=", len(bal["history"]))
    assert bal["balance"] == 25
    assert len(bal["history"]) >= 1

    print("[4] POST /credits/spend amount=5")
    r = client.post(
        "/credits/spend",
        headers=headers,
        json={"amount": 5, "description": "smoke_spend", "relatedDocId": "doc-smoke"},
    )
    assert r.status_code == 200, r.text
    spent = r.json()
    print("    newBalance=", spent["newBalance"], "spent=", spent["spent"])
    assert spent["newBalance"] == 20
    assert spent["spent"] == 5

    print("[5] GET /credits/balance (after spend)")
    r = client.get("/credits/balance", headers=headers)
    assert r.status_code == 200, r.text
    bal2 = r.json()
    print("    balance=", bal2["balance"])
    assert bal2["balance"] == 20, f"expected 20, got {bal2['balance']}"
    assert (bal["balance"] - bal2["balance"]) == 5, "balance must drop by 5"

    print("[6] insufficient credits -> 402")
    r = client.post(
        "/credits/spend",
        headers=headers,
        json={"amount": 9999, "description": "too_much"},
    )
    assert r.status_code == 402, r.text

    print("[7] POST /credits/purchase tier=starter")
    r = client.post("/credits/purchase", headers=headers, json={"tier": "starter"})
    assert r.status_code == 200, r.text
    pur = r.json()
    print("    newBalance=", pur["newBalance"], "purchased=", pur["purchased"])
    assert pur["purchased"] == 50
    assert pur["newBalance"] == 70

    print("[8] GET /auth/me")
    r = client.get("/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    me = r.json()
    print("    email=", me["email"], "creditsBalance=", me["creditsBalance"])
    assert me["email"] == email
    assert me["creditsBalance"] == 70

    print("ALL ASSERTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
