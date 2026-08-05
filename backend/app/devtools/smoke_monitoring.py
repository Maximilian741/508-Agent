"""Smoke: scheduled monitoring — the DB lease, scheduling, and quiet alerting.

The load-bearing risk is DUPLICATE WORK. The backend runs
``uvicorn --workers 2``, so each worker has its own event loop and a naive
"scan what's due" loop would fire every monitor twice — duplicate crawls of a
customer's site and duplicate alert emails. The lease must make that impossible.

This pins:
  * two workers racing for the same due monitor -> EXACTLY ONE wins
  * a claimed monitor is invisible to other workers until released
  * a crashed worker's stale lease is reclaimable (no monitor stranded forever)
  * a released monitor is rescheduled by its frequency, not re-run immediately
  * repeated failures disable the monitor instead of hammering the site forever
  * alerts fire ONLY on NEW issues (a quiet alerting product is a used one)
  * SSRF validation happens at CREATE time, so an internal URL can never be
    parked in the scheduler for a background worker to fetch later

Usage:
    python -m app.devtools.smoke_monitoring
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_mon_')}/s.db")

from datetime import datetime, timedelta  # noqa: E402

from app.db import models as _models  # noqa: F401,E402
from app.db.base import Base  # noqa: E402
from app.db.models import MonitoredSiteRow  # noqa: E402
from app.db.session_sqlalchemy import ENGINE, session_scope  # noqa: E402
from app.services.monitoring import (  # noqa: E402
    STALE_LEASE_SECONDS,
    claim_due_monitor,
    maybe_send_alert,
    next_run_from,
    release_monitor,
)


def _add_monitor(user="u1", url="https://example.com/a", due=True, frequency="daily") -> str:
    mid = uuid.uuid4().hex
    now = datetime.utcnow()
    with session_scope() as s:
        s.add(
            MonitoredSiteRow(
                id=mid,
                user_id=user,
                url=url,
                frequency=frequency,
                enabled=True,
                notify_email="owner@example.com",
                created_at=now,
                next_run_at=now - timedelta(minutes=1) if due else now + timedelta(days=1),
            )
        )
    return mid


def _row(mid: str):
    with session_scope() as s:
        r = s.get(MonitoredSiteRow, mid)
        return {
            "claimed_by": r.claimed_by,
            "claimed_at": r.claimed_at,
            "next_run_at": r.next_run_at,
            "enabled": bool(r.enabled),
            "failures": int(r.consecutive_failures or 0),
            "status": r.last_status,
            "issues": int(r.last_issue_count or 0),
        }


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    Base.metadata.create_all(bind=ENGINE)

    # ---- THE critical property: two racing workers, exactly one winner ----
    mid = _add_monitor()
    a = claim_due_monitor(token="worker-A")
    b = claim_due_monitor(token="worker-B")
    check("worker A claims the due monitor", a is not None and a["id"] == mid, str(a))
    check("worker B gets NOTHING (no duplicate scan, no duplicate email)", b is None, str(b))
    check("the lease records the winning worker", _row(mid)["claimed_by"] == "worker-A")

    # Many workers racing at once: still exactly one total winner.
    mid2 = _add_monitor(url="https://example.com/b")
    winners = [claim_due_monitor(token=f"w{i}") for i in range(6)]
    got = [w for w in winners if w is not None and w["id"] == mid2]
    check("6 racing workers -> exactly one claims it", len(got) == 1, f"{len(got)} winners")

    # ---- a crashed worker must not strand a monitor ----
    with session_scope() as s:
        r = s.get(MonitoredSiteRow, mid)
        r.claimed_at = datetime.utcnow() - timedelta(seconds=STALE_LEASE_SECONDS + 60)
    reclaimed = claim_due_monitor(token="worker-C")
    check("a stale lease is reclaimable (crashed worker doesn't strand it)",
          reclaimed is not None and reclaimed["id"] == mid, str(reclaimed))

    # ---- release reschedules instead of re-running immediately ----
    before = datetime.utcnow()
    release_monitor(mid, frequency="daily", status="12 issue(s), grade B", issue_count=12)
    row = _row(mid)
    check("release clears the lease", row["claimed_by"] is None)
    check("release records the run result", row["issues"] == 12 and "grade B" in row["status"], str(row))
    check("release schedules the NEXT run ~1 day out (not immediately)",
          row["next_run_at"] > before + timedelta(hours=23), str(row["next_run_at"]))
    check("a rescheduled monitor is no longer due", claim_due_monitor(token="worker-D") is None
          or claim_due_monitor(token="worker-D")["id"] != mid)

    check("weekly frequency schedules ~7 days out",
          next_run_from("weekly") > datetime.utcnow() + timedelta(days=6))
    check("unknown frequency falls back to the weekly default",
          next_run_from("nonsense") > datetime.utcnow() + timedelta(days=6))

    # ---- repeated failures disable rather than hammer the site ----
    mid3 = _add_monitor(url="https://example.com/c")
    for _ in range(5):
        with session_scope() as s:
            s.get(MonitoredSiteRow, mid3).next_run_at = datetime.utcnow() - timedelta(minutes=1)
        claim_due_monitor(token="worker-E")
        release_monitor(mid3, frequency="daily", status="Could not reach that URL.", failed=True)
    r3 = _row(mid3)
    check("5 consecutive failures disables the monitor (stop hammering)", r3["enabled"] is False, str(r3))
    check("a disabled monitor is never claimed again", claim_due_monitor(token="worker-F") is None
          or claim_due_monitor(token="worker-F")["id"] != mid3)

    # ---- alerting is QUIET: only genuinely new issues ----
    sent = {"n": 0, "subject": "", "to": ""}

    import app.services.mailer as _mailer

    real_send = _mailer.send_email

    def fake_send(to, subject, body):
        sent["n"] += 1
        sent["to"], sent["subject"] = to, subject
        return True

    _mailer.send_email = fake_send
    try:
        mon = {"url": "https://example.com/a", "notify_email": "owner@example.com"}
        check("no alert when the check FAILED",
              not maybe_send_alert(mon, {"ok": False, "status": "unreachable"}))
        check("no alert when nothing is new",
              not maybe_send_alert(mon, {"ok": True, "issueCount": 9, "changes": {"newIssues": 0, "resolvedIssues": 4}}))
        check("no alert on a first-ever scan (no previous scan to compare)",
              not maybe_send_alert(mon, {"ok": True, "issueCount": 9, "changes": None}))
        check("no alert when the owner set no email",
              not maybe_send_alert({"url": "x", "notify_email": ""},
                                   {"ok": True, "changes": {"newIssues": 3}}))
        check("emails sent so far: zero", sent["n"] == 0, str(sent))
        alerted = maybe_send_alert(
            mon,
            {"ok": True, "url": "https://example.com/a", "issueCount": 12, "score": 70.0,
             "grade": "C", "changes": {"newIssues": 3, "resolvedIssues": 1}},
        )
        check("ALERTS when new issues appear", alerted and sent["n"] == 1, str(sent))
        check("alert goes to the owner", sent["to"] == "owner@example.com", str(sent))
        check("alert subject states the new-issue count", "3 new" in sent["subject"], sent["subject"])
    finally:
        _mailer.send_email = real_send

    # ---- SSRF is rejected at CREATE time, not just at fetch time ----
    from app.security.url_fetch import SsrfError, validate_scan_target

    # The create-time gate must run BOTH checks a live fetch would — scheme AND
    # resolved IP — otherwise an internal address could be parked in the
    # scheduler and fetched later by a background worker with nobody watching.
    for bad in (
        "http://127.0.0.1/",
        "http://169.254.169.254/",     # cloud metadata
        "http://10.0.0.1/",
        "http://[::1]/",
        "file:///etc/passwd",
        "http://user:pass@8.8.8.8/",
    ):
        try:
            validate_scan_target(bad)
            check(f"create-time gate rejects {bad}", False, "did not raise")
        except SsrfError:
            check(f"create-time gate rejects {bad}", True)
        except Exception as exc:
            check(f"create-time gate rejects {bad}", False, repr(exc))

    # ---- per-user isolation ----
    from app.services.monitoring import list_monitors

    check("monitors are per-user", all(m["url"] != "https://example.com/a" for m in list_monitors("someone-else")))
    check("owner sees their own monitors", len(list_monitors("u1")) >= 1)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
