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


def normalize_url_key(url: str) -> str:
    """Identity a re-scan is matched on: scheme+host+path, no query/fragment."""
    try:
        p = urlparse((url or "").strip())
        host = (p.hostname or "").lower()
        path = (p.path or "/").rstrip("/") or "/"
        scheme = (p.scheme or "https").lower()
        port = f":{p.port}" if p.port and p.port not in (80, 443) else ""
        return f"{scheme}://{host}{port}{path}"[:600]
    except Exception:
        return (url or "")[:600]


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

    xpath = ((getattr(node, "metadata", None) and node.metadata.properties) or {}).get("__xpath")
    return f"{kind}|{xpath or ''}"


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
                fps = json.loads(row.fingerprints or "[]")
            except Exception:
                fps = []
            return {
                "scannedAt": row.created_at.isoformat() if row.created_at else None,
                "issueCount": int(row.issue_count or 0),
                "score": float(row.score or 0),
                "grade": row.grade or "",
                "fingerprints": fps if isinstance(fps, list) else [],
            }
    except Exception as exc:  # history is a bonus, never a hard dependency
        logger.warning("load_previous_scan failed: %s", exc)
        return None


def save_scan(user_id: str, url: str, fingerprints: List[str], score: Any) -> None:
    """Record this scan. Best-effort; never raises."""
    if not user_id:
        return
    try:
        import uuid

        from app.db.models import ScanHistoryRow
        from app.db.session_sqlalchemy import session_scope

        with session_scope() as session:
            session.add(
                ScanHistoryRow(
                    id=uuid.uuid4().hex,
                    user_id=user_id,
                    url_key=normalize_url_key(url),
                    url=(url or "")[:2048],
                    issue_count=len(fingerprints),
                    score=int(round(float(getattr(score, "score", 0) or 0))),
                    grade=str(getattr(score, "grade", "") or "")[:8],
                    fingerprints=json.dumps(fingerprints[:_MAX_FINGERPRINTS]),
                    created_at=datetime.utcnow(),
                )
            )
    except Exception as exc:
        logger.warning("save_scan failed: %s", exc)


def build_change_report(
    user_id: str, url: str, violations: List[Any], tree: Any, score: Any
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Diff this scan against the previous one. Returns (report|None, fingerprints)."""
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
