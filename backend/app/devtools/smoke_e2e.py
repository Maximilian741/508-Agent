"""End-to-end smoke for the full ship path.

Exercises the happy path that a brand-new user would walk through:

    1)  POST /auth/sign-in           (fake email, no password yet)
    2)  POST /auth/grant-starter     (idempotent +25 credits)
    3)  GET  /credits/balance        (asserts balance == 25)
    4)  POST /auth/set-password
    5)  POST /auth/sign-in           (now WITH the password)
    6)  PATCH /auth/me               (update displayName)
    7)  GET  /healthz                (200)
    8)  GET  /billing/config         (enabled=False; no Stripe key)
    9)  POST /auth/sign-out          (204)

A green run means: app boots, db works, jwt minting+verifying works,
auth flow works, credits ledger works, billing config probe works,
healthz works, profile editing works, sign-out works.

Usage:
    python -m app.devtools.smoke_e2e
"""

from __future__ import annotations

import os
import sys
import tempfile

# Hermetic env - no Stripe key, fresh sqlite. Must be set BEFORE we import
# anything that touches settings or the engine.
for _k in (
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "STRIPE_PRICE_STARTER",
    "STRIPE_PRICE_PRO",
    "STRIPE_PRICE_STUDIO",
):
    os.environ.pop(_k, None)

_TMP_DIR = tempfile.mkdtemp(prefix="508_smoke_e2e_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DIR}/smoke.db"

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.auth import router as auth_router  # noqa: E402
from app.api.credits import router as credits_router  # noqa: E402
from app.api.health import router as health_router  # noqa: E402
from app.api.stripe_billing import router as billing_router  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session_sqlalchemy import ENGINE  # noqa: E402
from app.db import models as _models  # noqa: F401, E402


def _bootstrap() -> TestClient:
    Base.metadata.create_all(bind=ENGINE)
    app = FastAPI(title="smoke-e2e")
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(credits_router)
    app.include_router(billing_router)
    return TestClient(app)


def main() -> int:
    client = _bootstrap()
    email = "e2e-smoke@example.com"

    print("[1] POST /auth/sign-in (no password, fresh user)")
    r = client.post(
        "/auth/sign-in",
        json={"email": email, "displayName": "E2E Smoke"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    token = body["token"]
    user = body["user"]
    user_id = user["id"]
    assert user["email"] == email, user
    assert user["hasPassword"] is False, user
    assert user["creditsBalance"] == 0, user
    print(
        "    user_id=", user_id[:8],
        "token=", token[:8],
        "balance=", user["creditsBalance"],
    )
    bearer = {"Authorization": f"Bearer {token}"}

    print("[2] POST /auth/grant-starter")
    # grant-starter only reads X-Account-Id, not Bearer -- pass both for safety.
    headers = {**bearer, "X-Account-Id": user_id}
    r = client.post("/auth/grant-starter", headers=headers)
    assert r.status_code == 200, r.text
    grant = r.json()
    assert grant["granted"] is True, grant
    assert grant["amount"] == 25, grant
    assert grant["user"]["creditsBalance"] == 25, grant
    print(
        "    granted=", grant["granted"],
        "amount=", grant["amount"],
        "balance=", grant["user"]["creditsBalance"],
    )

    print("[3] GET /credits/balance -> assert 25")
    r = client.get("/credits/balance", headers=headers)
    assert r.status_code == 200, r.text
    bal = r.json()
    assert bal["balance"] == 25, bal
    assert len(bal["history"]) >= 1, bal
    print("    balance=", bal["balance"], "history_len=", len(bal["history"]))

    print("[4] POST /auth/set-password")
    r = client.post(
        "/auth/set-password",
        headers=bearer,
        json={"password": "hunter2!"},
    )
    assert r.status_code == 200, r.text
    sp = r.json()
    assert sp["updated"] is True, sp
    assert sp["user"]["hasPassword"] is True, sp
    print("    updated=", sp["updated"], "hasPassword=", sp["user"]["hasPassword"])

    print("[5] POST /auth/sign-in WITH password -> new JWT")
    r = client.post(
        "/auth/sign-in",
        json={"email": email, "password": "hunter2!"},
    )
    assert r.status_code == 200, r.text
    body2 = r.json()
    new_token = body2["token"]
    assert new_token, body2
    assert body2["user"]["id"] == user_id, body2
    assert body2["user"]["hasPassword"] is True, body2
    print("    new_token=", new_token[:8], "(differs?", new_token != token, ")")
    bearer2 = {"Authorization": f"Bearer {new_token}"}

    print("[6] PATCH /auth/me -> rename")
    r = client.patch(
        "/auth/me",
        headers={**bearer2, "X-Account-Id": user_id},
        json={"displayName": "E2E Renamed"},
    )
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["displayName"] == "E2E Renamed", me
    assert me["id"] == user_id, me
    print("    displayName=", me["displayName"])

    print("[7] GET /healthz")
    r = client.get("/healthz")
    assert r.status_code == 200, r.text
    print("    /healthz ok ->", r.json())

    print("[8] GET /billing/config -> enabled=false (no Stripe key)")
    r = client.get("/billing/config")
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["enabled"] is False, cfg
    print("    enabled=", cfg["enabled"], "tiers=", len(cfg.get("tiers", [])))

    print("[9] POST /auth/sign-out")
    r = client.post("/auth/sign-out", headers={"X-Account-Id": user_id})
    assert r.status_code == 204, r.status_code
    print("    sign-out 204")

    print("ALL ASSERTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
