from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class DocumentStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_508_agent.db"
        os.environ["DATABASE_PATH"] = str(self.db_path)
        os.environ.pop("DATABASE_URL", None)

        from app.persistence import db as persistence_db

        if persistence_db._CONN is not None:
            persistence_db._CONN.close()
            persistence_db._CONN = None
        persistence_db.init_db()

        import app.main as main_module

        self.main_module = importlib.reload(main_module)
        self.client = TestClient(self.main_module.app)

    def tearDown(self) -> None:
        from app.persistence import db as persistence_db

        if persistence_db._CONN is not None:
            persistence_db._CONN.close()
            persistence_db._CONN = None
        os.environ.pop("DATABASE_PATH", None)
        self.tmp_dir.cleanup()

    def _repo(self):
        from app.persistence.db import get_repo

        return get_repo()

    def _seed_doc(self, doc_id: str) -> None:
        doc_path = Path(self.tmp_dir.name) / f"{doc_id}.pdf"
        doc_path.write_bytes(b"pdf")
        self._repo().save_document(
            {
                "id": doc_id,
                "filename": f"{doc_id}.pdf",
                "docType": "pdf",
                "path": str(doc_path),
            }
        )

    def test_status_not_run(self) -> None:
        doc_id = "doc-not-run"
        self._seed_doc(doc_id)
        payload = self._repo().get_document_status(doc_id)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["status"], "not_run")
        self.assertIn("no_jobs", payload["reasons"])

    def test_status_in_progress(self) -> None:
        doc_id = "doc-running"
        self._seed_doc(doc_id)
        self._repo().save_job({"jobId": "job-running", "docId": doc_id, "status": "running", "progress": 20, "message": "Running"})
        payload = self._repo().get_document_status(doc_id)
        assert payload is not None
        self.assertEqual(payload["status"], "in_progress")

    def test_status_fixed(self) -> None:
        doc_id = "doc-fixed"
        self._seed_doc(doc_id)
        self._repo().save_job({"jobId": "job-fixed", "docId": doc_id, "status": "done", "progress": 100, "message": "Done"})
        self._repo().save_fix_report(
            doc_id,
            {
                "before": {"issueCount": 1, "bySeverity": {"warning": 1}},
                "after": {"issueCount": 0, "bySeverity": {}},
                "delta": {"fixed": [{"id": "a"}], "remaining": [], "introduced": []},
            },
        )
        payload = self._repo().get_document_status(doc_id)
        assert payload is not None
        self.assertEqual(payload["status"], "fixed")

    def test_status_needs_review_pending_manual(self) -> None:
        doc_id = "doc-needs-review"
        self._seed_doc(doc_id)
        self._repo().save_job({"jobId": "job-needs-review", "docId": doc_id, "status": "done", "progress": 100, "message": "Done"})
        self._repo().save_fix_report(
            doc_id,
            {
                "before": {"issueCount": 1, "bySeverity": {"warning": 1}},
                "after": {"issueCount": 1, "bySeverity": {"warning": 1}},
                "delta": {"fixed": [], "remaining": [{"id": "a", "severity": "warning"}], "introduced": []},
            },
        )
        self._repo().add_manual_review_items(
            doc_id,
            [
                {
                    "id": "mr-pending-1",
                    "issueId": "missing_alt_text",
                    "targetNodeId": "node-1",
                    "reason": "Needs human decision",
                    "status": "pending",
                }
            ],
        )
        payload = self._repo().get_document_status(doc_id)
        assert payload is not None
        self.assertEqual(payload["status"], "needs_review")
        self.assertIn("pending_manual>0", payload["reasons"])

    def test_status_errors(self) -> None:
        doc_id = "doc-errors"
        self._seed_doc(doc_id)
        self._repo().save_job({"jobId": "job-errors", "docId": doc_id, "status": "error", "progress": 0, "message": "Failed"})
        payload = self._repo().get_document_status(doc_id)
        assert payload is not None
        self.assertEqual(payload["status"], "errors")
        self.assertIn("job_failed", payload["reasons"])

    def test_status_errors_with_critical_remaining(self) -> None:
        doc_id = "doc-critical-remaining"
        self._seed_doc(doc_id)
        self._repo().save_job({"jobId": "job-critical-remaining", "docId": doc_id, "status": "done", "progress": 100, "message": "Done"})
        self._repo().save_fix_report(
            doc_id,
            {
                "before": {"issueCount": 1, "bySeverity": {"critical": 1}},
                "after": {"issueCount": 1, "bySeverity": {"critical": 1}},
                "delta": {"fixed": [], "remaining": [{"id": "a", "severity": "critical"}], "introduced": []},
            },
        )
        payload = self._repo().get_document_status(doc_id)
        assert payload is not None
        self.assertEqual(payload["status"], "errors")
        self.assertIn("critical_remaining>0", payload["reasons"])

    def test_status_endpoints(self) -> None:
        doc_id = "doc-endpoint"
        self._seed_doc(doc_id)
        self._repo().save_job({"jobId": "job-endpoint", "docId": doc_id, "status": "running", "progress": 50, "message": "Running"})

        list_resp = self.client.get("/documents/status")
        self.assertEqual(list_resp.status_code, 200)
        body = list_resp.json()
        self.assertIn("items", body)
        self.assertTrue(any(item.get("docId") == doc_id and "status" in item for item in body["items"]))

        one_resp = self.client.get(f"/documents/{doc_id}/status")
        self.assertEqual(one_resp.status_code, 200)
        item = one_resp.json()
        self.assertEqual(item["docId"], doc_id)
        self.assertIn("status", item)


if __name__ == "__main__":
    unittest.main()
