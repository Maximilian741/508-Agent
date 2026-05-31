"""Smoke test for the GDPR-style account self-service endpoints.

Walks through:
    sign-in -> grant-starter -> spend (to populate ledger) ->
    PATCH /auth/me {displayName} ->
    GET /auth/export (verify payload shape + content) ->
    DELETE /auth/me -> assert /auth/me 401 after.

Usage:
    python -m app.devtools.smoke_account_self_service
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

# Use a fresh sqlite db for this run.  Must be set BEFORE we import the
# session module so the engine binds to the right URL.
_TMP_DIR = tempfile.mkdtemp(prefix="508_smoke_self_")
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
    app = FastAPI(title="smoke-self-service")
    app.include_router(auth_router)
    app.include_router(credits_router)
    return TestClient(app)


def main() -> int:
    client = _bootstrap()
    email = "self-service@example.com"

    print("[1] POST /auth/sign-in")
    r = client.post(
        "/auth/sign-in",
        json={"email": email, "displayName": "Selfie", "password": "selfiepass1"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    token = data["token"]
    user_id = data["user"]["id"]
    headers = {"Authorization": f"Bearer {token}"}
    grant_headers = headers
    print("    user_id=", user_id[:8], "displayName=", data["user"]["displayName"])

    print("[2] POST /auth/grant-starter (populate ledger)")
    r = client.post("/auth/grant-starter", headers=grant_headers)
    assert r.status_code == 200, r.text
    assert r.json()["granted"] is True

    print("[3] PATCH /auth/me {displayName}")
    r = client.patch("/auth/me", headers=headers, json={"displayName": "Renamed Selfie"})
    assert r.status_code == 200, r.text
    patched = r.json()
    print("    displayName=", patched["displayName"])
    assert patched["displayName"] == "Renamed Selfie"
    assert patched["id"] == user_id

    # Verify it stuck via /auth/me
    r = client.get("/auth/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["displayName"] == "Renamed Selfie"

    print("[4] GET /auth/export")
    r = client.get("/auth/export", headers=headers)
    assert r.status_code == 200, r.text
    assert r.headers.get("content-type", "").startswith("application/json"), r.headers
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd and ".json" in cd, cd
    payload = json.loads(r.content.decode("utf-8"))
    assert "user" in payload and "credit_history" in payload, list(payload.keys())
    assert "audit_log_entries_for_this_user" in payload, list(payload.keys())
    assert payload["user"]["id"] == user_id
    assert payload["user"]["email"] == email
    assert payload["user"]["displayName"] == "Renamed Selfie"
    # grant-starter put at least one ledger row in
    assert len(payload["credit_history"]) >= 1, payload["credit_history"]
    print("    keys=", sorted(payload.keys()), "history_len=", len(payload["credit_history"]))

    print("[5] DELETE /auth/me")
    r = client.delete("/auth/me", headers=headers)
    assert r.status_code == 204, r.text

    print("[6] GET /auth/me after delete -> expect 401")
    r = client.get("/auth/me", headers=headers)
    assert r.status_code == 401, f"expected 401 after delete, got {r.status_code}: {r.text}"

    # Ledger rows should be gone too
    from app.db.session_sqlalchemy import session_scope
    from app.db.models import CreditLedgerRow
    from sqlalchemy import select as _select
    with session_scope() as session:
        rows = session.execute(
            _select(CreditLedgerRow).where(CreditLedgerRow.user_id == user_id)
        ).scalars().all()
        assert len(rows) == 0, f"ledger rows survived delete: {len(rows)}"

    print("ALL ASSERTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
