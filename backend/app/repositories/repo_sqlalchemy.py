from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import delete, select

from app.db.models import Base, DocumentRow, FixReportRow, IssueRow, ManualReviewRow, ScanJobRow
from app.db.session import ENGINE, SessionLocal
from app.repositories.base import Repository


def _loads(value: Optional[str], default: object) -> object:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


class SqlAlchemyRepository(Repository):
    def __init__(self) -> None:
        Base.metadata.create_all(bind=ENGINE)

    def save_document(self, doc: Dict[str, object]) -> None:
        with SessionLocal() as db:
            row = db.get(DocumentRow, str(doc["id"]))
            if row is None:
                row = DocumentRow(
                    id=str(doc["id"]),
                    filename=str(doc.get("filename", "")),
                    doc_type=str(doc.get("docType", "pdf")),
                    original_path=str(doc.get("path", "")),
                    created_at=datetime.utcnow(),
                )
                db.add(row)
            row.filename = str(doc.get("filename", row.filename))
            row.doc_type = str(doc.get("docType", row.doc_type))
            row.original_path = str(doc.get("path", row.original_path))
            row.fixed_path = str(doc.get("fixedPath")) if doc.get("fixedPath") else row.fixed_path
            row.rebuilt_path = str(doc.get("rebuiltPath")) if doc.get("rebuiltPath") else row.rebuilt_path
            row.tag_tree_path = str(doc.get("tagTreePath")) if doc.get("tagTreePath") else row.tag_tree_path
            row.status = str(doc.get("status")) if doc.get("status") else row.status
            extras = {
                "tagSummary": doc.get("tagSummary"),
                "fixReport": doc.get("fixReport"),
            }
            row.extra_json = json.dumps(extras)
            db.commit()

    def get_document(self, doc_id: str) -> Optional[Dict[str, object]]:
        with SessionLocal() as db:
            row = db.get(DocumentRow, doc_id)
            if row is None:
                return None
            extras = _loads(row.extra_json, {})
            if not isinstance(extras, dict):
                extras = {}
            result: Dict[str, object] = {
                "id": row.id,
                "filename": row.filename,
                "docType": row.doc_type,
                "path": row.original_path,
            }
            if row.fixed_path:
                result["fixedPath"] = row.fixed_path
            if row.rebuilt_path:
                result["rebuiltPath"] = row.rebuilt_path
            if row.tag_tree_path:
                result["tagTreePath"] = row.tag_tree_path
            if row.status:
                result["status"] = row.status
            if extras.get("tagSummary") is not None:
                result["tagSummary"] = extras.get("tagSummary")
            if extras.get("fixReport") is not None:
                result["fixReport"] = extras.get("fixReport")
            return result

    def update_document(self, doc_id: str, updates: Dict[str, object]) -> None:
        current = self.get_document(doc_id)
        if current is None:
            return
        merged = dict(current)
        merged.update(updates)
        merged["id"] = doc_id
        self.save_document(merged)

    def list_documents(self) -> List[Dict[str, object]]:
        with SessionLocal() as db:
            rows = db.execute(select(DocumentRow).order_by(DocumentRow.created_at.desc())).scalars().all()
            out: List[Dict[str, object]] = []
            for row in rows:
                out.append(
                    {
                        "docId": row.id,
                        "filename": row.filename,
                        "docType": row.doc_type,
                        "createdAt": row.created_at.isoformat() + "Z",
                        "path": row.original_path,
                        "fixedPath": row.fixed_path,
                        "rebuiltPath": row.rebuilt_path,
                    }
                )
            return out

    def save_job(self, job: Dict[str, object]) -> None:
        with SessionLocal() as db:
            row = db.get(ScanJobRow, str(job["jobId"]))
            if row is None:
                row = ScanJobRow(
                    id=str(job["jobId"]),
                    doc_id=str(job.get("docId", "")),
                    status=str(job.get("status", "queued")),
                    progress=int(job.get("progress", 0)),
                    message=str(job.get("message")) if job.get("message") is not None else None,
                )
                db.add(row)
            row.status = str(job.get("status", row.status))
            row.progress = int(job.get("progress", row.progress))
            row.message = str(job.get("message")) if job.get("message") is not None else row.message
            if row.status in {"done", "error"}:
                row.finished_at = datetime.utcnow()
            db.commit()

    def get_job(self, job_id: str) -> Optional[Dict[str, object]]:
        with SessionLocal() as db:
            row = db.get(ScanJobRow, job_id)
            if row is None:
                return None
            return {
                "jobId": row.id,
                "docId": row.doc_id,
                "status": row.status,
                "progress": row.progress,
                "message": row.message,
            }

    def update_job(self, job_id: str, updates: Dict[str, object]) -> None:
        current = self.get_job(job_id)
        if current is None:
            return
        current.update(updates)
        self.save_job(current)

    def save_issues(self, doc_id: str, phase: str, issues: List[Dict[str, object]], keys: List[str]) -> None:
        with SessionLocal() as db:
            db.execute(delete(IssueRow).where(IssueRow.doc_id == doc_id, IssueRow.phase == phase))
            for issue, key in zip(issues, keys):
                db.add(IssueRow(doc_id=doc_id, phase=phase, issue_json=json.dumps(issue), issue_key=key))
            db.commit()

    def get_issues(self, doc_id: str, phase: str) -> List[Dict[str, object]]:
        with SessionLocal() as db:
            rows = db.execute(
                select(IssueRow).where(IssueRow.doc_id == doc_id, IssueRow.phase == phase).order_by(IssueRow.id.asc())
            ).scalars().all()
            out: List[Dict[str, object]] = []
            for row in rows:
                parsed = _loads(row.issue_json, {})
                if isinstance(parsed, dict):
                    out.append(parsed)
            return out

    def get_latest_issues(self, doc_id: str) -> List[Dict[str, object]]:
        after = self.get_issues(doc_id, "after")
        if after:
            return after
        return self.get_issues(doc_id, "before")

    def save_fix_report(self, doc_id: str, report: Dict[str, object]) -> None:
        with SessionLocal() as db:
            row = db.get(FixReportRow, doc_id)
            if row is None:
                row = FixReportRow(doc_id=doc_id, report_json=json.dumps(report))
                db.add(row)
            else:
                row.report_json = json.dumps(report)
                row.created_at = datetime.utcnow()
            db.commit()

    def get_fix_report(self, doc_id: str) -> Optional[Dict[str, object]]:
        with SessionLocal() as db:
            row = db.get(FixReportRow, doc_id)
            if row is None:
                return None
            parsed = _loads(row.report_json, None)
            return parsed if isinstance(parsed, dict) else None

    def add_manual_review_items(self, doc_id: str, items: List[Dict[str, object]]) -> None:
        if not items:
            return
        with SessionLocal() as db:
            for item in items:
                item_id = str(item.get("id", f"mr-{doc_id}-{int(datetime.utcnow().timestamp() * 1000)}"))
                row = db.get(ManualReviewRow, item_id)
                if row is not None:
                    row.item_json = json.dumps(item)
                    row.doc_id = doc_id
                    row.resolved = False
                    row.created_at = datetime.utcnow()
                else:
                    db.add(ManualReviewRow(id=item_id, doc_id=doc_id, item_json=json.dumps(item), resolved=False))
            db.commit()

    def list_manual_review_items(self) -> List[Dict[str, object]]:
        with SessionLocal() as db:
            rows = db.execute(
                select(ManualReviewRow).where(ManualReviewRow.resolved.is_(False)).order_by(ManualReviewRow.created_at.desc())
            ).scalars().all()
            out: List[Dict[str, object]] = []
            for row in rows:
                parsed = _loads(row.item_json, {})
                if isinstance(parsed, dict):
                    out.append(parsed)
            return out

    def clear_manual_review_items(self) -> int:
        with SessionLocal() as db:
            rows = db.execute(
                select(ManualReviewRow).where(ManualReviewRow.resolved.is_(False))
            ).scalars().all()
            count = len(rows)
            for row in rows:
                row.resolved = True
            db.commit()
            return count

    def get_manual_review_item(self, item_id: str) -> Optional[Dict[str, object]]:
        with SessionLocal() as db:
            row = db.get(ManualReviewRow, item_id)
            if row is None:
                return None
            parsed = _loads(row.item_json, None)
            return parsed if isinstance(parsed, dict) else None

    def update_manual_review_item(self, item_id: str, item: Dict[str, object], resolved: bool = False) -> bool:
        with SessionLocal() as db:
            row = db.get(ManualReviewRow, item_id)
            if row is None:
                return False
            row.item_json = json.dumps(item)
            row.resolved = resolved
            db.commit()
            return True
