from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import delete, select

from app.db.models import (
    DocumentRow,
    EvidenceBundleRow,
    FixReportRow,
    IssueRow,
    JobPolicySnapshotRow,
    JobScoringRow,
    ManualReviewRow,
    PolicyPackRow,
    ScanJobRow,
)
from app.db.session_sqlalchemy import SessionLocal
from app.repositories.base import Repository
from app.status.classifier import compute_doc_status


def _loads(value: Optional[str], default: object) -> object:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


class SqlAlchemyRepository(Repository):
    def __init__(self) -> None:
        # Schema is managed by Alembic migrations.
        return None

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
                "localPath": doc.get("localPath"),
                "scanTargetPath": doc.get("scanTargetPath"),
                "localScanTargetPath": doc.get("localScanTargetPath"),
                "tagSummary": doc.get("tagSummary"),
                "fixReport": doc.get("fixReport"),
                "localFixedPath": doc.get("localFixedPath"),
                "localRebuiltPath": doc.get("localRebuiltPath"),
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
            if extras.get("localPath") is not None:
                result["localPath"] = extras.get("localPath")
            if row.fixed_path:
                result["fixedPath"] = row.fixed_path
            if row.rebuilt_path:
                result["rebuiltPath"] = row.rebuilt_path
            if row.tag_tree_path:
                result["tagTreePath"] = row.tag_tree_path
            if extras.get("scanTargetPath") is not None:
                result["scanTargetPath"] = extras.get("scanTargetPath")
            if extras.get("localScanTargetPath") is not None:
                result["localScanTargetPath"] = extras.get("localScanTargetPath")
            if row.status:
                result["status"] = row.status
            if extras.get("tagSummary") is not None:
                result["tagSummary"] = extras.get("tagSummary")
            if extras.get("fixReport") is not None:
                result["fixReport"] = extras.get("fixReport")
            if extras.get("localFixedPath") is not None:
                result["localFixedPath"] = extras.get("localFixedPath")
            if extras.get("localRebuiltPath") is not None:
                result["localRebuiltPath"] = extras.get("localRebuiltPath")
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

    def list_manual_review_items_for_doc(self, doc_id: str, include_resolved: bool = False) -> List[Dict[str, object]]:
        with SessionLocal() as db:
            stmt = select(ManualReviewRow).where(ManualReviewRow.doc_id == doc_id)
            if not include_resolved:
                stmt = stmt.where(ManualReviewRow.resolved.is_(False))
            rows = db.execute(stmt.order_by(ManualReviewRow.created_at.desc())).scalars().all()
            out: List[Dict[str, object]] = []
            for row in rows:
                parsed = _loads(row.item_json, {})
                if isinstance(parsed, dict):
                    out.append(parsed)
            return out

    def count_documents(self) -> int:
        with SessionLocal() as db:
            return len(db.execute(select(DocumentRow.id)).all())

    def get_latest_job_for_doc(self, doc_id: str) -> Optional[Dict[str, object]]:
        with SessionLocal() as db:
            row = db.execute(
                select(ScanJobRow).where(ScanJobRow.doc_id == doc_id).order_by(ScanJobRow.started_at.desc(), ScanJobRow.id.desc()).limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            return {
                "jobId": row.id,
                "docId": row.doc_id,
                "status": row.status,
                "progress": int(row.progress or 0),
                "message": row.message,
            }

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

    def _build_document_status_summaries(self, rows: List[DocumentRow]) -> List[Dict[str, object]]:
        if not rows:
            return []
        doc_ids = [row.id for row in rows]
        with SessionLocal() as db:
            latest_job_by_doc: Dict[str, ScanJobRow] = {}
            job_rows = db.execute(
                select(ScanJobRow).where(ScanJobRow.doc_id.in_(doc_ids)).order_by(ScanJobRow.doc_id.asc(), ScanJobRow.started_at.desc(), ScanJobRow.id.desc())
            ).scalars().all()
            for row in job_rows:
                if row.doc_id not in latest_job_by_doc:
                    latest_job_by_doc[row.doc_id] = row

            fix_report_by_doc: Dict[str, Dict[str, object]] = {}
            fix_rows = db.execute(select(FixReportRow).where(FixReportRow.doc_id.in_(doc_ids))).scalars().all()
            for row in fix_rows:
                parsed = _loads(row.report_json, {})
                if isinstance(parsed, dict):
                    fix_report_by_doc[row.doc_id] = parsed

            manual_counts_by_doc: Dict[str, Dict[str, int]] = {doc_id: {"pending": 0, "approved": 0, "rejected": 0} for doc_id in doc_ids}
            manual_rows = db.execute(select(ManualReviewRow).where(ManualReviewRow.doc_id.in_(doc_ids))).scalars().all()
            for row in manual_rows:
                counts = manual_counts_by_doc.get(row.doc_id)
                if counts is None:
                    continue
                status = ""
                payload = _loads(row.item_json, {})
                if isinstance(payload, dict):
                    status = str(payload.get("status") or "").strip().lower()
                if status not in {"pending", "approved", "rejected"}:
                    status = "rejected" if bool(row.resolved) else "pending"
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
            issue_rows = db.execute(
                select(IssueRow).where(IssueRow.doc_id.in_(doc_ids), IssueRow.phase.in_(["before", "after"])).order_by(IssueRow.id.asc())
            ).scalars().all()
            for row in issue_rows:
                bucket = issue_aggregate.get(row.doc_id)
                if bucket is None:
                    continue
                parsed = _loads(row.issue_json, {})
                if not isinstance(parsed, dict):
                    continue
                severity = self._issue_severity_bucket(parsed.get("severity"))
                if row.phase == "before":
                    bucket["before_seen"] = True
                    bucket["before_total"] = int(bucket.get("before_total", 0)) + 1
                    before_map = bucket["before_by_severity"]
                    if isinstance(before_map, dict):
                        before_map[severity] = int(before_map.get(severity, 0)) + 1
                else:
                    bucket["after_seen"] = True
                    bucket["after_total"] = int(bucket.get("after_total", 0)) + 1
                    after_map = bucket["after_by_severity"]
                    if isinstance(after_map, dict):
                        after_map[severity] = int(after_map.get(severity, 0)) + 1

            latest_job_ids = [row.id for row in latest_job_by_doc.values()]
            score_by_job: Dict[str, Dict[str, Dict[str, object]]] = {}
            policy_by_job: Dict[str, Dict[str, object]] = {}
            if latest_job_ids:
                score_rows = db.execute(select(JobScoringRow).where(JobScoringRow.job_id.in_(latest_job_ids))).scalars().all()
                for score_row in score_rows:
                    counts = _loads(score_row.counts_by_severity, {})
                    if not isinstance(counts, dict):
                        counts = {}
                    score_by_job.setdefault(score_row.job_id, {})[score_row.pass_type] = {
                        "scoreTotal": int(score_row.score_total or 0),
                        "status": score_row.status,
                        "createdAt": score_row.created_at.isoformat() + "Z" if score_row.created_at else None,
                        "countsBySeverity": self._normalized_severity_counts(counts),
                    }
                policy_rows = db.execute(select(JobPolicySnapshotRow).where(JobPolicySnapshotRow.job_id.in_(latest_job_ids))).scalars().all()
                for policy_row in policy_rows:
                    policy_by_job[policy_row.job_id] = {
                        "policyPackId": policy_row.policy_pack_id,
                        "name": policy_row.policy_name,
                        "version": int(policy_row.policy_version or 1),
                    }

            summaries: List[Dict[str, object]] = []
            for row in rows:
                doc_id = row.id
                job_row = latest_job_by_doc.get(doc_id)
                job_id = job_row.id if job_row else None
                fix_report = fix_report_by_doc.get(doc_id, {})
                manual_counts = manual_counts_by_doc.get(doc_id, {"pending": 0, "approved": 0, "rejected": 0})
                agg = issue_aggregate.get(doc_id, {})

                scores = score_by_job.get(job_id or "", {})
                baseline = scores.get("baseline")
                post_fix = scores.get("post_fix")
                post_manual = scores.get("post_manual")
                preferred_score = post_manual or post_fix or baseline
                preferred_score_status = str(preferred_score.get("status") or "").strip().lower() if isinstance(preferred_score, dict) else ""

                delta_payload = fix_report.get("delta", {}) if isinstance(fix_report, dict) else {}
                fixed_list = delta_payload.get("fixed", []) if isinstance(delta_payload, dict) else []
                remaining_list = delta_payload.get("remaining", []) if isinstance(delta_payload, dict) else []
                introduced_list = delta_payload.get("introduced", []) if isinstance(delta_payload, dict) else []

                critical_remaining = 0
                if isinstance(remaining_list, list):
                    for issue in remaining_list:
                        if isinstance(issue, dict):
                            sev = str(issue.get("severity") or "").strip().lower()
                            if sev in {"critical", "error"}:
                                critical_remaining += 1

                classification = compute_doc_status(
                    {
                        "latestJobStatus": self._normalize_job_status(job_row.status) if job_row else "",
                        "hasJobs": job_row is not None,
                        "hasFixReport": bool(fix_report),
                        "pendingManual": int(manual_counts.get("pending", 0) or 0),
                        "remainingCount": len(remaining_list) if isinstance(remaining_list, list) else 0,
                        "introducedCount": len(introduced_list) if isinstance(introduced_list, list) else 0,
                        "criticalRemaining": critical_remaining,
                        "preferredScoreStatus": preferred_score_status,
                    }
                )

                summaries.append(
                    {
                        "docId": doc_id,
                        "filename": row.filename,
                        "docType": row.doc_type or "pdf",
                        "createdAt": row.created_at.isoformat() + "Z" if row.created_at else None,
                        "latestJob": (
                            {
                                "jobId": job_row.id,
                                "status": self._normalize_job_status(job_row.status),
                                "startedAt": job_row.started_at.isoformat() + "Z" if job_row.started_at else None,
                                "finishedAt": job_row.finished_at.isoformat() + "Z" if job_row.finished_at else None,
                            }
                            if job_row
                            else None
                        ),
                        "policy": policy_by_job.get(job_id or ""),
                        "score": {"baseline": baseline, "postFix": post_fix, "postManual": post_manual},
                        "counts": {
                            "before": (
                                {"total": int(agg.get("before_total", 0) or 0), "bySeverity": agg.get("before_by_severity", self._empty_severity_counts())}
                                if bool(agg.get("before_seen", False))
                                else None
                            ),
                            "after": (
                                {"total": int(agg.get("after_total", 0) or 0), "bySeverity": agg.get("after_by_severity", self._empty_severity_counts())}
                                if bool(agg.get("after_seen", False))
                                else None
                            ),
                            "delta": (
                                {
                                    "fixed": len(fixed_list) if isinstance(fixed_list, list) else 0,
                                    "remaining": len(remaining_list) if isinstance(remaining_list, list) else 0,
                                    "introduced": len(introduced_list) if isinstance(introduced_list, list) else 0,
                                }
                                if bool(fix_report)
                                else None
                            ),
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

    def list_documents_with_status(self, limit: int = 50, offset: int = 0) -> List[Dict[str, object]]:
        with SessionLocal() as db:
            rows = db.execute(
                select(DocumentRow)
                .order_by(DocumentRow.created_at.desc())
                .limit(max(1, int(limit or 50)))
                .offset(max(0, int(offset or 0)))
            ).scalars().all()
        return self._build_document_status_summaries(rows)

    def get_document_status(self, doc_id: str) -> Optional[Dict[str, object]]:
        with SessionLocal() as db:
            row = db.get(DocumentRow, doc_id)
            if row is None:
                return None
        items = self._build_document_status_summaries([row])
        return items[0] if items else None

    def list_policy_packs(self) -> List[Dict[str, object]]:
        with SessionLocal() as db:
            rows = db.execute(select(PolicyPackRow).where(PolicyPackRow.is_active.is_(True)).order_by(PolicyPackRow.name.asc())).scalars().all()
            out: List[Dict[str, object]] = []
            for row in rows:
                policy_json = _loads(row.policy_json, {})
                if not isinstance(policy_json, dict):
                    policy_json = {}
                targets = policy_json.get("targets", [])
                if not isinstance(targets, list):
                    targets = []
                out.append(
                    {
                        "id": row.id,
                        "name": row.name,
                        "description": row.description or "",
                        "version": int(row.version or 1),
                        "targets": [str(target) for target in targets],
                        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
                        "policy_json": policy_json,
                    }
                )
            return out

    def get_policy_pack(self, policy_id: str) -> Optional[Dict[str, object]]:
        with SessionLocal() as db:
            row = db.get(PolicyPackRow, policy_id)
            if row is None:
                return None
            policy_json = _loads(row.policy_json, {})
            if not isinstance(policy_json, dict):
                policy_json = {}
            targets = policy_json.get("targets", [])
            if not isinstance(targets, list):
                targets = []
            return {
                "id": row.id,
                "name": row.name,
                "description": row.description or "",
                "version": int(row.version or 1),
                "targets": [str(target) for target in targets],
                "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
                "policy_json": policy_json,
            }

    def save_job_policy_snapshot(
        self,
        job_id: str,
        policy_pack_id: Optional[str],
        policy_name: str,
        policy_version: int,
        policy_json: Dict[str, object],
    ) -> None:
        with SessionLocal() as db:
            row = db.get(JobPolicySnapshotRow, job_id)
            if row is None:
                row = JobPolicySnapshotRow(
                    job_id=job_id,
                    policy_pack_id=policy_pack_id,
                    policy_name=policy_name,
                    policy_version=int(policy_version),
                    policy_json=json.dumps(policy_json),
                    created_at=datetime.utcnow(),
                )
                db.add(row)
            else:
                row.policy_pack_id = policy_pack_id
                row.policy_name = policy_name
                row.policy_version = int(policy_version)
                row.policy_json = json.dumps(policy_json)
            db.commit()

    def get_job_policy_snapshot(self, job_id: str) -> Optional[Dict[str, object]]:
        with SessionLocal() as db:
            row = db.get(JobPolicySnapshotRow, job_id)
            if row is None:
                return None
            payload = _loads(row.policy_json, {})
            if not isinstance(payload, dict):
                payload = {}
            return {
                "jobId": row.job_id,
                "policyPackId": row.policy_pack_id,
                "policyName": row.policy_name,
                "policyVersion": int(row.policy_version or 1),
                "policyJson": payload,
                "createdAt": row.created_at.isoformat() + "Z" if row.created_at else None,
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
        with SessionLocal() as db:
            row = db.execute(select(JobScoringRow).where(JobScoringRow.job_id == job_id, JobScoringRow.pass_type == pass_type)).scalar_one_or_none()
            if row is None:
                row = JobScoringRow(
                    job_id=job_id,
                    pass_type=pass_type,
                    score_total=int(score_total),
                    status=status,
                    counts_by_severity=json.dumps(counts_by_severity),
                    points_by_category=json.dumps(points_by_category),
                    coverage=json.dumps(coverage),
                    created_at=datetime.utcnow(),
                )
                db.add(row)
            else:
                row.score_total = int(score_total)
                row.status = status
                row.counts_by_severity = json.dumps(counts_by_severity)
                row.points_by_category = json.dumps(points_by_category)
                row.coverage = json.dumps(coverage)
                row.created_at = datetime.utcnow()
            db.commit()

    def get_job_scores(self, job_id: str) -> List[Dict[str, object]]:
        with SessionLocal() as db:
            rows = db.execute(select(JobScoringRow).where(JobScoringRow.job_id == job_id).order_by(JobScoringRow.created_at.asc())).scalars().all()
            ordering = {"baseline": 0, "post_fix": 1, "post_manual": 2}
            rows = sorted(rows, key=lambda row: (ordering.get(row.pass_type, 9), row.created_at))
            out: List[Dict[str, object]] = []
            for row in rows:
                counts = _loads(row.counts_by_severity, {})
                if not isinstance(counts, dict):
                    counts = {}
                points = _loads(row.points_by_category, {})
                if not isinstance(points, dict):
                    points = {}
                coverage = _loads(row.coverage, {})
                if not isinstance(coverage, dict):
                    coverage = {}
                out.append(
                    {
                        "passType": row.pass_type,
                        "scoreTotal": int(row.score_total or 0),
                        "status": row.status,
                        "countsBySeverity": counts,
                        "pointsByCategory": points,
                        "coverage": coverage,
                        "createdAt": row.created_at.isoformat() + "Z" if row.created_at else None,
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
        with SessionLocal() as db:
            db.add(
                EvidenceBundleRow(
                    id=bundle_id,
                    job_id=job_id,
                    doc_id=doc_id,
                    bundle_path=bundle_path,
                    bundle_hash=bundle_hash,
                    created_at=datetime.utcnow(),
                    created_by=created_by,
                    options_json=json.dumps(options),
                    status=status,
                    error_text=error_text,
                )
            )
            db.commit()

    def update_evidence_bundle_record(
        self,
        bundle_id: str,
        *,
        bundle_path: Optional[str] = None,
        bundle_hash: Optional[str] = None,
        status: Optional[str] = None,
        error_text: Optional[str] = None,
    ) -> bool:
        with SessionLocal() as db:
            row = db.get(EvidenceBundleRow, bundle_id)
            if row is None:
                return False
            if bundle_path is not None:
                row.bundle_path = bundle_path
            if bundle_hash is not None:
                row.bundle_hash = bundle_hash
            if status is not None:
                row.status = status
            if error_text is not None:
                row.error_text = error_text
            db.commit()
            return True

    def list_evidence_bundles_for_doc(self, doc_id: str) -> List[Dict[str, object]]:
        with SessionLocal() as db:
            rows = db.execute(select(EvidenceBundleRow).where(EvidenceBundleRow.doc_id == doc_id).order_by(EvidenceBundleRow.created_at.desc())).scalars().all()
            out: List[Dict[str, object]] = []
            for row in rows:
                options = _loads(row.options_json, {})
                if not isinstance(options, dict):
                    options = {}
                out.append(
                    {
                        "bundleId": row.id,
                        "jobId": row.job_id,
                        "docId": row.doc_id,
                        "bundlePath": row.bundle_path,
                        "bundleHash": row.bundle_hash,
                        "createdAt": row.created_at.isoformat() + "Z" if row.created_at else None,
                        "createdBy": row.created_by,
                        "options": options,
                        "status": row.status,
                        "errorText": row.error_text,
                    }
                )
            return out

    def get_evidence_bundle(self, bundle_id: str) -> Optional[Dict[str, object]]:
        with SessionLocal() as db:
            row = db.get(EvidenceBundleRow, bundle_id)
            if row is None:
                return None
            options = _loads(row.options_json, {})
            if not isinstance(options, dict):
                options = {}
            return {
                "bundleId": row.id,
                "jobId": row.job_id,
                "docId": row.doc_id,
                "bundlePath": row.bundle_path,
                "bundleHash": row.bundle_hash,
                "createdAt": row.created_at.isoformat() + "Z" if row.created_at else None,
                "createdBy": row.created_by,
                "options": options,
                "status": row.status,
                "errorText": row.error_text,
            }
