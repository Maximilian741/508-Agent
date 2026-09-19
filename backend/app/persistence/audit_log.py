"""Append-only audit log persistence.

Records every important user-facing action (analyze, remediate, share, view,
download, manual_review_resolve, auth_fail) keyed to the actor identified by
the Cloudflare Access JWT.  The table is intentionally minimal:

* ``id`` is a uuid4 hex.
* ``at`` is ISO-8601 UTC.
* ``request_id`` is the value attached to ``request.state.request_id`` by
  ``RequestIdLoggingMiddleware`` so an entry can be cross-referenced against
  application logs.
* ``actor_email`` / ``actor_sub`` come from ``request.state.user`` (CF Access
  claims) or are ``None`` in dev mode.
* ``ip`` is whatever ``request.client.host`` reports — best-effort, may be a
  Cloudflare proxy address rather than the real user.
* ``event`` is one of a small enum: see ``AuditEvent`` below.
* ``doc_id`` / ``job_id`` are the most useful cross-cuts for the admin UI.
* ``details_json`` is an opaque, JSON-encoded blob of additional metadata.
  PII discipline: callers must not include filenames, document titles, or
  raw user content.  Doc ids only.

This module talks to SQLAlchemy directly (via the engine the rest of the
persistence layer uses) so we get the same transactional semantics as the
existing repositories.  Writes are deliberately defensive: if the table is
absent (legacy DB, migration not yet run), every helper degrades to a
``logger.warning`` no-op rather than throwing.

The table is append-only at the API layer.  ``record_event`` only INSERTs;
``purge_older_than`` is the single legitimate DELETE path and is gated to
admin emails by the API router.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.db.session_sqlalchemy import ENGINE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public event vocabulary
# ---------------------------------------------------------------------------

# Anything not in this set is rejected at write-time so we don't end up with a
# free-for-all of misspelled events.
_ALLOWED_EVENTS = frozenset(
    {
        "analyze",
        # The URL scan and the whole-site scan record these; both were being
        # dropped as unknown, so the free-scan funnel left no audit trail at all.
        "analyze_url",
        "scan_site",
        "remediate",
        "share_create",
        "share_view",
        "download",
        "manual_review_resolve",
        "auth_fail",
        "grant_credits",
        "spend_credits",
        "purchase_credits",
    }
)


@dataclass(frozen=True)
class AuditLogEntry:
    """Plain-data record returned by ``list_events``."""

    id: str
    at: str
    request_id: Optional[str]
    actor_email: Optional[str]
    actor_sub: Optional[str]
    ip: Optional[str]
    event: str
    doc_id: Optional[str]
    job_id: Optional[str]
    details: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "at": self.at,
            "requestId": self.request_id,
            "actorEmail": self.actor_email,
            "actorSub": self.actor_sub,
            "ip": self.ip,
            "event": self.event,
            "docId": self.doc_id,
            "jobId": self.job_id,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _table_exists(connection) -> bool:
    """Cheap probe: ``SELECT 1 FROM audit_log LIMIT 0`` — works on both
    SQLite and Postgres, raises on missing table.
    """
    try:
        connection.execute(text("SELECT 1 FROM audit_log LIMIT 0"))
        return True
    except Exception:
        return False


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        # Last-ditch — never raise from the audit path.
        return "{}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def record_event(
    *,
    event: str,
    request_id: Optional[str] = None,
    actor_email: Optional[str] = None,
    actor_sub: Optional[str] = None,
    ip: Optional[str] = None,
    doc_id: Optional[str] = None,
    job_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Append a single audit log entry.  Returns the new entry id, or None
    if the write was a soft no-op (table missing, etc.).

    Never raises — callers wire this in code paths that absolutely cannot
    fail just because the audit log isn't ready.
    """

    if event not in _ALLOWED_EVENTS:
        logger.warning("audit_log.record_event: unknown event %r — dropping", event)
        return None

    entry_id = uuid.uuid4().hex
    at = _utc_now_iso()
    payload = _safe_json(details or {})

    try:
        with ENGINE.begin() as conn:
            if not _table_exists(conn):
                logger.warning(
                    "audit_log.record_event: audit_log table missing — skipping %s event",
                    event,
                )
                return None
            conn.execute(
                text(
                    """
                    INSERT INTO audit_log
                      (id, at, request_id, actor_email, actor_sub, ip, event, doc_id, job_id, details_json)
                    VALUES
                      (:id, :at, :request_id, :actor_email, :actor_sub, :ip, :event, :doc_id, :job_id, :details_json)
                    """
                ),
                {
                    "id": entry_id,
                    "at": at,
                    "request_id": request_id,
                    "actor_email": actor_email,
                    "actor_sub": actor_sub,
                    "ip": ip,
                    "event": event,
                    "doc_id": doc_id,
                    "job_id": job_id,
                    "details_json": payload,
                },
            )
        return entry_id
    except Exception as exc:
        logger.warning("audit_log.record_event failed for event=%s: %s", event, exc)
        return None


def list_events(
    filters: Optional[Dict[str, Any]] = None,
    limit: int = 200,
) -> List[AuditLogEntry]:
    """Return up to ``limit`` recent entries matching ``filters``.

    Supported filter keys:
      * ``actor_email`` — case-insensitive SUBSTRING match. Only ever safe
        behind the admin gate: "o@bigcorp.com" matches "cfo@bigcorp.com", so it
        must never be used to scope a caller to their own rows.
      * ``actor_email_exact`` — case-insensitive EXACT match. This is the one
        to use for per-user scoping.
      * ``event`` — exact match.
      * ``since`` — ISO-8601 UTC string; entries strictly after.
      * ``until`` — ISO-8601 UTC string; entries strictly before.

    Unknown keys are ignored.  ``limit`` is clamped to 1..2000.
    """

    filters = filters or {}
    bounded_limit = max(1, min(2000, int(limit or 200)))

    where: List[str] = []
    params: Dict[str, Any] = {}
    actor_email = (filters.get("actor_email") or "").strip()
    if actor_email:
        # SQLite uses LIKE (case-insensitive by default for ASCII).  Postgres
        # honors LOWER() for the comparison.  Both behave acceptably.
        where.append("LOWER(COALESCE(actor_email,'')) LIKE :actor_email")
        params["actor_email"] = f"%{actor_email.lower()}%"

    actor_email_exact = (filters.get("actor_email_exact") or "").strip()
    if actor_email_exact:
        where.append("LOWER(COALESCE(actor_email,'')) = :actor_email_exact")
        params["actor_email_exact"] = actor_email_exact.lower()

    event = (filters.get("event") or "").strip()
    if event:
        where.append("event = :event")
        params["event"] = event

    since = (filters.get("since") or "").strip()
    if since:
        where.append("at > :since")
        params["since"] = since

    until = (filters.get("until") or "").strip()
    if until:
        where.append("at < :until")
        params["until"] = until

    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    sql = (
        f"SELECT id, at, request_id, actor_email, actor_sub, ip, event, doc_id, job_id, details_json "
        f"FROM audit_log {where_clause} ORDER BY at DESC LIMIT :limit"
    )
    params["limit"] = bounded_limit

    try:
        with ENGINE.begin() as conn:
            if not _table_exists(conn):
                logger.warning("audit_log.list_events: table missing — returning []")
                return []
            rows = conn.execute(text(sql), params).fetchall()
    except Exception as exc:
        logger.warning("audit_log.list_events failed: %s", exc)
        return []

    out: List[AuditLogEntry] = []
    for row in rows:
        try:
            details = json.loads(row[9] or "{}")
            if not isinstance(details, dict):
                details = {}
        except Exception:
            details = {}
        out.append(
            AuditLogEntry(
                id=str(row[0]),
                at=str(row[1] or ""),
                request_id=(str(row[2]) if row[2] is not None else None),
                actor_email=(str(row[3]) if row[3] is not None else None),
                actor_sub=(str(row[4]) if row[4] is not None else None),
                ip=(str(row[5]) if row[5] is not None else None),
                event=str(row[6] or ""),
                doc_id=(str(row[7]) if row[7] is not None else None),
                job_id=(str(row[8]) if row[8] is not None else None),
                details=details,
            )
        )
    return out


def purge_older_than(days: int) -> int:
    """Delete entries older than ``days`` days.  Returns rows removed.

    This is the single legitimate DELETE path; gated to admin emails at the
    API router.  ``days`` is clamped to a minimum of 1 to make accidental
    "purge everything" impossible.
    """

    bounded_days = max(1, int(days or 1))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=bounded_days)).isoformat().replace("+00:00", "Z")
    try:
        with ENGINE.begin() as conn:
            if not _table_exists(conn):
                logger.warning("audit_log.purge_older_than: table missing — no-op")
                return 0
            result = conn.execute(
                text("DELETE FROM audit_log WHERE at < :cutoff"),
                {"cutoff": cutoff},
            )
            return int(getattr(result, "rowcount", 0) or 0)
    except Exception as exc:
        logger.warning("audit_log.purge_older_than failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Convenience: derive actor info from a FastAPI Request
# ---------------------------------------------------------------------------


def context_from_request(request) -> Dict[str, Optional[str]]:
    """Pull ``request_id``, ``actor_email``, ``actor_sub``, ``ip`` from a
    FastAPI ``Request``.  Used by the route hooks so each call site stays
    a one-liner.  Never raises.
    """

    out: Dict[str, Optional[str]] = {
        "request_id": None,
        "actor_email": None,
        "actor_sub": None,
        "ip": None,
    }
    try:
        out["request_id"] = getattr(request.state, "request_id", None)
    except Exception:
        pass
    try:
        user = getattr(request.state, "user", None) or {}
        if isinstance(user, dict):
            out["actor_email"] = user.get("email")
            out["actor_sub"] = user.get("sub")
    except Exception:
        pass
    try:
        client = getattr(request, "client", None)
        if client is not None:
            out["ip"] = getattr(client, "host", None)
    except Exception:
        pass
    return out


__all__ = [
    "AuditLogEntry",
    "record_event",
    "list_events",
    "purge_older_than",
    "context_from_request",
]
