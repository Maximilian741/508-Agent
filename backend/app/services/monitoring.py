"""Scheduled re-scans with regression alerts.

Turns a one-off scan into a standing watch: we re-check a URL on a schedule and
email the owner only when something NEW breaks. Built on the pieces that already
exist and are tested — the SSRF-hardened fetcher, the analyzers, the
content-derived fingerprints in :mod:`app.services.scan_history`, and the
pluggable mailer.

MULTI-WORKER SAFETY (the load-bearing detail)
---------------------------------------------
The backend runs ``uvicorn --workers 2``. Every worker gets its own event loop,
so a naive "wake up and scan what's due" loop fires the same monitor twice —
duplicate crawls of a customer's site and, worse, duplicate alert emails.

``claim_due_monitor`` therefore takes a LEASE with a single atomic conditional
UPDATE (``WHERE id = ... AND next_run_at <= now AND (claimed_by IS NULL OR
claimed_at < stale_cutoff)``) and proceeds only if it changed exactly one row.
The database arbitrates, so exactly one worker wins regardless of how many are
racing — and this works identically on SQLite and Postgres (no ``SELECT FOR
UPDATE`` required). A lease older than ``STALE_LEASE_SECONDS`` is reclaimable so
a crashed worker can't strand a monitor forever.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import or_

logger = logging.getLogger(__name__)

FREQUENCY_INTERVALS = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}
DEFAULT_FREQUENCY = "weekly"
STALE_LEASE_SECONDS = 900          # a lease older than this is assumed crashed
MAX_CONSECUTIVE_FAILURES = 5       # auto-disable a permanently broken monitor
MAX_MONITORS_PER_USER = 25         # abuse bound: we fetch these on a schedule

# This worker's identity for the lease. Unique per process.
WORKER_TOKEN = uuid.uuid4().hex[:16]


def next_run_from(frequency: str, now: Optional[datetime] = None) -> datetime:
    now = now or datetime.utcnow()
    return now + FREQUENCY_INTERVALS.get(frequency, FREQUENCY_INTERVALS[DEFAULT_FREQUENCY])


def claim_due_monitor(now: Optional[datetime] = None, token: str = WORKER_TOKEN) -> Optional[Dict[str, Any]]:
    """Atomically claim ONE monitor that is due. Returns its data, or None.

    The claim is a single conditional UPDATE, so concurrent workers cannot both
    win the same row — the database decides. See the module docstring.
    """
    now = now or datetime.utcnow()
    stale_cutoff = now - timedelta(seconds=STALE_LEASE_SECONDS)
    try:
        from app.db.models import MonitoredSiteRow
        from app.db.session_sqlalchemy import session_scope

        with session_scope() as session:
            candidates = (
                session.query(MonitoredSiteRow)
                .filter(
                    MonitoredSiteRow.enabled.is_(True),
                    MonitoredSiteRow.next_run_at <= now,
                )
                .order_by(MonitoredSiteRow.next_run_at.asc())
                .limit(10)
                .all()
            )
            for row in candidates:
                # Atomic compare-and-swap: only the worker whose UPDATE matches
                # the free/stale predicate gets rowcount 1.
                claimed = (
                    session.query(MonitoredSiteRow)
                    .filter(
                        MonitoredSiteRow.id == row.id,
                        MonitoredSiteRow.enabled.is_(True),
                        MonitoredSiteRow.next_run_at <= now,
                        or_(
                            MonitoredSiteRow.claimed_by.is_(None),
                            MonitoredSiteRow.claimed_at < stale_cutoff,
                        ),
                    )
                    .update(
                        {"claimed_by": token, "claimed_at": now},
                        synchronize_session=False,
                    )
                )
                if claimed == 1:
                    session.flush()
                    return {
                        "id": row.id,
                        "user_id": row.user_id,
                        "url": row.url,
                        "frequency": row.frequency,
                        "notify_email": row.notify_email,
                        "last_issue_count": int(row.last_issue_count or 0),
                    }
        return None
    except Exception as exc:
        logger.warning("claim_due_monitor failed: %s", exc)
        return None


def release_monitor(
    monitor_id: str,
    *,
    frequency: str,
    status: str,
    issue_count: Optional[int] = None,
    failed: bool = False,
    token: str = WORKER_TOKEN,
) -> None:
    """Finish a run: schedule the next one and drop the lease. Never raises."""
    try:
        from app.db.models import MonitoredSiteRow
        from app.db.session_sqlalchemy import session_scope

        now = datetime.utcnow()
        with session_scope() as session:
            row = session.get(MonitoredSiteRow, monitor_id)
            if row is None:
                return
            row.last_run_at = now
            row.last_status = (status or "")[:200]
            row.next_run_at = next_run_from(frequency, now)
            row.claimed_by = None
            row.claimed_at = None
            if issue_count is not None:
                row.last_issue_count = int(issue_count)
            if failed:
                row.consecutive_failures = int(row.consecutive_failures or 0) + 1
                if row.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    # Stop hammering a URL that keeps failing; the user can
                    # re-enable it once the site is reachable again.
                    row.enabled = False
                    row.last_status = f"Disabled after {row.consecutive_failures} failed checks."[:200]
            else:
                row.consecutive_failures = 0
    except Exception as exc:
        logger.warning("release_monitor failed: %s", exc)


def run_monitor_check(monitor: Dict[str, Any]) -> Dict[str, Any]:
    """Re-scan one monitored URL and diff it against the previous scan.

    Returns a result dict; never raises. Reuses the SSRF-hardened fetcher and
    the same fingerprint machinery the interactive scan uses, so a monitored
    re-scan and a manual re-scan agree.
    """
    import os as _os
    import tempfile
    from pathlib import Path

    from app.security.url_fetch import SsrfError, UrlFetchError, fetch_url_html
    from app.services.scan_history import build_change_report, save_scan

    url = monitor.get("url") or ""
    try:
        html_bytes, final_url = fetch_url_html(url)
    except (SsrfError, UrlFetchError) as exc:
        return {"ok": False, "status": str(exc)[:200]}
    except Exception as exc:
        return {"ok": False, "status": f"Could not fetch: {exc}"[:200]}

    fd, tmp_name = tempfile.mkstemp(suffix=".html")
    tmp_path = Path(tmp_name)
    try:
        _os.close(fd)
        tmp_path.write_bytes(html_bytes)
        from app.parsers import parse_to_tree
        from app.services.remediation_engine import RemediationEngine

        result = parse_to_tree(str(tmp_path))
        tree = result.tree
        violations = RemediationEngine().detect_violations(tree)
    except Exception as exc:
        return {"ok": False, "status": f"Could not analyze the page: {exc}"[:200]}
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

    from app.api.pipeline import _build_scan_score

    score = _build_scan_score(violations)
    report, fingerprints = build_change_report(
        monitor.get("user_id") or "", final_url, violations, tree, score
    )
    save_scan(monitor.get("user_id") or "", final_url, fingerprints, score)
    return {
        "ok": True,
        "url": final_url,
        "issueCount": len(violations),
        "score": score.score,
        "grade": score.grade,
        "changes": report,
        "status": f"{len(violations)} issue(s), grade {score.grade}",
    }


def _alert_body(url: str, result: Dict[str, Any]) -> str:
    changes = result.get("changes") or {}
    new = int(changes.get("newIssues") or 0)
    resolved = int(changes.get("resolvedIssues") or 0)
    lines = [
        f"We re-checked {url} and found {new} new accessibility issue(s).",
        "",
        f"  New since last check:   {new}",
        f"  Fixed since last check: {resolved}",
        f"  Total open issues:      {result.get('issueCount', 0)}",
        f"  Page score:             {result.get('score')} ({result.get('grade')})",
        "",
        "Sign in to see exactly what changed and how to fix each one.",
        "",
        "You're receiving this because you asked 508 Agent to monitor this page.",
    ]
    return "\n".join(lines)


def maybe_send_alert(monitor: Dict[str, Any], result: Dict[str, Any]) -> bool:
    """Email the owner ONLY when something new broke. Returns True if sent.

    Deliberately quiet: no email for a clean re-check, and none for issues that
    were already there. An alerting product people mute is worthless.
    """
    to = (monitor.get("notify_email") or "").strip()
    if not to or not result.get("ok"):
        return False
    changes = result.get("changes") or {}
    new_issues = int(changes.get("newIssues") or 0)
    if new_issues <= 0:
        return False
    try:
        from app.services.mailer import send_email

        url = result.get("url") or monitor.get("url") or ""
        subject = f"[508 Agent] {new_issues} new accessibility issue(s) on {url[:80]}"
        return bool(send_email(to, subject, _alert_body(url, result)))
    except Exception as exc:
        logger.warning("maybe_send_alert failed: %s", exc)
        return False


def process_one_due_monitor(now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Claim -> scan -> alert -> release. Returns a summary, or None if nothing due."""
    monitor = claim_due_monitor(now=now)
    if monitor is None:
        return None
    result = run_monitor_check(monitor)
    alerted = maybe_send_alert(monitor, result)
    release_monitor(
        monitor["id"],
        frequency=monitor.get("frequency") or DEFAULT_FREQUENCY,
        status=result.get("status") or "",
        issue_count=result.get("issueCount") if result.get("ok") else None,
        failed=not result.get("ok"),
    )
    return {"monitorId": monitor["id"], "alerted": alerted, **result}


def list_monitors(user_id: str) -> List[Dict[str, Any]]:
    try:
        from app.db.models import MonitoredSiteRow
        from app.db.session_sqlalchemy import session_scope

        with session_scope() as session:
            rows = (
                session.query(MonitoredSiteRow)
                .filter(MonitoredSiteRow.user_id == user_id)
                .order_by(MonitoredSiteRow.created_at.desc())
                .all()
            )
            return [
                {
                    "id": r.id,
                    "url": r.url,
                    "frequency": r.frequency,
                    "enabled": bool(r.enabled),
                    "notifyEmail": r.notify_email,
                    "lastRunAt": r.last_run_at.isoformat() if r.last_run_at else None,
                    "nextRunAt": r.next_run_at.isoformat() if r.next_run_at else None,
                    "lastIssueCount": int(r.last_issue_count or 0),
                    "lastStatus": r.last_status or "",
                }
                for r in rows
            ]
    except Exception as exc:
        logger.warning("list_monitors failed: %s", exc)
        return []
