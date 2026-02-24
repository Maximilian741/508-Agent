from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _default_policy_json(name: str, targets: List[str], severity_weights: Dict[str, float], overrides: Dict[str, Dict[str, object]]) -> Dict[str, object]:
    return {
        "schemaVersion": 1,
        "name": name,
        "targets": targets,
        "thresholds": {
            "statusRules": {
                "pass": {"maxCritical": 0, "maxSerious": 2},
                "needs_review": {"maxCritical": 0, "maxSerious": 10},
            }
        },
        "scoring": {
            "baseScore": 100,
            "severityWeights": severity_weights,
            "confidenceMultiplier": True,
            "coveragePenalty": {"enabled": True, "perSkippedRule": 0.2, "maxPenalty": 10},
        },
        "rules": {
            "defaults": {"enabled": True},
            "overrides": overrides,
        },
        "export": {
            "includeOriginal": False,
            "includeFixedIfAvailable": True,
            "templates": {"summaryPdf": "default_v1"},
        },
    }


_DEFAULT_POLICY_PACKS: List[Dict[str, object]] = [
    {
        "id": "policy-508-wcag20-aa",
        "name": "Section 508 (WCAG 2.0 AA)",
        "description": "Baseline Section 508-aligned checks for PDF, DOCX, and PPTX.",
        "version": 1,
        "is_active": 1,
        "policy_json": _default_policy_json(
            name="Section 508 (WCAG 2.0 AA)",
            targets=["pdf", "docx", "pptx"],
            severity_weights={"critical": 18, "serious": 8, "moderate": 3, "minor": 1},
            overrides={
                "PDF.MISSING_ALT_TEXT": {"enabled": True, "severity": "serious"},
                "PDF.TAG_TREE_MISSING": {"enabled": True, "severity": "critical"},
                "DOCX.METADATA_LANGUAGE_MISSING": {"enabled": True, "severity": "moderate"},
            },
        ),
    },
    {
        "id": "policy-wcag22-aa-docs",
        "name": "WCAG 2.2 AA (docs)",
        "description": "WCAG 2.2 document-focused profile with stricter structure requirements.",
        "version": 1,
        "is_active": 1,
        "policy_json": _default_policy_json(
            name="WCAG 2.2 AA (docs)",
            targets=["pdf", "docx", "pptx"],
            severity_weights={"critical": 20, "serious": 9, "moderate": 4, "minor": 1},
            overrides={
                "PDF.MISSING_ALT_TEXT": {"enabled": True, "severity": "serious"},
                "PDF.TAG_TREE_MISSING": {"enabled": True, "severity": "critical"},
                "PPTX.READING_ORDER": {"enabled": True, "severity": "serious"},
            },
        ),
    },
    {
        "id": "policy-pdf-ua-focused",
        "name": "PDF/UA-focused (Tagged PDF)",
        "description": "Prioritizes tagged PDF structure, reading order, and alternate text completeness.",
        "version": 1,
        "is_active": 1,
        "policy_json": _default_policy_json(
            name="PDF/UA-focused (Tagged PDF)",
            targets=["pdf"],
            severity_weights={"critical": 22, "serious": 10, "moderate": 3, "minor": 1},
            overrides={
                "PDF.TAG_TREE_MISSING": {"enabled": True, "severity": "critical"},
                "PDF.MISSING_ALT_TEXT": {"enabled": True, "severity": "critical"},
                "PDF.READING_ORDER": {"enabled": True, "severity": "serious"},
            },
        ),
    },
]


def _db_path() -> Path:
    url = os.getenv("DATABASE_URL", "").strip()
    if url.startswith("sqlite:///"):
        raw = url.replace("sqlite:///", "", 1)
        return Path(raw)
    explicit = os.getenv("DATABASE_PATH", "").strip()
    if explicit:
        return Path(explicit)
    return Path("./.runtime/508_agent.db")


def get_connection() -> sqlite3.Connection:
    global _CONN
    if _CONN is not None:
        return _CONN
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _CONN = sqlite3.connect(str(path), check_same_thread=False)
    _CONN.row_factory = sqlite3.Row
    return _CONN


def init_db() -> None:
    conn = get_connection()
    with _LOCK:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
              id TEXT PRIMARY KEY,
              filename TEXT,
              doc_type TEXT,
              created_at TEXT,
              status TEXT,
              original_path TEXT,
              fixed_path TEXT,
              rebuilt_path TEXT,
              tag_tree_path TEXT,
              extra_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS issues (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              doc_id TEXT,
              phase TEXT,
              issue_json TEXT,
              issue_key TEXT,
              created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fix_reports (
              doc_id TEXT PRIMARY KEY,
              report_json TEXT,
              created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manual_review (
              id TEXT PRIMARY KEY,
              doc_id TEXT,
              item_json TEXT,
              created_at TEXT,
              resolved INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_jobs (
              id TEXT PRIMARY KEY,
              doc_id TEXT,
              status TEXT,
              progress REAL,
              message TEXT,
              started_at TEXT,
              finished_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_packs (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              description TEXT,
              version INTEGER NOT NULL,
              is_active INTEGER NOT NULL DEFAULT 1,
              policy_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_policy_snapshot (
              job_id TEXT PRIMARY KEY,
              policy_pack_id TEXT,
              policy_name TEXT NOT NULL,
              policy_version INTEGER NOT NULL,
              policy_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """
        )
        now = _utc_now()
        for pack in _DEFAULT_POLICY_PACKS:
            conn.execute(
                """
                INSERT OR IGNORE INTO policy_packs(id, name, description, version, is_active, policy_json, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    str(pack["id"]),
                    str(pack["name"]),
                    str(pack.get("description") or ""),
                    int(pack.get("version") or 1),
                    int(pack.get("is_active") or 1),
                    json.dumps(pack["policy_json"]),
                    now,
                    now,
                ),
            )
        conn.commit()


class SqliteRepo:
    def _policy_from_row(self, row: sqlite3.Row) -> Dict[str, object]:
        policy_json: Dict[str, object] = {}
        try:
            payload = json.loads(row["policy_json"] or "{}")
            if isinstance(payload, dict):
                policy_json = payload
        except Exception:
            policy_json = {}
        targets = policy_json.get("targets", [])
        if not isinstance(targets, list):
            targets = []
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"] or "",
            "version": int(row["version"] or 1),
            "targets": [str(target) for target in targets],
            "updated_at": row["updated_at"],
            "policy_json": policy_json,
        }

    def list_policy_packs(self) -> List[Dict[str, object]]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, name, description, version, policy_json, updated_at FROM policy_packs WHERE COALESCE(is_active,1)=1 ORDER BY name ASC"
        ).fetchall()
        return [self._policy_from_row(row) for row in rows]

    def get_policy_pack(self, policy_id: str) -> Optional[Dict[str, object]]:
        conn = get_connection()
        row = conn.execute(
            "SELECT id, name, description, version, policy_json, updated_at FROM policy_packs WHERE id=?",
            (policy_id,),
        ).fetchone()
        if row is None:
            return None
        return self._policy_from_row(row)

    def save_job_policy_snapshot(
        self,
        job_id: str,
        policy_pack_id: Optional[str],
        policy_name: str,
        policy_version: int,
        policy_json: Dict[str, object],
    ) -> None:
        conn = get_connection()
        with _LOCK:
            conn.execute(
                """
                INSERT INTO job_policy_snapshot(job_id, policy_pack_id, policy_name, policy_version, policy_json, created_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(job_id) DO UPDATE SET
                  policy_pack_id=excluded.policy_pack_id,
                  policy_name=excluded.policy_name,
                  policy_version=excluded.policy_version,
                  policy_json=excluded.policy_json
                """,
                (
                    job_id,
                    policy_pack_id,
                    policy_name,
                    int(policy_version),
                    json.dumps(policy_json),
                    _utc_now(),
                ),
            )
            conn.commit()

    def get_job_policy_snapshot(self, job_id: str) -> Optional[Dict[str, object]]:
        conn = get_connection()
        row = conn.execute(
            "SELECT job_id, policy_pack_id, policy_name, policy_version, policy_json, created_at FROM job_policy_snapshot WHERE job_id=?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        payload: Dict[str, object] = {}
        try:
            parsed = json.loads(row["policy_json"] or "{}")
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = {}
        return {
            "jobId": row["job_id"],
            "policyPackId": row["policy_pack_id"],
            "policyName": row["policy_name"],
            "policyVersion": int(row["policy_version"] or 1),
            "policyJson": payload,
            "createdAt": row["created_at"],
        }

    def save_document(self, doc: Dict[str, object]) -> None:
        conn = get_connection()
        doc_id = str(doc["id"])
        now = _utc_now()
        extra = {
            "scanTargetPath": doc.get("scanTargetPath"),
            "tagTreePath": doc.get("tagTreePath"),
            "tagSummary": doc.get("tagSummary"),
            "fixReport": doc.get("fixReport"),
        }
        with _LOCK:
            conn.execute(
                """
                INSERT INTO documents(id, filename, doc_type, created_at, status, original_path, fixed_path, rebuilt_path, tag_tree_path, extra_json)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  filename=excluded.filename,
                  doc_type=excluded.doc_type,
                  status=excluded.status,
                  original_path=excluded.original_path,
                  fixed_path=excluded.fixed_path,
                  rebuilt_path=excluded.rebuilt_path,
                  tag_tree_path=excluded.tag_tree_path,
                  extra_json=excluded.extra_json
                """,
                (
                    doc_id,
                    str(doc.get("filename") or ""),
                    str(doc.get("docType") or doc.get("kind") or "pdf"),
                    now,
                    str(doc.get("status") or ""),
                    str(doc.get("path") or ""),
                    str(doc.get("fixedPath") or ""),
                    str(doc.get("rebuiltPath") or ""),
                    str(doc.get("tagTreePath") or ""),
                    json.dumps(extra),
                ),
            )
            conn.commit()

    def get_document(self, doc_id: str) -> Optional[Dict[str, object]]:
        conn = get_connection()
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if row is None:
            return None
        extra: Dict[str, object] = {}
        try:
            extra = json.loads(row["extra_json"] or "{}")
            if not isinstance(extra, dict):
                extra = {}
        except Exception:
            extra = {}
        out: Dict[str, object] = {
            "id": row["id"],
            "filename": row["filename"],
            "docType": row["doc_type"] or "pdf",
            "path": row["original_path"],
            "fixedPath": row["fixed_path"] or None,
            "rebuiltPath": row["rebuilt_path"] or None,
            "scanTargetPath": extra.get("scanTargetPath"),
            "tagTreePath": extra.get("tagTreePath"),
            "tagSummary": extra.get("tagSummary"),
            "fixReport": extra.get("fixReport"),
        }
        return out

    def update_document(self, doc_id: str, updates: Dict[str, object]) -> None:
        current = self.get_document(doc_id)
        if current is None:
            return
        merged = dict(current)
        merged.update(updates)
        merged["id"] = doc_id
        self.save_document(merged)

    def list_documents(self) -> List[Dict[str, object]]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, filename, doc_type, created_at, original_path, fixed_path, rebuilt_path FROM documents ORDER BY created_at DESC"
        ).fetchall()
        return [
            {
                "docId": row["id"],
                "filename": row["filename"],
                "docType": row["doc_type"] or "pdf",
                "createdAt": row["created_at"],
                "path": row["original_path"],
                "fixedPath": row["fixed_path"],
                "rebuiltPath": row["rebuilt_path"],
            }
            for row in rows
        ]

    def save_job(self, job: Dict[str, object]) -> None:
        conn = get_connection()
        now = datetime.utcnow().isoformat() + "Z"
        with _LOCK:
            conn.execute(
                """
                INSERT INTO scan_jobs(id, doc_id, status, progress, message, started_at, finished_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  doc_id=excluded.doc_id,
                  status=excluded.status,
                  progress=excluded.progress,
                  message=excluded.message,
                  finished_at=excluded.finished_at
                """,
                (
                    str(job.get("jobId") or ""),
                    str(job.get("docId") or ""),
                    str(job.get("status") or ""),
                    float(job.get("progress") or 0),
                    str(job.get("message") or ""),
                    now,
                    now if str(job.get("status") or "") in {"done", "error"} else None,
                ),
            )
            conn.commit()

    def get_job(self, job_id: str) -> Optional[Dict[str, object]]:
        conn = get_connection()
        row = conn.execute("SELECT * FROM scan_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        return {
            "jobId": row["id"],
            "docId": row["doc_id"],
            "status": row["status"],
            "progress": int(row["progress"] or 0),
            "message": row["message"],
        }

    def update_job(self, job_id: str, updates: Dict[str, object]) -> None:
        current = self.get_job(job_id)
        if current is None:
            return
        current.update(updates)
        self.save_job(current)

    def save_issues(self, doc_id: str, phase: str, issues: List[Dict[str, object]], keys: List[str]) -> None:
        conn = get_connection()
        with _LOCK:
            conn.execute("DELETE FROM issues WHERE doc_id=? AND phase=?", (doc_id, phase))
            for issue, key in zip(issues, keys):
                conn.execute(
                    "INSERT INTO issues(doc_id, phase, issue_json, issue_key, created_at) VALUES(?,?,?,?,?)",
                    (doc_id, phase, json.dumps(issue), key, _utc_now()),
                )
            conn.commit()

    def get_issues(self, doc_id: str, phase: str) -> List[Dict[str, object]]:
        conn = get_connection()
        rows = conn.execute("SELECT issue_json FROM issues WHERE doc_id=? AND phase=? ORDER BY id ASC", (doc_id, phase)).fetchall()
        out: List[Dict[str, object]] = []
        for row in rows:
            try:
                issue = json.loads(row["issue_json"])
                if isinstance(issue, dict):
                    out.append(issue)
            except Exception:
                continue
        return out

    def get_latest_issues(self, doc_id: str) -> List[Dict[str, object]]:
        after = self.get_issues(doc_id, "after")
        if after:
            return after
        return self.get_issues(doc_id, "before")

    def save_fix_report(self, doc_id: str, report: Dict[str, object]) -> None:
        conn = get_connection()
        with _LOCK:
            conn.execute(
                """
                INSERT INTO fix_reports(doc_id, report_json, created_at) VALUES(?,?,?)
                ON CONFLICT(doc_id) DO UPDATE SET report_json=excluded.report_json, created_at=excluded.created_at
                """,
                (doc_id, json.dumps(report), _utc_now()),
            )
            conn.commit()

    def get_fix_report(self, doc_id: str) -> Optional[Dict[str, object]]:
        conn = get_connection()
        row = conn.execute("SELECT report_json FROM fix_reports WHERE doc_id=?", (doc_id,)).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["report_json"])
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def add_manual_review_items(self, doc_id: str, items: List[Dict[str, object]]) -> None:
        if not items:
            return
        conn = get_connection()
        now = _utc_now()
        with _LOCK:
            for item in items:
                item_id = str(item.get("id") or f"mr-{doc_id}-{int(datetime.utcnow().timestamp() * 1000)}")
                conn.execute(
                    """
                    INSERT INTO manual_review(id, doc_id, item_json, created_at, resolved)
                    VALUES(?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                      doc_id=excluded.doc_id,
                      item_json=excluded.item_json
                    """,
                    (
                        item_id,
                        doc_id,
                        json.dumps(item),
                        str(item.get("createdAt") or now),
                        0,
                    ),
                )
            conn.commit()

    def list_manual_review_items(self) -> List[Dict[str, object]]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT item_json FROM manual_review WHERE COALESCE(resolved,0)=0 ORDER BY created_at DESC"
        ).fetchall()
        out: List[Dict[str, object]] = []
        for row in rows:
            try:
                item = json.loads(row["item_json"])
                if isinstance(item, dict):
                    out.append(item)
            except Exception:
                continue
        return out

    def list_manual_review_items_for_doc(self, doc_id: str, include_resolved: bool = False) -> List[Dict[str, object]]:
        conn = get_connection()
        if include_resolved:
            rows = conn.execute(
                "SELECT item_json FROM manual_review WHERE doc_id=? ORDER BY created_at DESC",
                (doc_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT item_json FROM manual_review WHERE doc_id=? AND COALESCE(resolved,0)=0 ORDER BY created_at DESC",
                (doc_id,),
            ).fetchall()
        out: List[Dict[str, object]] = []
        for row in rows:
            try:
                item = json.loads(row["item_json"])
                if isinstance(item, dict):
                    out.append(item)
            except Exception:
                continue
        return out

    def clear_manual_review_items(self) -> int:
        conn = get_connection()
        with _LOCK:
            count_row = conn.execute("SELECT COUNT(*) AS c FROM manual_review WHERE COALESCE(resolved,0)=0").fetchone()
            count = int(count_row["c"] if count_row else 0)
            conn.execute("UPDATE manual_review SET resolved=1 WHERE COALESCE(resolved,0)=0")
            conn.commit()
            return count

    def get_manual_review_item(self, item_id: str) -> Optional[Dict[str, object]]:
        conn = get_connection()
        row = conn.execute("SELECT item_json FROM manual_review WHERE id=?", (item_id,)).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["item_json"])
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def update_manual_review_item(self, item_id: str, item: Dict[str, object], resolved: bool = False) -> bool:
        conn = get_connection()
        with _LOCK:
            exists = conn.execute("SELECT 1 FROM manual_review WHERE id=?", (item_id,)).fetchone()
            if exists is None:
                return False
            conn.execute(
                "UPDATE manual_review SET item_json=?, resolved=? WHERE id=?",
                (json.dumps(item), 1 if resolved else 0, item_id),
            )
            conn.commit()
            return True


_REPO = SqliteRepo()


def get_repo() -> SqliteRepo:
    return _REPO
