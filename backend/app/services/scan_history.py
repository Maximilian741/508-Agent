"""Scan history + regression diff for the free URL scanner.

"You have 14 issues" is a snapshot. "3 of these are NEW since Tuesday, and you
fixed 5" is a reason to come back — and it's the foundation the scheduled
monitoring feature will sit on.

The hard part is deciding whether an issue in today's scan is the SAME issue as
one from last week. Violation ids can't be used: node ids are ordinal counters
minted by the parser (``html-link-7``), so inserting one paragraph renumbers
everything after it and a naive id diff reports a wall of phantom regressions.

So we fingerprint each issue by its CONTENT — the rule plus the most durable
identifying signal the node offers (a link's href + text, a heading's text, a
table's header cells …), falling back to the element's xpath only when the node
carries no text of its own. Identical fingerprints within one scan get an
occurrence suffix so counts stay exact.

This is a best-effort match, not an identity proof: a page that rewrites its
copy wholesale will look like "old issues fixed, new issues appeared". The UI
says "since your last scan" rather than claiming per-issue lineage.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_WS_RE = re.compile(r"\s+")
_MAX_FINGERPRINTS = 400  # bound the stored payload
# Bumping this invalidates stored fingerprints. A version MISMATCH suppresses
# the diff entirely rather than reporting a false "everything fixed, everything
# new" — see load_previous_scan.
FINGERPRINT_VERSION = "v1"


def normalize_url_key(url: str) -> str:
    """Identity a re-scan is matched on.

    scheme+host+path, plus a short HASH of the query string when there is one.
    Hashing (rather than keeping or dropping the query) satisfies both needs:
    ``?page=2`` stays a distinct page with its own history, while no raw query
    — which routinely carries tokens, emails and other PII — is ever stored.
    """
    try:
        p = urlparse((url or "").strip())
        host = (p.hostname or "").lower()
        path = (p.path or "/").rstrip("/") or "/"
        scheme = (p.scheme or "https").lower()
        port = f":{p.port}" if p.port and p.port not in (80, 443) else ""
        key = f"{scheme}://{host}{port}{path}"
        if p.query:
            key += "?" + hashlib.sha1(p.query.encode("utf-8", "ignore")).hexdigest()[:10]
        return key[:600]
    except Exception:
        return (url or "").split("?", 1)[0][:600]


def sanitized_url(url: str) -> str:
    """The URL with its query string and fragment removed, for storage/display."""
    try:
        p = urlparse((url or "").strip())
        host = (p.hostname or "").lower()
        port = f":{p.port}" if p.port and p.port not in (80, 443) else ""
        return f"{(p.scheme or 'https').lower()}://{host}{port}{p.path or '/'}"[:2048]
    except Exception:
        return (url or "").split("?", 1)[0][:2048]


def _log_safe(url: str) -> str:
    """Host only — never log a full URL (query strings carry tokens/PII)."""
    try:
        return urlparse((url or "").strip()).hostname or "?"
    except Exception:
        return "?"


def _norm(text: str, limit: int = 80) -> str:
    return _WS_RE.sub(" ", (text or "").strip()).lower()[:limit]


def _node_signature(node: Any) -> str:
    """The most durable identifying signal a node offers."""
    if node is None:
        return ""
    kind = type(node).__name__
    content = getattr(node, "content", None)
    text = _norm(getattr(content, "text", "") or "")

    target = getattr(node, "target", None)
    if target:  # LinkNode — href is far more stable than link text
        return f"{kind}|{_norm(str(target), 200)}|{text}"

    if text:
        return f"{kind}|{text}"

    # Structural nodes (tables, images, empty containers) carry no text of their
    # own. Use child text when there is any (a table's header cells), else fall
    # back to the parser's xpath.
    child_bits: List[str] = []
    for child in (getattr(node, "children", None) or [])[:6]:
        ctext = _norm(getattr(getattr(child, "content", None), "text", "") or "", 24)
        if ctext:
            child_bits.append(ctext)
        for gchild in (getattr(child, "children", None) or [])[:4]:
            gtext = _norm(getattr(getattr(gchild, "content", None), "text", "") or "", 24)
            if gtext:
                child_bits.append(gtext)
        if len(child_bits) >= 6:
            break
    if child_bits:
        return f"{kind}|" + ",".join(child_bits[:6])

    # Last resort (images carry no text): the xpath TAIL, not the full path.
    # A full path is anchored at <html>, so wrapping the page in one extra
    # <div> — a routine CSS/layout edit — changes every path and the diff would
    # report every finding as simultaneously new AND resolved. The last couple
    # of steps identify the element's local position and survive ancestor
    # restructuring.
    xpath = ((getattr(node, "metadata", None) and node.metadata.properties) or {}).get("__xpath")
    if xpath:
        steps = [s for s in str(xpath).split("/") if s]
        # Only the element's OWN tag, with its positional index stripped. A full
        # path is anchored at <html>, so wrapping the page in one <div> — a
        # routine layout edit — would change every path and make the diff report
        # every finding as simultaneously new AND resolved. Same-tag siblings
        # are then separated by the occurrence index applied below, which
        # follows document order and is stable under ancestor restructuring.
        own = re.sub(r"\[\d+\]$", "", steps[-1]) if steps else ""
        return f"{kind}|~{own}"
    return f"{kind}|"


def fingerprint_violations(violations: List[Any], tree: Any) -> List[str]:
    """Stable, content-derived fingerprints for a scan's findings."""
    nodes: Dict[str, Any] = {}
    try:
        from app.models.accessibility import iter_reading_order

        for n in iter_reading_order(tree.root):
            nodes[n.id] = n
    except Exception:
        pass

    seen: Dict[str, int] = {}
    out: List[str] = []
    for v in violations[:_MAX_FINGERPRINTS]:
        rule = getattr(v, "rule_id", "") or ""
        node = nodes.get(getattr(getattr(v, "location", None), "node_id", "") or "")
        base = f"{rule}|{_node_signature(node)}"
        # Same rule on identically-described nodes (e.g. two <img> with no alt
        # and no text) -> add an occurrence index so counts stay exact.
        idx = seen.get(base, 0)
        seen[base] = idx + 1
        digest = hashlib.sha1(f"{base}#{idx}".encode("utf-8", "ignore")).hexdigest()[:16]
        out.append(digest)
    return out


def diff_fingerprints(previous: List[str], current: List[str]) -> Dict[str, int]:
    """Counts of newly-appeared / resolved / carried-over issues."""
    prev, cur = set(previous or []), set(current or [])
    return {
        "new": len(cur - prev),
        "resolved": len(prev - cur),
        "unchanged": len(cur & prev),
    }


def load_previous_scan(user_id: str, url: str) -> Optional[Dict[str, Any]]:
    """Most recent prior scan of this URL by this user (None if first time)."""
    if not user_id:
        return None
    try:
        from app.db.models import ScanHistoryRow
        from app.db.session_sqlalchemy import session_scope

        key = normalize_url_key(url)
        with session_scope() as session:
            row = (
                session.query(ScanHistoryRow)
                .filter(ScanHistoryRow.user_id == user_id, ScanHistoryRow.url_key == key)
                .order_by(ScanHistoryRow.created_at.desc())
                .first()
            )
            if row is None:
                return None
            try:
                payload = json.loads(row.fingerprints or "[]")
            except Exception:
                payload = []
            # v1 rows were a bare list; newer rows are {version, truncated, items}.
            if isinstance(payload, dict):
                version = payload.get("version")
                fps = payload.get("items") or []
                truncated = bool(payload.get("truncated"))
            else:
                version, fps, truncated = FINGERPRINT_VERSION, payload, False
            if version != FINGERPRINT_VERSION:
                # The signature algorithm changed, so old fingerprints are not
                # comparable. Suppress the diff rather than report a bogus
                # "everything fixed, everything new".
                return None
            return {
                "scannedAt": row.created_at.isoformat() if row.created_at else None,
                "issueCount": int(row.issue_count or 0),
                "score": float(row.score or 0),
                "grade": row.grade or "",
                "fingerprints": fps if isinstance(fps, list) else [],
                "truncated": truncated,
            }
    except Exception as exc:  # history is a bonus, never a hard dependency
        logger.warning("load_previous_scan failed for %s: %s", _log_safe(url), exc)
        return None


def save_scan(user_id: str, url: str, fingerprints: List[str], score: Any) -> None:
    """Record this scan. Best-effort; never raises."""
    if not user_id:
        return
    try:
        import uuid

        from app.db.models import ScanHistoryRow
        from app.db.session_sqlalchemy import session_scope

        truncated = len(fingerprints) > _MAX_FINGERPRINTS
        payload = {
            "version": FINGERPRINT_VERSION,
            # A truncated set can't support an honest "resolved" count later.
            "truncated": truncated,
            "items": fingerprints[:_MAX_FINGERPRINTS],
        }
        with session_scope() as session:
            session.add(
                ScanHistoryRow(
                    id=uuid.uuid4().hex,
                    user_id=user_id,
                    # Query string is deliberately NOT persisted (tokens/PII);
                    # url_key keeps a hash of it so pages stay distinguishable.
                    url_key=normalize_url_key(url),
                    url=sanitized_url(url),
                    issue_count=len(fingerprints),
                    score=int(round(float(getattr(score, "score", 0) or 0))),
                    grade=str(getattr(score, "grade", "") or "")[:8],
                    fingerprints=json.dumps(payload),
                    created_at=datetime.utcnow(),
                )
            )
        _prune_history(user_id, normalize_url_key(url))
    except Exception as exc:
        logger.warning("save_scan failed for %s: %s", _log_safe(url), exc)


_KEEP_PER_URL = 20


def _prune_history(user_id: str, url_key: str) -> None:
    """Keep only the most recent scans per (user, url).

    Only the latest row is ever read, so unbounded retention would grow the
    table forever for exactly the daily-scanner use case this feature targets.
    """
    try:
        from app.db.models import ScanHistoryRow
        from app.db.session_sqlalchemy import session_scope

        with session_scope() as session:
            stale = (
                session.query(ScanHistoryRow)
                .filter(ScanHistoryRow.user_id == user_id, ScanHistoryRow.url_key == url_key)
                .order_by(ScanHistoryRow.created_at.desc())
                .offset(_KEEP_PER_URL)
                .all()
            )
            for row in stale:
                session.delete(row)
    except Exception as exc:
        logger.warning("_prune_history failed: %s", exc)


def diff_against_previous(
    user_id: str, url: str, fingerprints: List[str]
) -> Optional[Dict[str, Any]]:
    """Diff already-computed fingerprints against the previous scan.

    Takes fingerprints rather than a tree on purpose: the caller must capture
    them BEFORE anything mutates the tree, otherwise the diff describes a
    remediated page the user doesn't actually have.
    """
    previous = load_previous_scan(user_id, url)
    if previous is None:
        return None
    prev_fps = previous.get("fingerprints") or []
    # A truncated previous scan can't support an honest "resolved" count: issues
    # beyond the cap were never recorded, so they'd read as fixed. Report only
    # what we can stand behind.
    if previous.get("truncated"):
        cur = set(fingerprints)
        return {
            "previousScanAt": previous.get("scannedAt"),
            "previousIssueCount": previous.get("issueCount", 0),
            "previousScore": previous.get("score", 0.0),
            "previousGrade": previous.get("grade", ""),
            "newIssues": len(cur - set(prev_fps)),
            "resolvedIssues": 0,
            "unchangedIssues": len(cur & set(prev_fps)),
            "partial": True,
        }
    counts = diff_fingerprints(prev_fps, fingerprints)
    return {
        "previousScanAt": previous.get("scannedAt"),
        "previousIssueCount": previous.get("issueCount", 0),
        "previousScore": previous.get("score", 0.0),
        "previousGrade": previous.get("grade", ""),
        "newIssues": counts["new"],
        "resolvedIssues": counts["resolved"],
        "unchangedIssues": counts["unchanged"],
        "partial": False,
    }


def build_change_report(
    user_id: str, url: str, violations: List[Any], tree: Any, score: Any
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Diff this scan against the previous one. Returns (report|None, fingerprints).

    Convenience wrapper for callers (the monitor runner) that hold an
    unmutated tree. Interactive scans should fingerprint first and call
    :func:`diff_against_previous` instead.
    """
    fingerprints = fingerprint_violations(violations, tree)
    previous = load_previous_scan(user_id, url)
    if previous is None:
        return None, fingerprints
    counts = diff_fingerprints(previous.get("fingerprints") or [], fingerprints)
    return (
        {
            "previousScanAt": previous.get("scannedAt"),
            "previousIssueCount": previous.get("issueCount", 0),
            "previousScore": previous.get("score", 0.0),
            "previousGrade": previous.get("grade", ""),
            "newIssues": counts["new"],
            "resolvedIssues": counts["resolved"],
            "unchangedIssues": counts["unchanged"],
        },
        fingerprints,
    )
