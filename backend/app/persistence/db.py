from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
UTC = timezone.utc
from pathlib import Path
from typing import Dict, List, Optional

from app.config import get_settings
from app.db.migrations import run_migrations
from app.db.models import PolicyPackRow
from app.db.session_sqlalchemy import SessionLocal
from app.persistence.repo_postgres import PostgresRepository

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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
    settings = get_settings()
    url = settings.database_url
    if url.startswith("sqlite:///"):
        raw = url.replace("sqlite:///", "", 1)
        return Path(raw)
    explicit = os.getenv("DATABASE_PATH", "").strip()
    if explicit:
        return Path(explicit)
    if url.startswith("postgres"):
        raise RuntimeError("sqlite3 db path resolution called for Postgres DATABASE_URL")
    return Path("./.runtime/508_agent.db")


def get_connection() -> sqlite3.Connection:
    global _CONN
    settings = get_settings()
    if settings.database_url.startswith("postgres"):
        raise RuntimeError("sqlite3 connection requested while DATABASE_URL is Postgres")
    if _CONN is not None:
        return _CONN
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _CONN = sqlite3.connect(str(path), check_same_thread=False)
    _CONN.row_factory = sqlite3.Row
    return _CONN


def init_db() -> None:
    settings = get_settings()
    if settings.database_url.startswith("postgres"):
        run_migrations()
        _seed_postgres_policy_packs()
        return

    conn = get_connection()
    with _LOCK:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
              id TEXT PRIMARY KEY,
              owner_id TEXT,
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
              owner_id TEXT,
              item_json TEXT,
              created_at TEXT,
              resolved INTEGER DEFAULT 0,
              ai_decision_json TEXT,
              ai_confidence REAL,
              ai_status TEXT,
              validator_status TEXT,
              ai_model TEXT,
              ai_updated_at TEXT
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_jobs_doc_id ON scan_jobs(doc_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fix_reports_doc_id ON fix_reports(doc_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_manual_review_doc_id ON manual_review(doc_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_issues_doc_phase ON issues(doc_id, phase)")
        # Backfill owner_id on the documents table for pre-existing sqlite DBs.
        _doc_cols = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(documents)").fetchall()
        }
        if "owner_id" not in _doc_cols:
            conn.execute("ALTER TABLE documents ADD COLUMN owner_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_owner ON documents(owner_id)")
        existing_cols = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(manual_review)").fetchall()
        }
        add_cols = [
            ("ai_decision_json", "TEXT"),
            ("ai_confidence", "REAL"),
            ("ai_status", "TEXT"),
            ("validator_status", "TEXT"),
            ("ai_model", "TEXT"),
            ("ai_updated_at", "TEXT"),
            # Who queued the item. Backfilled as NULL: pre-existing rows keep
            # showing up for the owner of their document (see 0017).
            ("owner_id", "TEXT"),
        ]
        for col_name, col_type in add_cols:
            if col_name not in existing_cols:
                conn.execute(f"ALTER TABLE manual_review ADD COLUMN {col_name} {col_type}")
        # After the backfill — the column has to exist before it can be indexed.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_manual_review_owner ON manual_review(owner_id)")
        # users and api_keys are ORM-owned (create_all in main.py), and
        # create_all never adds a column to an existing table. Backfill on a
        # pre-existing sqlite DB, or every sign-in fails on a missing column.
        orm_add_cols = [
            ("users", "token_version", "INTEGER NOT NULL DEFAULT 0"),
            ("api_keys", "revoked_reason", "TEXT"),
            # Refund/chargeback clawback (0018). Missing it 500s the FIRST thing
            # a new account does — POST /auth/grant-starter reads the ledger —
            # and the UI swallows that, so the user lands with 0 credits and no
            # idea why. Caught by running the real signup flow in a browser.
            ("credit_ledger", "stripe_ref", "TEXT"),
        ]
        for table_name, col_name, col_ddl in orm_add_cols:
            table_cols = {
                str(row[1])
                for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            }
            if table_cols and col_name not in table_cols:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_ddl}")
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_scoring (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id TEXT NOT NULL,
              pass_type TEXT NOT NULL,
              score_total INTEGER NOT NULL,
              status TEXT NOT NULL,
              counts_by_severity TEXT NOT NULL,
              points_by_category TEXT,
              coverage TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(job_id, pass_type)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS evidence_bundles (
              id TEXT PRIMARY KEY,
              job_id TEXT NOT NULL,
              doc_id TEXT NOT NULL,
              bundle_path TEXT NOT NULL,
              bundle_hash TEXT NOT NULL,
              created_at TEXT NOT NULL,
              created_by TEXT,
              options_json TEXT NOT NULL,
              status TEXT NOT NULL,
              error_text TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_bundles_doc_id ON evidence_bundles(doc_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_bundles_job_id ON evidence_bundles(job_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
              id TEXT PRIMARY KEY,
              at TEXT NOT NULL,
              request_id TEXT,
              actor_email TEXT,
              actor_sub TEXT,
              ip TEXT,
              event TEXT NOT NULL,
              doc_id TEXT,
              job_id TEXT,
              details_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_at ON audit_log(at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_actor_email ON audit_log(actor_email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_event ON audit_log(event)")
        # NOTE: users and credit_ledger are NOT created here. They are owned
        # by the SQLAlchemy ORM (app/db/models.py) and created in main.py via
        # Base.metadata.create_all. Hand-creating them here used to leave a
        # stale schema (missing password_hash, email_verified_at) that blocked
        # sign-in on fresh installs.
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


def _seed_postgres_policy_packs() -> None:
    with SessionLocal() as db:
        now = datetime.now(UTC)
        for pack in _DEFAULT_POLICY_PACKS:
            exists = db.get(PolicyPackRow, str(pack["id"]))
            if exists is not None:
                continue
            db.add(
                PolicyPackRow(
                    id=str(pack["id"]),
                    name=str(pack["name"]),
                    description=str(pack.get("description") or ""),
                    version=int(pack.get("version") or 1),
                    is_active=bool(pack.get("is_active", 1)),
                    policy_json=json.dumps(pack["policy_json"]),
                    created_at=now,
                    updated_at=now,
                )
            )
        db.commit()


class SqliteRepo:
    @staticmethod
    def _issue_severity_bucket(raw: object) -> str:
        severity = str(raw or "").strip().lower()
        if severity in {"critical", "serious", "moderate", "minor"}:
            return severity
        if severity == "error":
            return "serious"
        if severity == "warning":
            return "moderate"
        return "minor"

    @staticmethod
    def _empty_severity_counts() -> Dict[str, int]:
        return {"critical": 0, "serious": 0, "moderate": 0, "minor": 0}

    @classmethod
    def _normalized_severity_counts(cls, payload: Dict[str, object]) -> Dict[str, int]:
        out = cls._empty_severity_counts()
        for key, value in payload.items():
            bucket = cls._issue_severity_bucket(key)
            try:
                out[bucket] += int(value or 0)
            except Exception:
                continue
        return out

    @staticmethod
    def _normalize_job_status(raw: object) -> str:
        value = str(raw or "").strip().lower()
        if value in {"queued", "running"}:
            return value
        if value in {"done", "completed"}:
            return "completed"
        if value in {"error", "failed"}:
            return "failed"
        return value or "unknown"

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

    def save_job_score(
        self,
        job_id: str,
        pass_type: str,
        score_total: int,
        status: str,
        counts_by_severity: Dict[str, int],
        points_by_category: Dict[str, float],
        coverage: Dict[str, float],
    ) -> None:
        conn = get_connection()
        with _LOCK:
            conn.execute(
                """
                INSERT INTO job_scoring(job_id, pass_type, score_total, status, counts_by_severity, points_by_category, coverage, created_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(job_id, pass_type) DO UPDATE SET
                  score_total=excluded.score_total,
                  status=excluded.status,
                  counts_by_severity=excluded.counts_by_severity,
                  points_by_category=excluded.points_by_category,
                  coverage=excluded.coverage,
                  created_at=excluded.created_at
                """,
                (
                    job_id,
                    pass_type,
                    int(score_total),
                    status,
                    json.dumps(counts_by_severity),
                    json.dumps(points_by_category),
                    json.dumps(coverage),
                    _utc_now(),
                ),
            )
            conn.commit()

    def get_job_scores(self, job_id: str) -> List[Dict[str, object]]:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT pass_type, score_total, status, counts_by_severity, points_by_category, coverage, created_at
            FROM job_scoring
            WHERE job_id=?
            ORDER BY
              CASE pass_type
                WHEN 'baseline' THEN 0
                WHEN 'post_fix' THEN 1
                WHEN 'post_manual' THEN 2
                ELSE 9
              END ASC,
              created_at ASC
            """,
            (job_id,),
        ).fetchall()
        out: List[Dict[str, object]] = []
        for row in rows:
            try:
                counts = json.loads(row["counts_by_severity"] or "{}")
                if not isinstance(counts, dict):
                    counts = {}
            except Exception:
                counts = {}
            try:
                points = json.loads(row["points_by_category"] or "{}")
                if not isinstance(points, dict):
                    points = {}
            except Exception:
                points = {}
            try:
                coverage = json.loads(row["coverage"] or "{}")
                if not isinstance(coverage, dict):
                    coverage = {}
            except Exception:
                coverage = {}
            out.append(
                {
                    "passType": row["pass_type"],
                    "scoreTotal": int(row["score_total"] or 0),
                    "status": row["status"],
                    "countsBySeverity": counts,
                    "pointsByCategory": points,
                    "coverage": coverage,
                    "createdAt": row["created_at"],
                }
            )
        return out

    def create_evidence_bundle_record(
        self,
        bundle_id: str,
        job_id: str,
        doc_id: str,
        bundle_path: str,
        bundle_hash: str,
        options: Dict[str, object],
        status: str = "created",
        created_by: Optional[str] = None,
        error_text: Optional[str] = None,
    ) -> None:
        conn = get_connection()
        with _LOCK:
            conn.execute(
                """
                INSERT INTO evidence_bundles(id, job_id, doc_id, bundle_path, bundle_hash, created_at, created_by, options_json, status, error_text)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    bundle_id,
                    job_id,
                    doc_id,
                    bundle_path,
                    bundle_hash,
                    _utc_now(),
                    created_by,
                    json.dumps(options),
                    status,
                    error_text,
                ),
            )
            conn.commit()

    def update_evidence_bundle_record(
        self,
        bundle_id: str,
        *,
        bundle_path: Optional[str] = None,
        bundle_hash: Optional[str] = None,
        status: Optional[str] = None,
        error_text: Optional[str] = None,
    ) -> bool:
        conn = get_connection()
        row = conn.execute("SELECT * FROM evidence_bundles WHERE id=?", (bundle_id,)).fetchone()
        if row is None:
            return False
        next_path = bundle_path if bundle_path is not None else row["bundle_path"]
        next_hash = bundle_hash if bundle_hash is not None else row["bundle_hash"]
        next_status = status if status is not None else row["status"]
        next_error = error_text if error_text is not None else row["error_text"]
        with _LOCK:
            conn.execute(
                """
                UPDATE evidence_bundles
                SET bundle_path=?, bundle_hash=?, status=?, error_text=?
                WHERE id=?
                """,
                (next_path, next_hash, next_status, next_error, bundle_id),
            )
            conn.commit()
        return True

    def list_evidence_bundles_for_doc(self, doc_id: str) -> List[Dict[str, object]]:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT id, job_id, doc_id, bundle_path, bundle_hash, created_at, created_by, options_json, status, error_text
            FROM evidence_bundles
            WHERE doc_id=?
            ORDER BY created_at DESC, id DESC
            """,
            (doc_id,),
        ).fetchall()
        out: List[Dict[str, object]] = []
        for row in rows:
            try:
                options = json.loads(row["options_json"] or "{}")
                if not isinstance(options, dict):
                    options = {}
            except Exception:
                options = {}
            out.append(
                {
                    "bundleId": row["id"],
                    "jobId": row["job_id"],
                    "docId": row["doc_id"],
                    "bundlePath": row["bundle_path"],
                    "bundleHash": row["bundle_hash"],
                    "createdAt": row["created_at"],
                    "createdBy": row["created_by"],
                    "options": options,
                    "status": row["status"],
                    "errorText": row["error_text"],
                }
            )
        return out

    def get_evidence_bundle(self, bundle_id: str) -> Optional[Dict[str, object]]:
        conn = get_connection()
        row = conn.execute(
            """
            SELECT id, job_id, doc_id, bundle_path, bundle_hash, created_at, created_by, options_json, status, error_text
            FROM evidence_bundles
            WHERE id=?
            """,
            (bundle_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            options = json.loads(row["options_json"] or "{}")
            if not isinstance(options, dict):
                options = {}
        except Exception:
            options = {}
        return {
            "bundleId": row["id"],
            "jobId": row["job_id"],
            "docId": row["doc_id"],
            "bundlePath": row["bundle_path"],
            "bundleHash": row["bundle_hash"],
            "createdAt": row["created_at"],
            "createdBy": row["created_by"],
            "options": options,
            "status": row["status"],
            "errorText": row["error_text"],
        }

    def save_document(self, doc: Dict[str, object]) -> None:
        conn = get_connection()
        doc_id = str(doc["id"])
        now = _utc_now()
        extra = {
            "localPath": doc.get("localPath"),
            "scanTargetPath": doc.get("scanTargetPath"),
            "localScanTargetPath": doc.get("localScanTargetPath"),
            "tagTreePath": doc.get("tagTreePath"),
            "tagSummary": doc.get("tagSummary"),
            "fixReport": doc.get("fixReport"),
            "localFixedPath": doc.get("localFixedPath"),
            "localRebuiltPath": doc.get("localRebuiltPath"),
        }
        with _LOCK:
            conn.execute(
                """
                INSERT INTO documents(id, owner_id, filename, doc_type, created_at, status, original_path, fixed_path, rebuilt_path, tag_tree_path, extra_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
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
                    (str(doc.get("ownerId")) if doc.get("ownerId") else None),
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
            "ownerId": (row["owner_id"] if "owner_id" in row.keys() else None),
            "filename": row["filename"],
            "docType": row["doc_type"] or "pdf",
            "path": row["original_path"],
            "localPath": extra.get("localPath"),
            "fixedPath": row["fixed_path"] or None,
            "rebuiltPath": row["rebuilt_path"] or None,
            "scanTargetPath": extra.get("scanTargetPath"),
            "localScanTargetPath": extra.get("localScanTargetPath"),
            "tagTreePath": extra.get("tagTreePath"),
            "tagSummary": extra.get("tagSummary"),
            "fixReport": extra.get("fixReport"),
            "localFixedPath": extra.get("localFixedPath"),
            "localRebuiltPath": extra.get("localRebuiltPath"),
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

    def list_documents(self, owner_id: Optional[str] = None) -> List[Dict[str, object]]:
        conn = get_connection()
        if owner_id:
            rows = conn.execute(
                "SELECT id, owner_id, filename, doc_type, created_at, original_path, fixed_path, rebuilt_path FROM documents WHERE owner_id=? ORDER BY created_at DESC",
                (owner_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, owner_id, filename, doc_type, created_at, original_path, fixed_path, rebuilt_path FROM documents ORDER BY created_at DESC"
            ).fetchall()
        return [
            {
                "docId": row["id"],
                "ownerId": row["owner_id"],
                "filename": row["filename"],
                "docType": row["doc_type"] or "pdf",
                "createdAt": row["created_at"],
                "path": row["original_path"],
                "fixedPath": row["fixed_path"],
                "rebuiltPath": row["rebuilt_path"],
            }
            for row in rows
        ]

    def count_documents(self, owner_id: Optional[str] = None) -> int:
        conn = get_connection()
        if owner_id:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM documents WHERE owner_id=?", (owner_id,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS c FROM documents").fetchone()
        return int(row["c"] if row else 0)

    def _build_document_status_summaries(self, rows: List[sqlite3.Row]) -> List[Dict[str, object]]:
        from app.status.classifier import compute_doc_status

        if not rows:
            return []

        conn = get_connection()
        doc_ids = [str(row["id"]) for row in rows]
        placeholders = ",".join(["?"] * len(doc_ids))

        latest_job_by_doc: Dict[str, sqlite3.Row] = {}
        job_rows = conn.execute(
            f"""
            SELECT id, doc_id, status, started_at, finished_at
            FROM scan_jobs
            WHERE doc_id IN ({placeholders})
            ORDER BY doc_id ASC, started_at DESC, id DESC
            """,
            tuple(doc_ids),
        ).fetchall()
        for row in job_rows:
            doc_id = str(row["doc_id"])
            if doc_id not in latest_job_by_doc:
                latest_job_by_doc[doc_id] = row

        latest_job_ids = [str(row["id"]) for row in latest_job_by_doc.values()]
        job_placeholders = ",".join(["?"] * len(latest_job_ids)) if latest_job_ids else ""

        fix_report_by_doc: Dict[str, Dict[str, object]] = {}
        fix_rows = conn.execute(
            f"SELECT doc_id, report_json FROM fix_reports WHERE doc_id IN ({placeholders})",
            tuple(doc_ids),
        ).fetchall()
        for row in fix_rows:
            try:
                parsed = json.loads(row["report_json"] or "{}")
                if isinstance(parsed, dict):
                    fix_report_by_doc[str(row["doc_id"])] = parsed
            except Exception:
                continue

        manual_counts_by_doc: Dict[str, Dict[str, int]] = {
            doc_id: {"pending": 0, "approved": 0, "rejected": 0} for doc_id in doc_ids
        }
        manual_rows = conn.execute(
            f"SELECT doc_id, item_json, resolved FROM manual_review WHERE doc_id IN ({placeholders})",
            tuple(doc_ids),
        ).fetchall()
        for row in manual_rows:
            doc_id = str(row["doc_id"])
            counts = manual_counts_by_doc.get(doc_id)
            if counts is None:
                continue
            status = ""
            try:
                payload = json.loads(row["item_json"] or "{}")
                if isinstance(payload, dict):
                    status = str(payload.get("status") or "").strip().lower()
            except Exception:
                status = ""
            if status not in {"pending", "approved", "rejected"}:
                status = "rejected" if int(row["resolved"] or 0) == 1 else "pending"
            counts[status] = int(counts.get(status, 0)) + 1

        issue_aggregate: Dict[str, Dict[str, object]] = {
            doc_id: {
                "before_total": 0,
                "before_by_severity": self._empty_severity_counts(),
                "after_total": 0,
                "after_by_severity": self._empty_severity_counts(),
                "before_seen": False,
                "after_seen": False,
            }
            for doc_id in doc_ids
        }
        issue_rows = conn.execute(
            f"""
            SELECT doc_id, phase, issue_json
            FROM issues
            WHERE doc_id IN ({placeholders}) AND phase IN ('before', 'after')
            ORDER BY id ASC
            """,
            tuple(doc_ids),
        ).fetchall()
        for row in issue_rows:
            doc_id = str(row["doc_id"])
            phase = str(row["phase"] or "")
            state = issue_aggregate.get(doc_id)
            if state is None:
                continue
            try:
                issue = json.loads(row["issue_json"] or "{}")
            except Exception:
                continue
            if not isinstance(issue, dict):
                continue
            bucket = self._issue_severity_bucket(issue.get("severity"))
            if phase == "before":
                state["before_seen"] = True
                state["before_total"] = int(state["before_total"]) + 1
                by = state["before_by_severity"]
                if isinstance(by, dict):
                    by[bucket] = int(by.get(bucket, 0)) + 1
            elif phase == "after":
                state["after_seen"] = True
                state["after_total"] = int(state["after_total"]) + 1
                by = state["after_by_severity"]
                if isinstance(by, dict):
                    by[bucket] = int(by.get(bucket, 0)) + 1

        scores_by_job: Dict[str, Dict[str, Dict[str, object]]] = {}
        if latest_job_ids:
            score_rows = conn.execute(
                f"""
                SELECT job_id, pass_type, score_total, status, created_at
                FROM job_scoring
                WHERE job_id IN ({job_placeholders})
                """,
                tuple(latest_job_ids),
            ).fetchall()
            for row in score_rows:
                job_id = str(row["job_id"])
                pass_type = str(row["pass_type"])
                if job_id not in scores_by_job:
                    scores_by_job[job_id] = {}
                scores_by_job[job_id][pass_type] = {
                    "scoreTotal": int(row["score_total"] or 0),
                    "status": str(row["status"] or ""),
                    "createdAt": row["created_at"],
                }

        policy_by_job: Dict[str, Dict[str, object]] = {}
        if latest_job_ids:
            policy_rows = conn.execute(
                f"""
                SELECT job_id, policy_pack_id, policy_name, policy_version
                FROM job_policy_snapshot
                WHERE job_id IN ({job_placeholders})
                """,
                tuple(latest_job_ids),
            ).fetchall()
            for row in policy_rows:
                policy_by_job[str(row["job_id"])] = {
                    "policyPackId": row["policy_pack_id"],
                    "name": str(row["policy_name"] or ""),
                    "version": int(row["policy_version"] or 1),
                }

        summaries: List[Dict[str, object]] = []
        for row in rows:
            doc_id = str(row["id"])
            issue_state = issue_aggregate.get(doc_id, {})
            fix_report = fix_report_by_doc.get(doc_id)
            manual_counts = manual_counts_by_doc.get(doc_id, {"pending": 0, "approved": 0, "rejected": 0})

            before_counts: Optional[Dict[str, object]] = None
            after_counts: Optional[Dict[str, object]] = None
            delta_counts: Optional[Dict[str, int]] = None

            if isinstance(fix_report, dict):
                before_payload = fix_report.get("before")
                if isinstance(before_payload, dict):
                    before_by = before_payload.get("bySeverity", {})
                    before_counts = {
                        "total": int(before_payload.get("issueCount", 0) or 0),
                        "bySeverity": self._normalized_severity_counts(before_by if isinstance(before_by, dict) else {}),
                    }
                after_payload = fix_report.get("after")
                if isinstance(after_payload, dict):
                    after_by = after_payload.get("bySeverity", {})
                    after_counts = {
                        "total": int(after_payload.get("issueCount", 0) or 0),
                        "bySeverity": self._normalized_severity_counts(after_by if isinstance(after_by, dict) else {}),
                    }
                delta_payload = fix_report.get("delta")
                if isinstance(delta_payload, dict):
                    fixed_list = delta_payload.get("fixed", [])
                    remaining_list = delta_payload.get("remaining", [])
                    introduced_list = delta_payload.get("introduced", [])
                    delta_counts = {
                        "fixed": len(fixed_list) if isinstance(fixed_list, list) else 0,
                        "remaining": len(remaining_list) if isinstance(remaining_list, list) else 0,
                        "introduced": len(introduced_list) if isinstance(introduced_list, list) else 0,
                    }

            if before_counts is None and bool(issue_state.get("before_seen")):
                before_counts = {
                    "total": int(issue_state.get("before_total", 0) or 0),
                    "bySeverity": issue_state.get("before_by_severity", self._empty_severity_counts()),
                }
            if after_counts is None and bool(issue_state.get("after_seen")):
                after_counts = {
                    "total": int(issue_state.get("after_total", 0) or 0),
                    "bySeverity": issue_state.get("after_by_severity", self._empty_severity_counts()),
                }

            latest_job_row = latest_job_by_doc.get(doc_id)
            latest_job: Optional[Dict[str, object]] = None
            job_id: Optional[str] = None
            normalized_job_status = ""
            if latest_job_row is not None:
                job_id = str(latest_job_row["id"])
                normalized_job_status = self._normalize_job_status(latest_job_row["status"])
                latest_job = {
                    "jobId": job_id,
                    "status": normalized_job_status,
                    "startedAt": latest_job_row["started_at"],
                    "finishedAt": latest_job_row["finished_at"],
                }

            score_map = scores_by_job.get(job_id or "", {})
            score_summary = {
                "baseline": score_map.get("baseline"),
                "postFix": score_map.get("post_fix"),
                "postManual": score_map.get("post_manual"),
            }
            preferred_score_status = ""
            for key in ("postManual", "postFix", "baseline"):
                entry = score_summary.get(key)
                if isinstance(entry, dict) and str(entry.get("status") or ""):
                    preferred_score_status = str(entry.get("status") or "")
                    break

            critical_remaining = 0
            if isinstance(after_counts, dict):
                by = after_counts.get("bySeverity")
                if isinstance(by, dict):
                    critical_remaining = int(by.get("critical", 0) or 0)

            remaining_count = 0
            introduced_count = 0
            if isinstance(delta_counts, dict):
                remaining_count = int(delta_counts.get("remaining", 0) or 0)
                introduced_count = int(delta_counts.get("introduced", 0) or 0)
            elif isinstance(after_counts, dict):
                remaining_count = int(after_counts.get("total", 0) or 0)

            classification = compute_doc_status(
                {
                    "latestJobStatus": normalized_job_status,
                    "hasJobs": latest_job_row is not None,
                    "hasFixReport": isinstance(fix_report, dict),
                    "pendingManual": int(manual_counts.get("pending", 0) or 0),
                    "remainingCount": remaining_count,
                    "introducedCount": introduced_count,
                    "criticalRemaining": critical_remaining,
                    "preferredScoreStatus": preferred_score_status,
                }
            )

            summaries.append(
                {
                    "docId": doc_id,
                    "filename": str(row["filename"] or ""),
                    "docType": str(row["doc_type"] or "unknown"),
                    "createdAt": row["created_at"],
                    "latestJob": latest_job,
                    "policy": policy_by_job.get(job_id or ""),
                    "score": score_summary,
                    "counts": {
                        "before": before_counts,
                        "after": after_counts,
                        "delta": delta_counts,
                        "manualReview": {
                            "pending": int(manual_counts.get("pending", 0) or 0),
                            "approved": int(manual_counts.get("approved", 0) or 0),
                            "rejected": int(manual_counts.get("rejected", 0) or 0),
                        },
                    },
                    "status": str(classification.get("status") or "needs_review"),
                    "reasons": classification.get("reasons", []),
                }
            )

        return summaries

    def list_documents_with_status(self, limit: int = 50, offset: int = 0, owner_id: Optional[str] = None) -> List[Dict[str, object]]:
        conn = get_connection()
        if owner_id:
            rows = conn.execute(
                """
                SELECT id, filename, doc_type, created_at, original_path, fixed_path, rebuilt_path
                FROM documents
                WHERE owner_id=?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (owner_id, max(1, int(limit or 50)), max(0, int(offset or 0))),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, filename, doc_type, created_at, original_path, fixed_path, rebuilt_path
                FROM documents
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (max(1, int(limit or 50)), max(0, int(offset or 0))),
            ).fetchall()
        return self._build_document_status_summaries(list(rows))

    def get_document_status(self, doc_id: str) -> Optional[Dict[str, object]]:
        conn = get_connection()
        row = conn.execute(
            """
            SELECT id, filename, doc_type, created_at, original_path, fixed_path, rebuilt_path
            FROM documents
            WHERE id=?
            """,
            (doc_id,),
        ).fetchone()
        if row is None:
            return None
        items = self._build_document_status_summaries([row])
        return items[0] if items else None

    def save_job(self, job: Dict[str, object]) -> None:
        conn = get_connection()
        now = _utc_now()
        with _LOCK:
            conn.execute(
                """
                INSERT INTO scan_jobs(id, doc_id, status, progress, message, started_at, finished_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  -- Progress updates may rewrite status/message, never the
                  -- document. Ownership is resolved job -> doc -> owner, so a
                  -- job whose doc_id can move is a job whose OWNER can move.
                  doc_id=CASE WHEN COALESCE(scan_jobs.doc_id,'')='' THEN excluded.doc_id ELSE scan_jobs.doc_id END,
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

    def get_latest_job_for_doc(self, doc_id: str) -> Optional[Dict[str, object]]:
        conn = get_connection()
        row = conn.execute(
            """
            SELECT id, doc_id, status, progress, message
            FROM scan_jobs
            WHERE doc_id=?
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """,
            (doc_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "jobId": row["id"],
            "docId": row["doc_id"],
            "status": row["status"],
            "progress": int(row["progress"] or 0),
            "message": row["message"],
        }

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

    def add_manual_review_items(
        self,
        doc_id: str,
        items: List[Dict[str, object]],
        owner_id: Optional[str] = None,
    ) -> None:
        if not items:
            return
        conn = get_connection()
        now = _utc_now()
        with _LOCK:
            for item in items:
                item_id = str(item.get("id") or f"mr-{doc_id}-{int(datetime.now(UTC).timestamp() * 1000)}")
                ai_decision = item.get("aiDecision") if isinstance(item.get("aiDecision"), dict) else None
                # The UPSERT only ever rewrites a row the SAME owner already
                # wrote: doc_id and owner_id are immutable once set, and the
                # DO UPDATE is skipped entirely when the id belongs to somebody
                # else. Without that guard an id a caller can reconstruct is
                # enough to overwrite another tenant's queued item.
                conn.execute(
                    """
                    INSERT INTO manual_review(id, doc_id, owner_id, item_json, created_at, resolved, ai_decision_json, ai_confidence, ai_status, validator_status, ai_model, ai_updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                      item_json=excluded.item_json,
                      ai_decision_json=excluded.ai_decision_json,
                      ai_confidence=excluded.ai_confidence,
                      ai_status=excluded.ai_status,
                      validator_status=excluded.validator_status,
                      ai_model=excluded.ai_model,
                      ai_updated_at=excluded.ai_updated_at
                    WHERE manual_review.owner_id IS excluded.owner_id
                    """,
                    (
                        item_id,
                        doc_id,
                        owner_id or None,
                        json.dumps(item),
                        str(item.get("createdAt") or now),
                        0,
                        json.dumps(ai_decision) if ai_decision is not None else None,
                        float(item.get("aiConfidence")) if item.get("aiConfidence") is not None else None,
                        str(item.get("aiStatus")) if item.get("aiStatus") is not None else None,
                        str(item.get("validatorStatus")) if item.get("validatorStatus") is not None else None,
                        str(item.get("aiModel")) if item.get("aiModel") is not None else None,
                        str(item.get("aiUpdatedAt")) if item.get("aiUpdatedAt") is not None else None,
                    ),
                )
            conn.commit()

    def list_manual_review_items(self) -> List[Dict[str, object]]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT item_json, ai_decision_json, ai_confidence, ai_status, validator_status, ai_model, ai_updated_at FROM manual_review WHERE COALESCE(resolved,0)=0 ORDER BY created_at DESC"
        ).fetchall()
        out: List[Dict[str, object]] = []
        for row in rows:
            try:
                item = json.loads(row["item_json"])
                if isinstance(item, dict):
                    if row["ai_decision_json"] and "aiDecision" not in item:
                        try:
                            item["aiDecision"] = json.loads(row["ai_decision_json"])
                        except Exception:
                            pass
                    if row["ai_confidence"] is not None:
                        item["aiConfidence"] = float(row["ai_confidence"])
                    if row["ai_status"] is not None:
                        item["aiStatus"] = str(row["ai_status"])
                    if row["validator_status"] is not None:
                        item["validatorStatus"] = str(row["validator_status"])
                    if row["ai_model"] is not None:
                        item["aiModel"] = str(row["ai_model"])
                    if row["ai_updated_at"] is not None:
                        item["aiUpdatedAt"] = str(row["ai_updated_at"])
                    out.append(item)
            except Exception:
                continue
        return out

    def list_manual_review_items_for_doc(
        self,
        doc_id: str,
        include_resolved: bool = False,
        owner_id: Optional[str] = None,
    ) -> List[Dict[str, object]]:
        conn = get_connection()
        where = ["doc_id=?"]
        params: List[object] = [doc_id]
        if not include_resolved:
            where.append("COALESCE(resolved,0)=0")
        if owner_id:
            # doc_id is derived from an uploaded filename, so it alone cannot
            # decide whose queue this is. owner_id NULL = written before the
            # column existed; those rows belong to the document's owner, who is
            # the only caller that reaches this far.
            where.append("(owner_id IS NULL OR owner_id=?)")
            params.append(owner_id)
        rows = conn.execute(
            "SELECT item_json, ai_decision_json, ai_confidence, ai_status, validator_status, ai_model, ai_updated_at "
            f"FROM manual_review WHERE {' AND '.join(where)} ORDER BY created_at DESC",
            tuple(params),
        ).fetchall()
        out: List[Dict[str, object]] = []
        for row in rows:
            try:
                item = json.loads(row["item_json"])
                if isinstance(item, dict):
                    if row["ai_decision_json"] and "aiDecision" not in item:
                        try:
                            item["aiDecision"] = json.loads(row["ai_decision_json"])
                        except Exception:
                            pass
                    if row["ai_confidence"] is not None:
                        item["aiConfidence"] = float(row["ai_confidence"])
                    if row["ai_status"] is not None:
                        item["aiStatus"] = str(row["ai_status"])
                    if row["validator_status"] is not None:
                        item["validatorStatus"] = str(row["validator_status"])
                    if row["ai_model"] is not None:
                        item["aiModel"] = str(row["ai_model"])
                    if row["ai_updated_at"] is not None:
                        item["aiUpdatedAt"] = str(row["ai_updated_at"])
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
        row = conn.execute(
            "SELECT doc_id, item_json, ai_decision_json, ai_confidence, ai_status, validator_status, ai_model, ai_updated_at FROM manual_review WHERE id=?",
            (item_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["item_json"])
            if isinstance(payload, dict):
                payload.setdefault("docId", row["doc_id"])
                if row["ai_decision_json"] and "aiDecision" not in payload:
                    try:
                        payload["aiDecision"] = json.loads(row["ai_decision_json"])
                    except Exception:
                        pass
                if row["ai_confidence"] is not None:
                    payload["aiConfidence"] = float(row["ai_confidence"])
                if row["ai_status"] is not None:
                    payload["aiStatus"] = str(row["ai_status"])
                if row["validator_status"] is not None:
                    payload["validatorStatus"] = str(row["validator_status"])
                if row["ai_model"] is not None:
                    payload["aiModel"] = str(row["ai_model"])
                if row["ai_updated_at"] is not None:
                    payload["aiUpdatedAt"] = str(row["ai_updated_at"])
                return payload
            return None
        except Exception:
            return None

    def update_manual_review_item(self, item_id: str, item: Dict[str, object], resolved: bool = False) -> bool:
        conn = get_connection()
        with _LOCK:
            exists = conn.execute("SELECT 1 FROM manual_review WHERE id=?", (item_id,)).fetchone()
            if exists is None:
                return False
            conn.execute(
                "UPDATE manual_review SET item_json=?, resolved=?, ai_decision_json=?, ai_confidence=?, ai_status=?, validator_status=?, ai_model=?, ai_updated_at=? WHERE id=?",
                (
                    json.dumps(item),
                    1 if resolved else 0,
                    json.dumps(item.get("aiDecision")) if isinstance(item.get("aiDecision"), dict) else None,
                    float(item.get("aiConfidence")) if item.get("aiConfidence") is not None else None,
                    str(item.get("aiStatus")) if item.get("aiStatus") is not None else None,
                    str(item.get("validatorStatus")) if item.get("validatorStatus") is not None else None,
                    str(item.get("aiModel")) if item.get("aiModel") is not None else None,
                    str(item.get("aiUpdatedAt")) if item.get("aiUpdatedAt") is not None else None,
                        item_id,
                ),
            )
            conn.commit()
            return True


_REPO: object | None = None


def get_repo():
    global _REPO
    settings = get_settings()
    wants_postgres = settings.database_url.startswith("postgres")
    if _REPO is not None:
        if wants_postgres and isinstance(_REPO, PostgresRepository):
            return _REPO
        if (not wants_postgres) and isinstance(_REPO, SqliteRepo):
            return _REPO
        _REPO = None
    if settings.database_url.startswith("postgres"):
        _REPO = PostgresRepository()
        print("[db] using PostgresRepository")
    else:
        _REPO = SqliteRepo()
        print("[db] using SqliteRepo")
    return _REPO
