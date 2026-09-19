"""Team seats: multiple users sharing one subscription's credit wallet.

A *team* is owned by an active subscriber (the payer/admin). Members the owner
invites share the owner's credit pool, overage, and free-certificate benefit —
implemented by a single resolver, :func:`resolve_credit_user_id`, that maps a
non-owner member's credit operations to the team owner. Solo users and team
owners resolve to themselves, so the per-user credit core is untouched for them.

Documents stay owned by the real member who created them (tenant isolation is
per real user); only billing/credits are shared.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import require_user
from app.api.stripe_billing import (
    active_subscription_for,
    plan_seats,
)
from app.db.models import TeamInviteRow, TeamMemberRow, TeamRow, UserRow
from app.db.session_sqlalchemy import session_scope
from app.services.mailer import send_email

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/teams")


# ---------------------------------------------------------------------------
# Resolver (imported by credits.py and stripe_billing.py)
# ---------------------------------------------------------------------------


def resolve_credit_user_id(user_id: str) -> str:
    """Return the wallet owner for a user's credit operations.

    A non-owner team member resolves to the team owner (the shared wallet and
    payer). Solo users and team owners resolve to themselves. Never raises; on
    any error it falls back to ``user_id`` so credit ops degrade to per-user.

    The owner row is checked to still EXIST before we redirect anyone's wallet
    at it. A dangling ``owner_id`` used to send a live member's balance reads
    and spends to a user id with no row: the member saw 0 credits and an
    inactive subscription with no hint that their wallet had been orphaned.
    ``delete_me`` no longer leaves one behind, and this is the second lock.
    """
    try:
        with session_scope() as session:
            member = session.execute(
                select(TeamMemberRow).where(TeamMemberRow.user_id == user_id)
            ).scalar_one_or_none()
            if member is None:
                return user_id
            team = session.execute(
                select(TeamRow).where(TeamRow.id == member.team_id)
            ).scalar_one_or_none()
            if team is None or not team.owner_id:
                return user_id
            if team.owner_id != user_id:
                owner_exists = session.execute(
                    select(UserRow.id).where(UserRow.id == team.owner_id)
                ).scalar_one_or_none()
                if owner_exists is None:
                    return user_id
            return team.owner_id
    except Exception:  # pragma: no cover - defensive: never block a spend
        return user_id


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TeamMemberDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    userId: str
    email: Optional[str] = None
    displayName: Optional[str] = None
    role: str
    joinedAt: str
    isOwner: bool = False


class TeamInviteDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    email: str
    role: str
    status: str
    createdAt: str
    acceptUrl: Optional[str] = None


class TeamDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    ownerId: str
    seatLimit: int
    seatsUsed: int
    role: str  # the caller's role in the team
    members: List[TeamMemberDTO] = Field(default_factory=list)
    invites: List[TeamInviteDTO] = Field(default_factory=list)


class MyTeamResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team: Optional[TeamDTO] = None
    canCreate: bool = False  # caller has an active sub and is not already in a team


class CreateTeamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)


class InviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    role: str = Field(default="member")


class AcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=8, max_length=64)


class RemoveMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    userId: str = Field(min_length=1, max_length=128)


class RevokeInviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inviteId: str = Field(min_length=1, max_length=64)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _accept_url(token: str) -> str:
    import os

    base = (os.getenv("PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    return f"{base}/join?token={token}" if base else f"/join?token={token}"


# An invite link is a bearer credential: whoever holds the token and controls
# the address gets a seat on the owner's wallet. Forwarded invite mail, a
# shared inbox, a CC, browser history and chat logs all keep it readable
# forever, so it has to stop working on its own. Seven days is the usual span.
_INVITE_TTL = timedelta(days=7)


def _invite_is_expired(invite: TeamInviteRow, now: Optional[datetime] = None) -> bool:
    now = now or datetime.utcnow()
    if invite.created_at is None:
        return True
    return (now - invite.created_at) > _INVITE_TTL


def _expire_stale_invites(session, team_id: Optional[str] = None) -> int:
    """Flip aged pending invites to 'expired'. Returns how many.

    Swept on read rather than by a background job: an expired invite that
    stayed 'pending' would go on holding a seat that the owner paid for and
    would keep showing up in the team's invite list as if it were live.
    """
    q = select(TeamInviteRow).where(TeamInviteRow.status == "pending")
    if team_id is not None:
        q = q.where(TeamInviteRow.team_id == team_id)
    now = datetime.utcnow()
    swept = 0
    for invite in session.execute(q).scalars().all():
        if _invite_is_expired(invite, now):
            invite.status = "expired"
            swept += 1
    if swept:
        session.flush()
    return swept


def _seats_used(session, team_id: str) -> int:
    _expire_stale_invites(session, team_id)
    members = session.execute(
        select(TeamMemberRow).where(TeamMemberRow.team_id == team_id)
    ).scalars().all()
    pending = session.execute(
        select(TeamInviteRow).where(
            TeamInviteRow.team_id == team_id,
            TeamInviteRow.status == "pending",
        )
    ).scalars().all()
    return len(members) + len(pending)


def _team_dto(session, team: TeamRow, caller_id: str, include_invites: bool) -> TeamDTO:
    member_rows = session.execute(
        select(TeamMemberRow).where(TeamMemberRow.team_id == team.id)
    ).scalars().all()
    user_ids = [m.user_id for m in member_rows]
    users = {}
    if user_ids:
        for u in session.execute(select(UserRow).where(UserRow.id.in_(user_ids))).scalars().all():
            users[u.id] = u
    members = [
        TeamMemberDTO(
            userId=m.user_id,
            email=(users.get(m.user_id).email if users.get(m.user_id) else None),
            displayName=(users.get(m.user_id).display_name if users.get(m.user_id) else None),
            role=m.role,
            joinedAt=(m.created_at.isoformat() if m.created_at else ""),
            isOwner=(m.user_id == team.owner_id),
        )
        for m in member_rows
    ]
    members.sort(key=lambda x: (not x.isOwner, x.email or ""))

    caller_role = next((m.role for m in member_rows if m.user_id == caller_id), "member")

    invites: List[TeamInviteDTO] = []
    if include_invites:
        _expire_stale_invites(session, team.id)
        invite_rows = session.execute(
            select(TeamInviteRow).where(
                TeamInviteRow.team_id == team.id,
                TeamInviteRow.status == "pending",
            )
        ).scalars().all()
        invites = [
            TeamInviteDTO(
                id=i.id,
                email=i.email,
                role=i.role,
                status=i.status,
                createdAt=(i.created_at.isoformat() if i.created_at else ""),
                acceptUrl=_accept_url(i.token),
            )
            for i in invite_rows
        ]

    return TeamDTO(
        id=team.id,
        name=team.name,
        ownerId=team.owner_id,
        seatLimit=int(team.seat_limit),
        seatsUsed=_seats_used(session, team.id),
        role=caller_role,
        members=members,
        invites=invites,
    )


def _require_team_admin(session, caller_id: str) -> TeamRow:
    member = session.execute(
        select(TeamMemberRow).where(TeamMemberRow.user_id == caller_id)
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="no_team")
    team = session.execute(
        select(TeamRow).where(TeamRow.id == member.team_id)
    ).scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=404, detail="no_team")
    is_admin = member.role == "admin" or team.owner_id == caller_id
    if not is_admin:
        raise HTTPException(status_code=403, detail="team_admin_only")
    return team


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/me", response_model=MyTeamResponse)
async def my_team(user: UserRow = Depends(require_user)) -> MyTeamResponse:
    with session_scope() as session:
        member = session.execute(
            select(TeamMemberRow).where(TeamMemberRow.user_id == user.id)
        ).scalar_one_or_none()
        if member is None:
            can_create = active_subscription_for(user.id) is not None
            return MyTeamResponse(team=None, canCreate=can_create)
        team = session.execute(
            select(TeamRow).where(TeamRow.id == member.team_id)
        ).scalar_one_or_none()
        if team is None:
            return MyTeamResponse(team=None, canCreate=False)
        include_invites = member.role == "admin" or team.owner_id == user.id
        return MyTeamResponse(team=_team_dto(session, team, user.id, include_invites), canCreate=False)


@router.post("", response_model=TeamDTO)
async def create_team(payload: CreateTeamRequest, user: UserRow = Depends(require_user)) -> TeamDTO:
    sub = active_subscription_for(user.id)
    if sub is None:
        raise HTTPException(status_code=402, detail="subscription_required")
    seats = plan_seats(sub.plan)

    with session_scope() as session:
        existing = session.execute(
            select(TeamMemberRow).where(TeamMemberRow.user_id == user.id)
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=409, detail="already_in_team")

        now = datetime.utcnow()
        team_id = secrets.token_hex(8)
        session.add(
            TeamRow(
                id=team_id,
                name=payload.name.strip()[:160],
                owner_id=user.id,
                seat_limit=seats,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            TeamMemberRow(
                id=secrets.token_hex(8),
                team_id=team_id,
                user_id=user.id,
                role="admin",
                created_at=now,
            )
        )
        session.flush()
        team = session.execute(select(TeamRow).where(TeamRow.id == team_id)).scalar_one()
        return _team_dto(session, team, user.id, include_invites=True)


@router.post("/invite", response_model=TeamInviteDTO)
async def invite_member(payload: InviteRequest, user: UserRow = Depends(require_user)) -> TeamInviteDTO:
    email = payload.email.strip().lower()
    role = "admin" if payload.role == "admin" else "member"
    with session_scope() as session:
        team = _require_team_admin(session, user.id)

        if _seats_used(session, team.id) >= int(team.seat_limit):
            raise HTTPException(status_code=409, detail="seat_limit_reached")

        # Already a member?
        already = session.execute(
            select(TeamMemberRow, UserRow)
            .join(UserRow, UserRow.id == TeamMemberRow.user_id)
            .where(TeamMemberRow.team_id == team.id, UserRow.email == email)
        ).first()
        if already is not None:
            raise HTTPException(status_code=409, detail="already_member")

        # Existing pending invite for this email on this team?
        dup = session.execute(
            select(TeamInviteRow).where(
                TeamInviteRow.team_id == team.id,
                TeamInviteRow.email == email,
                TeamInviteRow.status == "pending",
            )
        ).scalar_one_or_none()
        if dup is not None:
            token = dup.token
            invite = dup
        else:
            token = secrets.token_urlsafe(24)[:64]
            invite = TeamInviteRow(
                id=secrets.token_hex(8),
                team_id=team.id,
                email=email,
                role=role,
                token=token,
                status="pending",
                invited_by=user.id,
                created_at=datetime.utcnow(),
            )
            session.add(invite)
            session.flush()

        dto = TeamInviteDTO(
            id=invite.id,
            email=invite.email,
            role=invite.role,
            status=invite.status,
            createdAt=(invite.created_at.isoformat() if invite.created_at else ""),
            acceptUrl=_accept_url(token),
        )
        team_name = team.name

    # Best-effort email (never blocks the response).
    try:
        send_email(
            to=email,
            subject=f"You're invited to the {team_name} team on 508 Agent",
            body=(
                f"{user.display_name or user.email} invited you to join the "
                f"\"{team_name}\" team on 508 Agent.\n\n"
                f"Accept your invite: {dto.acceptUrl}\n\n"
                f"If you don't have an account yet, sign up with this email "
                f"address ({email}) first and confirm it from the verification "
                f"email, then open the link.\n\n"
                f"This invite expires in {_INVITE_TTL.days} days."
            ),
        )
    except Exception:  # pragma: no cover
        pass

    return dto


@router.post("/accept", response_model=TeamDTO)
async def accept_invite(payload: AcceptRequest, user: UserRow = Depends(require_user)) -> TeamDTO:
    """Consume an invite token and take a seat on the team's shared wallet.

    Two things have to hold besides possessing the token, because a seat spends
    the owner's credits and — with overage on — the owner's card:

    - the invite must still be inside its TTL. It used to be a permanent
      credential: a link back-dated 900 days still granted a seat;
    - the accepting account must have PROVEN the invited address
      (``email_verified_at``). The email match alone was satisfiable by
      PATCHing your own profile onto the invited address — any address not
      already registered can be claimed, and the PATCH leaves verification
      null — so an unrelated account could squat the address and walk in.
      Verification is the proof of inbox control the flow was assuming; it is
      cleared on every email change, so a squatter cannot inherit it.
    """
    # Retire an aged link in its OWN transaction: the 410 below aborts the main
    # one, so a mark written there would roll straight back and the dead invite
    # would go on holding a seat the owner pays for.
    with session_scope() as session:
        aged = session.execute(
            select(TeamInviteRow).where(TeamInviteRow.token == payload.token)
        ).scalar_one_or_none()
        if aged is not None and aged.status == "pending" and _invite_is_expired(aged):
            aged.status = "expired"

    with session_scope() as session:
        invite = session.execute(
            select(TeamInviteRow).where(TeamInviteRow.token == payload.token)
        ).scalar_one_or_none()
        if invite is None:
            raise HTTPException(status_code=404, detail="invite_not_found")
        if invite.status == "expired":
            # Say so rather than 404: the holder already has the token, so this
            # reveals nothing, and "ask for a new link" is actionable.
            raise HTTPException(status_code=410, detail="invite_expired")
        if invite.status != "pending":
            raise HTTPException(status_code=404, detail="invite_not_found")

        # The invite is addressed to a specific email; enforce it matches.
        if (user.email or "").strip().lower() != invite.email.strip().lower():
            raise HTTPException(status_code=403, detail="invite_email_mismatch")

        # ...and that the address was actually proven, not merely typed.
        if user.email_verified_at is None:
            raise HTTPException(status_code=403, detail="email_verification_required")

        existing = session.execute(
            select(TeamMemberRow).where(TeamMemberRow.user_id == user.id)
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=409, detail="already_in_team")

        team = session.execute(
            select(TeamRow).where(TeamRow.id == invite.team_id)
        ).scalar_one_or_none()
        if team is None:
            raise HTTPException(status_code=404, detail="team_gone")

        # Seat check counts current members + other pending invites (excluding
        # this one, which is about to be consumed).
        members = session.execute(
            select(TeamMemberRow).where(TeamMemberRow.team_id == team.id)
        ).scalars().all()
        other_pending = session.execute(
            select(TeamInviteRow).where(
                TeamInviteRow.team_id == team.id,
                TeamInviteRow.status == "pending",
                TeamInviteRow.id != invite.id,
            )
        ).scalars().all()
        if len(members) + len(other_pending) >= int(team.seat_limit):
            raise HTTPException(status_code=409, detail="seat_limit_reached")

        now = datetime.utcnow()
        session.add(
            TeamMemberRow(
                id=secrets.token_hex(8),
                team_id=team.id,
                user_id=user.id,
                role=invite.role,
                created_at=now,
            )
        )
        invite.status = "accepted"
        invite.accepted_user_id = user.id
        invite.accepted_at = now
        session.flush()
        return _team_dto(session, team, user.id, include_invites=(invite.role == "admin"))


@router.post("/revoke-invite")
async def revoke_invite(payload: RevokeInviteRequest, user: UserRow = Depends(require_user)) -> dict:
    with session_scope() as session:
        team = _require_team_admin(session, user.id)
        invite = session.execute(
            select(TeamInviteRow).where(
                TeamInviteRow.id == payload.inviteId,
                TeamInviteRow.team_id == team.id,
            )
        ).scalar_one_or_none()
        if invite is None:
            raise HTTPException(status_code=404, detail="invite_not_found")
        if invite.status == "pending":
            invite.status = "revoked"
            session.flush()
    return {"ok": True}


@router.post("/remove")
async def remove_member(payload: RemoveMemberRequest, user: UserRow = Depends(require_user)) -> dict:
    with session_scope() as session:
        team = _require_team_admin(session, user.id)
        if payload.userId == team.owner_id:
            raise HTTPException(status_code=400, detail="cannot_remove_owner")
        member = session.execute(
            select(TeamMemberRow).where(
                TeamMemberRow.team_id == team.id,
                TeamMemberRow.user_id == payload.userId,
            )
        ).scalar_one_or_none()
        if member is None:
            raise HTTPException(status_code=404, detail="member_not_found")
        session.delete(member)
        session.flush()
    return {"ok": True}


@router.post("/leave")
async def leave_team(user: UserRow = Depends(require_user)) -> dict:
    with session_scope() as session:
        member = session.execute(
            select(TeamMemberRow).where(TeamMemberRow.user_id == user.id)
        ).scalar_one_or_none()
        if member is None:
            raise HTTPException(status_code=404, detail="no_team")
        team = session.execute(
            select(TeamRow).where(TeamRow.id == member.team_id)
        ).scalar_one_or_none()
        if team is not None and team.owner_id == user.id:
            # The owner can't leave their own team; they disband it instead.
            raise HTTPException(status_code=400, detail="owner_cannot_leave")
        session.delete(member)
        session.flush()
    return {"ok": True}


@router.delete("")
async def disband_team(user: UserRow = Depends(require_user)) -> dict:
    """Owner disbands the team: removes all members and pending invites."""
    with session_scope() as session:
        team = session.execute(
            select(TeamRow).where(TeamRow.owner_id == user.id)
        ).scalar_one_or_none()
        if team is None:
            raise HTTPException(status_code=404, detail="no_team")
        for m in session.execute(
            select(TeamMemberRow).where(TeamMemberRow.team_id == team.id)
        ).scalars().all():
            session.delete(m)
        for i in session.execute(
            select(TeamInviteRow).where(
                TeamInviteRow.team_id == team.id,
                TeamInviteRow.status == "pending",
            )
        ).scalars().all():
            i.status = "revoked"
        session.delete(team)
        session.flush()
    return {"ok": True}


__all__ = ["router", "resolve_credit_user_id"]
