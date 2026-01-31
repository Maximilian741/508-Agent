"""In-memory API state for MVP endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models.accessibility import AccessibilityTree

last_tree: Optional[AccessibilityTree] = None
last_scan_id: Optional[str] = None
last_document_id: Optional[str] = None
last_issues: List[Dict[str, Any]] = []
manual_review_queue: List[Dict[str, Any]] = []
