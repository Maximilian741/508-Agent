"""Smoke test for the admin metrics dashboard endpoint.

Verifies access control (anonymous 401, non-admin 403, admin 200) and that the
aggregate reflects seeded data (a subscription, a certificate, a team, credits).

Usage:
    python -m app.devtools.smoke_admin_metrics
"""

from __future__ import annotations

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="508_smoke_admin_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/a.db"
# Mark one email as admin before the app/config is imported.
os.environ["ADMIN_EMAILS"] = "admin@example.com"

from datetime import datetime  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _signin(client: TestClient, email: str):
    r = client.post("/auth/sign-in", json={"email": email, "displayName": email.split("@")[0], "password": "adminpass123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["user"]["id"]


def main() -> int:
    client = TestClient(app)
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    admin_auth, _ = _signin(client, "admin@example.com")
    user_auth, user_id = _signin(client, "u1@example.com")

    # Access control.
    check("anonymous -> 401", client.get("/admin/metrics").status_code == 401)
    check("non-admin -> 403", client.get("/admin/metrics", headers=user_auth).status_code == 403)

    # Seed: a credit grant, an active subscription, a certificate, a team.
    client.post("/auth/grant-starter", headers=user_auth)  # +25

    from app.db.models import SubscriptionRow
    from app.db.session_sqlalchemy import session_scope

    with session_scope() as s:
        s.add(SubscriptionRow(
            id="sub_admin_u1", user_id=user_id, plan="team", status="active",
            stripe_customer_id="cus_u1", current_period_end=None, overage_enabled=False,
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ))

    r = client.post("/billing/issue-certificate", headers=user_auth, json={
        "filename": "u1.pdf", "conformanceClaim": "WCAG 2.1 AA", "score": 95,
        "fixedCount": 5, "remainingCount": 1,
    })
    check("seed certificate issued", r.status_code == 200)

    r = client.post("/teams", headers=user_auth, json={"name": "U1 Team"})
    check("seed team created", r.status_code == 200)

    # Admin can read metrics; aggregates reflect the seeded data.
    r = client.get("/admin/metrics", headers=admin_auth)
    check("admin -> 200", r.status_code == 200)
    m = r.json()

    check("users.total >= 2", m["users"]["total"] >= 2)
    check("subscriptions.active >= 1", m["subscriptions"]["active"] >= 1)
    check("subscriptions.byPlan has team", m["subscriptions"]["byPlan"].get("team", 0) >= 1)
    check("estimatedMrrUsd >= 49", m["subscriptions"]["estimatedMrrUsd"] >= 49)
    check("credits.granted >= 25", m["credits"]["granted"] >= 25)
    check("certificates.total >= 1", m["certificates"]["total"] >= 1)
    check("certificates.bySubscription >= 1", m["certificates"]["bySubscription"] >= 1)
    check("teams.count >= 1", m["teams"]["count"] >= 1)
    check("teams.seatsTotal >= 3", m["teams"]["seatsTotal"] >= 3)
    check("teams.seatsUsed >= 1", m["teams"]["seatsUsed"] >= 1)
    check("recentCertificates non-empty", len(m["recentCertificates"]) >= 1)
    check("generatedAt present", bool(m.get("generatedAt")))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
