"""Smoke: /metrics/overview accessibility metrics dashboard.

Exercises the server-backed, team-aware metrics aggregation:
  * headline totals (docs, issues found/fixed/pending, avg score, auto-fix %),
  * by-format and by-grade breakdowns,
  * a zero-filled 12-week timeline,
  * TENANT ISOLATION — a solo user never sees another user's documents,
  * TEAM SCOPING — a team member's dashboard spans the whole team,
  * empty state — a brand-new user gets zeros, not an error.

Read-only endpoint: it never writes or charges. We seed AnalysisResultRow (and
team rows) directly, then call the real HTTP route via TestClient.

Usage:
    python -m app.devtools.smoke_metrics
"""

from __future__ import annotations

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="508_smoke_metrics_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/m.db"

from datetime import datetime, timedelta  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _signin(client: TestClient, email: str):
    r = client.post(
        "/auth/sign-in",
        json={"email": email, "displayName": email.split("@")[0], "password": "metricspass123"},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["user"]["id"]


def _seed_result(user_id, doc_id, *, fmt, initial, fixed, pending, score, grade, when):
    from app.db.models import AnalysisResultRow
    from app.db.session_sqlalchemy import session_scope

    with session_scope() as session:
        session.add(
            AnalysisResultRow(
                id=f"{user_id}::{doc_id}",
                user_id=user_id,
                document_id=doc_id,
                filename=f"{doc_id}.{fmt}",
                source_format=fmt,
                initial_issues=initial,
                fixed_automatically=fixed,
                pending_manual=pending,
                score=score,
                grade=grade,
                created_at=when,
                updated_at=when,
            )
        )


def _seed_team(team_id, owner_id, member_ids):
    from app.db.models import TeamMemberRow, TeamRow
    from app.db.session_sqlalchemy import session_scope

    with session_scope() as session:
        session.add(TeamRow(id=team_id, name="Acme Agency", owner_id=owner_id, seat_limit=10))
        for i, uid in enumerate(member_ids):
            session.add(
                TeamMemberRow(
                    id=f"{team_id}-m{i}",
                    team_id=team_id,
                    user_id=uid,
                    role=("owner" if uid == owner_id else "member"),
                )
            )


def main() -> int:
    client = TestClient(app)
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    now = datetime.utcnow()
    a_auth, a_id = _signin(client, "a@example.com")
    b_auth, b_id = _signin(client, "b@example.com")
    c_auth, c_id = _signin(client, "c@example.com")
    d_auth, d_id = _signin(client, "d@example.com")  # stays empty

    # User A: two docs (one this week, one 3 weeks ago for the timeline).
    _seed_result(a_id, "a1", fmt="docx", initial=10, fixed=6, pending=4, score=80, grade="B", when=now)
    _seed_result(a_id, "a2", fmt="pdf", initial=4, fixed=4, pending=0, score=90, grade="A", when=now - timedelta(weeks=3))
    # User B: one doc (must never appear in A's totals).
    _seed_result(b_id, "b1", fmt="pptx", initial=20, fixed=5, pending=15, score=50, grade="F", when=now)
    # User C: one doc (will become A's teammate later).
    _seed_result(c_id, "c1", fmt="html", initial=2, fixed=2, pending=0, score=95, grade="A", when=now)

    # --- A's solo dashboard ---
    r = client.get("/metrics/overview", headers=a_auth)
    check("A overview 200", r.status_code == 200)
    m = r.json()
    t = m["totals"]
    check("A docs == 2", t["documentsAnalyzed"] == 2)
    check("A issuesFound == 14", t["issuesFound"] == 14)
    check("A issuesAutoFixed == 10", t["issuesAutoFixed"] == 10)
    check("A pending == 4", t["issuesPendingManual"] == 4)
    check("A avgScore == 85", t["avgScore"] == 85)
    check("A autoFixablePct == 71", t["autoFixablePct"] == 71)  # round(100*10/14)
    fmts = {f["format"]: f for f in m["byFormat"]}
    check("A byFormat has docx+pdf only", set(fmts) == {"docx", "pdf"})
    check("A pdf avgScore == 90", fmts.get("pdf", {}).get("avgScore") == 90)
    grades = {g["grade"]: g["documents"] for g in m["byGrade"]}
    check("A byGrade A=1,B=1", grades.get("A") == 1 and grades.get("B") == 1)
    check("A timeline has 12 weeks", len(m["timeline"]) == 12)
    check("A timeline doc total == 2", sum(p["documents"] for p in m["timeline"]) == 2)
    check("A scope self", m["scope"]["kind"] == "self" and m["scope"]["memberCount"] == 1)
    check("A recent has 2", len(m["recentDocuments"]) == 2)

    # --- Tenant isolation: B sees only its own doc ---
    r = client.get("/metrics/overview", headers=b_auth)
    mb = r.json()
    check("B docs == 1 (isolation)", mb["totals"]["documentsAnalyzed"] == 1)
    check("B issuesFound == 20 (only B)", mb["totals"]["issuesFound"] == 20)
    check("B does not see A/C formats", {f["format"] for f in mb["byFormat"]} == {"pptx"})

    # --- Team scoping: put A + C in one team (C owner) ---
    _seed_team("team-acme", owner_id=c_id, member_ids=[c_id, a_id])
    r = client.get("/metrics/overview", headers=a_auth)
    mt = r.json()
    check("A(team) scope team", mt["scope"]["kind"] == "team")
    check("A(team) teamName", mt["scope"]["teamName"] == "Acme Agency")
    check("A(team) memberCount == 2", mt["scope"]["memberCount"] == 2)
    check("A(team) docs == 3 (A2 + C1)", mt["totals"]["documentsAnalyzed"] == 3)
    check("A(team) formats == docx,pdf,html", {f["format"] for f in mt["byFormat"]} == {"docx", "pdf", "html"})
    # B is still NOT in the team -> still isolated.
    r = client.get("/metrics/overview", headers=b_auth)
    check("B still solo after team formed", client.get("/metrics/overview", headers=b_auth).json()["totals"]["documentsAnalyzed"] == 1)

    # --- Empty state: brand-new user D ---
    r = client.get("/metrics/overview", headers=d_auth)
    md = r.json()
    check("D empty docs == 0", md["totals"]["documentsAnalyzed"] == 0)
    check("D empty byFormat == []", md["byFormat"] == [])
    check("D timeline still 12 zeros", len(md["timeline"]) == 12 and sum(p["documents"] for p in md["timeline"]) == 0)
    check("D autoFixablePct == 0 (no div-by-zero)", md["totals"]["autoFixablePct"] == 0)

    # --- Auth required ---
    check("unauthenticated -> 401", client.get("/metrics/overview").status_code == 401)

    print("SMOKE METRICS:", "PASS" if failures == 0 else f"FAIL ({failures})")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
