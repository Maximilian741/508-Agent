"""Per-user, process-local API state for the legacy scan -> remediate flow.

Keyed by the authenticated user id so concurrent users never observe each
other's last scan (the old module-global ``last_tree`` was a cross-tenant
leak). This is best-effort, in-memory state; the durable record lives in the
repository (issues, fix_reports, manual_review) keyed by document id.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models.accessibility import AccessibilityTree


class UserScanState:
    __slots__ = ("last_tree", "last_scan_id", "last_document_id", "last_issues")

    def __init__(self) -> None:
        self.last_tree: Optional[AccessibilityTree] = None
        self.last_scan_id: Optional[str] = None
        self.last_document_id: Optional[str] = None
        self.last_issues: List[Dict[str, Any]] = []


_BY_USER: Dict[str, UserScanState] = {}
_MAX_USERS = 1000


def for_user(user_id: str) -> UserScanState:
    """Return (creating if needed) the scan state for ``user_id``."""
    st = _BY_USER.get(user_id)
    if st is None:
        if len(_BY_USER) >= _MAX_USERS:
            # Bound memory: evict an arbitrary existing entry.
            _BY_USER.pop(next(iter(_BY_USER)), None)
        st = UserScanState()
        _BY_USER[user_id] = st
    return st
