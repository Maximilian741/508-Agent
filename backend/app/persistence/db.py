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
        conn.commit()


class SqliteRepo:
    def save_document(self, doc: Dict[str, object]) -> None:
        conn = get_connection()
        doc_id = str(doc["id"])
        now = datetime.utcnow().isoformat() + "Z"
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
                    (doc_id, phase, json.dumps(issue), key, datetime.utcnow().isoformat() + "Z"),
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
                (doc_id, json.dumps(report), datetime.utcnow().isoformat() + "Z"),
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
        now = datetime.utcnow().isoformat() + "Z"
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
