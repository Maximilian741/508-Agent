"""Smoke: a team invite is a short-lived credential for a PROVEN address.

A seat on a team spends the OWNER's credit wallet and, with overage on, the
owner's card. The invite link was the only thing guarding that, and it was a
permanent bearer credential: no TTL at all (a link back-dated 900 days still
granted a seat), and the address it was addressed to could be taken by any
account simply PATCHing its own profile onto it — any address that isn't
already registered can be claimed, and an email change leaves
``emailVerifiedAt`` null, so nothing in the flow ever required control of the
inbox. Forwarded invite mail, a shared inbox, a CC or a years-old chat log
were all still-live keys to someone else's wallet.

Pins:

1. The email match still holds: an unrelated address is refused (403).
2. An UNVERIFIED account on the invited address is refused
   (403 email_verification_required) — squatting the address is not proof.
3. Verifying the address makes the same token work.
4. An invite past its TTL is refused (410 invite_expired) and is marked
   'expired' rather than left pending.
5. An expired invite stops holding a paid seat, and the owner can re-invite.
6. Verification is cleared by an email change, so the seat can't be inherited:
   a member who moves their account off the invited address and back cannot
   re-accept without proving the address again.

Usage:
    python -m app.devtools.smoke_team_invites
"""

from __future__ import annotations

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="508_smoke_invites_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/i.db"
os.environ.pop("SMTP_HOST", None)
# One rate-limit bucket per section; only read when a proxy is declared.
os.environ["TRUST_PROXY_HEADERS"] = "true"

from datetime import datetime, timedelta  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.api.teams import _INVITE_TTL  # noqa: E402
from app.db.models import (  # noqa: E402
    EmailVerifyTokenRow,
    SubscriptionRow,
    TeamInviteRow,
)
from app.db.session_sqlalchemy import session_scope  # noqa: E402
from app.main import app  # noqa: E402


def _age_invite(token: str, days: float) -> None:
    """Back-date an invite so its TTL can be exercised without waiting."""
    with session_scope() as s:
        row = s.execute(select(TeamInviteRow).where(TeamInviteRow.token == token)).scalar_one()
        row.created_at = datetime.utcnow() - timedelta(days=days)


def _invite_status(token: str) -> str:
    with session_scope() as s:
        return s.execute(
            select(TeamInviteRow.status).where(TeamInviteRow.token == token)
        ).scalars().one()


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, detail: object = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, "" if cond else f"  [{detail}]")
        if not cond:
            failures += 1

    def client(ip: str) -> TestClient:
        return TestClient(app, headers={"X-Forwarded-For": ip})

    def signin(c: TestClient, email: str, password: str = "invitepass123"):
        r = c.post("/auth/sign-in", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["user"]["id"]

    def verify(c: TestClient, auth: dict) -> None:
        uid = c.get("/auth/me", headers=auth).json()["id"]
        assert c.post("/auth/request-verify-email", headers=auth).status_code == 200
        with session_scope() as s:
            link = s.execute(
                select(EmailVerifyTokenRow.token).where(EmailVerifyTokenRow.user_id == uid)
            ).scalars().first()
        assert c.get("/auth/verify-email", params={"token": link}).status_code == 200

    def token_of(resp: dict) -> str:
        return resp["acceptUrl"].split("token=")[-1]

    c = client("10.5.0.1")
    owner_auth, owner_id = signin(c, "owner@example.com")
    with session_scope() as s:
        s.add(SubscriptionRow(
            id="sub_invites", user_id=owner_id, plan="team", status="active",
            stripe_customer_id="cus_inv", current_period_end=None, overage_enabled=False,
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ))
    team = c.post("/teams", headers=owner_auth, json={"name": "Acme"}).json()
    check("team created, seat limit 3", team.get("seatLimit") == 3, team)

    inv = c.post("/teams/invite", headers=owner_auth, json={"email": "colleague@example.com"}).json()
    tok = token_of(inv)
    check("invite issued for colleague@example.com", inv.get("email") == "colleague@example.com", inv)

    # --- 1/2. the address has to be right AND proven -------------------------
    mal_auth, _ = signin(c, "mallory@example.com")
    r = c.post("/teams/accept", headers=mal_auth, json={"token": tok})
    check("an unrelated address is refused -> 403", r.status_code == 403 and "mismatch" in r.text, r.text)

    r = c.patch("/auth/me", headers=mal_auth, json={"email": "colleague@example.com", "currentPassword": "invitepass123"})
    check("the squatter CAN take the unregistered address", r.status_code == 200, r.text)
    check("...but the address is not verified by taking it", r.json().get("emailVerifiedAt") is None, r.json())
    mal_auth = {"Authorization": f"Bearer {r.json()['token']}"}
    r = c.post("/teams/accept", headers=mal_auth, json={"token": tok})
    check(
        "an UNVERIFIED squatter cannot take the seat -> 403",
        r.status_code == 403 and "email_verification_required" in r.text,
        r.text,
    )
    check("the invite is still pending for the real invitee", _invite_status(tok) == "pending", _invite_status(tok))

    # --- 3. proving the address makes the same token work --------------------
    verify(c, mal_auth)
    r = c.post("/teams/accept", headers=mal_auth, json={"token": tok})
    check("a verified holder of the address accepts -> 200", r.status_code == 200, r.text)
    check("the invite is consumed", _invite_status(tok) == "accepted", _invite_status(tok))

    # --- 4. the TTL ----------------------------------------------------------
    c = client("10.5.0.2")
    old = c.post("/teams/invite", headers=owner_auth, json={"email": "late@example.com"}).json()
    old_tok = token_of(old)
    late_auth, _ = signin(c, "late@example.com")
    verify(c, late_auth)

    _age_invite(old_tok, _INVITE_TTL.days + 1)
    r = c.post("/teams/accept", headers=late_auth, json={"token": old_tok})
    check("an invite past its TTL is refused -> 410", r.status_code == 410 and "invite_expired" in r.text, r.text)
    check("the expired invite is marked, not left pending", _invite_status(old_tok) == "expired", _invite_status(old_tok))

    r = c.post("/teams/accept", headers=late_auth, json={"token": old_tok})
    check("a second try keeps saying expired, not 200", r.status_code == 410, r.status_code)
    r = c.post("/teams/accept", headers=late_auth, json={"token": "no-such-invite-token"})
    check("an unknown token is a plain 404", r.status_code == 404, r.status_code)

    # A fresh invite of the same age minus a day is still good.
    fresh = c.post("/teams/invite", headers=owner_auth, json={"email": "late@example.com"}).json()
    fresh_tok = token_of(fresh)
    check("the owner can re-invite the same address", fresh_tok != old_tok, (old_tok, fresh_tok))
    _age_invite(fresh_tok, _INVITE_TTL.days - 1)
    r = c.post("/teams/accept", headers=late_auth, json={"token": fresh_tok})
    check("an invite still inside the TTL works -> 200", r.status_code == 200, r.text)

    # --- 5. an expired invite stops holding a seat ---------------------------
    # Seats: owner + colleague + late = 3 of 3. One more must be refused...
    c = client("10.5.0.3")
    r = c.post("/teams/invite", headers=owner_auth, json={"email": "fourth@example.com"})
    check("a 4th seat is refused while all 3 are held -> 409", r.status_code == 409, r.status_code)
    # ...but a seat held only by an EXPIRED invite must come back. Free one by
    # removing a member, invite, expire it, and confirm the seat is reusable.
    late_id = [m["userId"] for m in c.get("/teams/me", headers=owner_auth).json()["team"]["members"]
               if m["email"] == "late@example.com"][0]
    check("free a seat by removing a member", c.post("/teams/remove", headers=owner_auth, json={"userId": late_id}).status_code == 200)
    held = c.post("/teams/invite", headers=owner_auth, json={"email": "ghost-invitee@example.com"}).json()
    check(
        "the pending invite occupies the freed seat",
        c.post("/teams/invite", headers=owner_auth, json={"email": "another@example.com"}).status_code == 409,
    )
    _age_invite(token_of(held), _INVITE_TTL.days + 1)
    r = c.post("/teams/invite", headers=owner_auth, json={"email": "another@example.com"})
    check("once it expires the seat is reusable -> 200", r.status_code == 200, r.text)
    reused_invite_id = r.json().get("id")
    check("the swept invite is marked expired", _invite_status(token_of(held)) == "expired", _invite_status(token_of(held)))
    listed = c.get("/teams/me", headers=owner_auth).json()["team"]["invites"]
    check(
        "an expired invite no longer shows as pending to the owner",
        all(i["email"] != "ghost-invitee@example.com" for i in listed),
        listed,
    )

    # --- 6. verification cannot be inherited through an email change ---------
    c = client("10.5.0.4")
    # Free the seat that invite is holding so the last section can use it.
    check(
        "the owner revokes the reused invite to free the seat",
        c.post("/teams/revoke-invite", headers=owner_auth, json={"inviteId": reused_invite_id}).status_code == 200,
    )
    drifter_auth, _ = signin(c, "drifter@example.com")
    verify(c, drifter_auth)
    check("the drifter is verified to start", c.get("/auth/me", headers=drifter_auth).json()["emailVerifiedAt"] is not None)
    r = c.patch("/auth/me", headers=drifter_auth, json={"email": "elsewhere@example.com", "currentPassword": "invitepass123"})
    drifter_auth = {"Authorization": f"Bearer {r.json()['token']}"}
    check("moving the account clears verification", r.json().get("emailVerifiedAt") is None, r.json())
    inv2 = c.post("/teams/invite", headers=owner_auth, json={"email": "elsewhere@example.com"}).json()
    r = c.post("/teams/accept", headers=drifter_auth, json={"token": token_of(inv2)})
    check(
        "the moved account must prove the NEW address before taking a seat",
        r.status_code == 403 and "email_verification_required" in r.text,
        r.text,
    )
    verify(c, drifter_auth)
    r = c.post("/teams/accept", headers=drifter_auth, json={"token": token_of(inv2)})
    check("once proven, the seat is granted", r.status_code == 200, r.text)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
