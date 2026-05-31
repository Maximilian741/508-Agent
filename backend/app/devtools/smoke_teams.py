"""Smoke test for team seats (shared subscription wallet).

Exercises: create team (subscriber only), invite + accept, shared-wallet spend
(a member's spend draws on the owner's balance), free certificates for members,
seat-limit enforcement, invite email-match guard, remove, and owner-can't-leave.

Usage:
    python -m app.devtools.smoke_teams
"""

from __future__ import annotations

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="508_smoke_teams_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/t.db"

from datetime import datetime  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _signin(client: TestClient, email: str):
    r = client.post("/auth/sign-in", json={"email": email, "displayName": email.split("@")[0], "password": "teampass123"})
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

    def bal(auth: dict) -> int:
        return client.get("/credits/balance", headers=auth).json()["balance"]

    owner_auth, owner_id = _signin(client, "owner@example.com")

    # A non-subscriber cannot create a team.
    nosub_auth, _ = _signin(client, "nosub@example.com")
    r = client.post("/teams", headers=nosub_auth, json={"name": "Nope"})
    check("non-subscriber create team -> 402", r.status_code == 402)

    # Give the owner an active subscription (overage OFF so spends draw cleanly).
    from app.db.models import SubscriptionRow
    from app.db.session_sqlalchemy import session_scope

    with session_scope() as s:
        s.add(SubscriptionRow(
            id="sub_team_owner", user_id=owner_id, plan="team", status="active",
            stripe_customer_id="cus_team", current_period_end=None, overage_enabled=False,
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ))

    # Owner creates the team.
    r = client.post("/teams", headers=owner_auth, json={"name": "Acme Accessibility"})
    check("create team -> 200", r.status_code == 200)
    team = r.json()
    check("seat limit from team plan == 3", team.get("seatLimit") == 3)
    check("owner is sole member", team.get("seatsUsed") == 1 and len(team.get("members", [])) == 1)
    check("caller role is admin", team.get("role") == "admin")

    # Invite a member.
    r = client.post("/teams/invite", headers=owner_auth, json={"email": "member@example.com"})
    check("invite member -> 200", r.status_code == 200)
    accept_url = r.json().get("acceptUrl", "")
    token = accept_url.split("token=")[-1]
    check("invite returns a token", bool(token))

    # Wrong user can't accept someone else's invite.
    member_auth, member_id = _signin(client, "member@example.com")
    other_auth, _ = _signin(client, "intruder@example.com")
    r = client.post("/teams/accept", headers=other_auth, json={"token": token})
    check("invite email mismatch -> 403", r.status_code == 403)

    # The invited member accepts.
    r = client.post("/teams/accept", headers=member_auth, json={"token": token})
    check("accept invite -> 200", r.status_code == 200)
    check("team now has 2 members", r.json().get("seatsUsed") == 2)

    # Shared wallet: fund the OWNER, then the member spends and it draws on the
    # owner's balance.
    client.post("/auth/grant-starter", headers=owner_auth)  # +25 to owner
    check("member sees shared (owner) balance = 25", bal(member_auth) == 25)
    r = client.post("/credits/spend", headers=member_auth, json={"amount": 10, "description": "remediate"})
    check("member spend on shared wallet -> 200", r.status_code == 200)
    check("owner balance reduced to 15", bal(owner_auth) == 15)
    check("member balance reflects shared 15", bal(member_auth) == 15)

    # Certificates are free for members (owner has an active subscription).
    r = client.post("/billing/issue-certificate", headers=member_auth, json={
        "filename": "report.pdf", "conformanceClaim": "WCAG 2.1 AA", "score": 98,
        "fixedCount": 12, "remainingCount": 0,
    })
    check("member certificate -> 200", r.status_code == 200)
    check("member certificate is subscription-free", r.json().get("paidWith") == "subscription")
    check("free certificate did not touch shared balance", bal(owner_auth) == 15)

    # Seat-limit enforcement: owner(1) + member(1) = 2 used; one more invite fills
    # the 3rd seat, a 4th is rejected.
    r = client.post("/teams/invite", headers=owner_auth, json={"email": "m2@example.com"})
    check("invite #2 (fills seat 3) -> 200", r.status_code == 200)
    r = client.post("/teams/invite", headers=owner_auth, json={"email": "m3@example.com"})
    check("invite beyond seat limit -> 409", r.status_code == 409)

    # Owner cannot leave their own team.
    r = client.post("/teams/leave", headers=owner_auth)
    check("owner cannot leave -> 400", r.status_code == 400)

    # Remove the member; their wallet resolution reverts to their own (empty) balance.
    r = client.post("/teams/remove", headers=owner_auth, json={"userId": member_id})
    check("remove member -> 200", r.status_code == 200)
    check("removed member sees own balance (0)", bal(member_auth) == 0)
    check("owner balance unaffected by removal", bal(owner_auth) == 15)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
