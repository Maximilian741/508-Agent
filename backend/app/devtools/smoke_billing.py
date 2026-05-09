"""Smoke test for the Stripe billing scaffold.

Verifies that with no STRIPE_SECRET_KEY set:
  - GET  /billing/config                       -> 200, enabled=false
  - POST /billing/create-checkout-session      -> 503, billing_not_configured
  - POST /credits/purchase                     -> 200 (mock path still works)

Usage:
    python -m app.devtools.smoke_billing
"""

from __future__ import annotations

import os
import sys
import tempfile

# Force-clear any inherited Stripe env so the smoke test is hermetic.
for _k in (
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "STRIPE_PRICE_STARTER",
    "STRIPE_PRICE_PRO",
    "STRIPE_PRICE_STUDIO",
):
    os.environ.pop(_k, None)

_TMP_DIR = tempfile.mkdtemp(prefix="508_smoke_billing_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DIR}/smoke.db"

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.auth import router as auth_router  # noqa: E402
from app.api.credits import router as credits_router  # noqa: E402
from app.api.stripe_billing import router as billing_router  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session_sqlalchemy import ENGINE  # noqa: E402
from app.db import models as _models  # noqa: F401, E402


def _bootstrap() -> TestClient:
    Base.metadata.create_all(bind=ENGINE)
    app = FastAPI(title="smoke-billing")
    app.include_router(auth_router)
    app.include_router(credits_router)
    app.include_router(billing_router)
    return TestClient(app)


def main() -> int:
    client = _bootstrap()

    print("[1] GET /billing/config (no key)")
    r = client.get("/billing/config")
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["enabled"] is False, f"expected enabled=false, got {cfg}"
    tiers = {t["tier"]: t for t in cfg["tiers"]}
    assert set(tiers.keys()) == {"starter", "pro", "studio"}, tiers
    assert tiers["starter"]["credits"] == 50
    assert tiers["pro"]["credits"] == 250
    assert tiers["studio"]["credits"] == 1300
    for t in tiers.values():
        assert t["priceConfigured"] is False, t
    print("    enabled=false, all three tiers reported, no price ids configured")

    print("[2] sign in to get a user id for the next call")
    r = client.post(
        "/auth/sign-in",
        json={"email": "billing-smoke@example.com", "displayName": "Billing"},
    )
    assert r.status_code == 200, r.text
    user_id = r.json()["user"]["id"]
    headers = {"X-Account-Id": user_id}

    print("[3] POST /billing/create-checkout-session (no key) -> 503")
    r = client.post(
        "/billing/create-checkout-session",
        headers=headers,
        json={
            "tier": "pro",
            "success_url": "https://example.com/ok",
            "cancel_url": "https://example.com/cancel",
        },
    )
    assert r.status_code == 503, r.text
    assert r.json().get("detail") == "billing_not_configured", r.text
    print("    503 billing_not_configured")

    print("[4] POST /credits/purchase (no key) still mocks")
    r = client.post(
        "/credits/purchase",
        headers=headers,
        json={"tier": "starter"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["purchased"] == 50, body
    assert body["tier"] == "starter", body
    print("    mock purchase ok, newBalance=", body["newBalance"])

    print("[5] flip STRIPE_SECRET_KEY on -> /credits/purchase becomes 409")
    os.environ["STRIPE_SECRET_KEY"] = "sk_test_dummy_for_smoke"
    try:
        r = client.post(
            "/credits/purchase",
            headers=headers,
            json={"tier": "starter"},
        )
        assert r.status_code == 409, r.text
        assert r.json().get("detail") == "use_stripe_checkout", r.text
        print("    409 use_stripe_checkout")

        r = client.get("/billing/config")
        assert r.status_code == 200, r.text
        assert r.json()["enabled"] is True
        print("    /billing/config now reports enabled=true")
    finally:
        os.environ.pop("STRIPE_SECRET_KEY", None)

    print("OK smoke_billing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
