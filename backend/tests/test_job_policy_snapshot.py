from __future__ import annotations

import importlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter


class JobPolicySnapshotTests(unittest.TestCase):
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

    def _build_min_pdf_bytes(self) -> bytes:
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue()

    def test_scan_start_creates_policy_snapshot(self) -> None:
        pdf_bytes = self._build_min_pdf_bytes()
        upload = self.client.post(
            "/documents/upload",
            files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
        )
        self.assertEqual(upload.status_code, 200)
        doc_id = upload.json()["docId"]

        with patch("app.api.documents.threading.Thread") as thread_cls:
            thread = thread_cls.return_value
            thread.start.return_value = None
            start = self.client.post(f"/documents/{doc_id}/scan", json={})
        self.assertEqual(start.status_code, 200)
        job_id = start.json()["jobId"]

        from app.persistence.db import get_repo

        snapshot = get_repo().get_job_policy_snapshot(job_id)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["policyPackId"], "policy-508-wcag20-aa")
        self.assertEqual(snapshot["policyName"], "Section 508 (WCAG 2.0 AA)")

    def test_set_policy_on_queued_job_and_job_detail_includes_policy(self) -> None:
        from app.persistence.db import get_repo

        repo = get_repo()
        job_id = "job-test-policy-queued"
        repo.save_job({"jobId": job_id, "docId": "doc-test", "status": "queued", "progress": 0, "message": "Queued"})

        set_policy = self.client.post(
            f"/jobs/{job_id}/policy",
            json={"policy_pack_id": "policy-wcag22-aa-docs"},
        )
        self.assertEqual(set_policy.status_code, 200)
        body = set_policy.json()
        self.assertEqual(body["policy"]["policyPackId"], "policy-wcag22-aa-docs")
        self.assertEqual(body["policy"]["name"], "WCAG 2.2 AA (docs)")

        job_detail = self.client.get(f"/jobs/{job_id}")
        self.assertEqual(job_detail.status_code, 200)
        payload = job_detail.json()
        self.assertIn("policy", payload)
        self.assertEqual(payload["policy"]["policyPackId"], "policy-wcag22-aa-docs")

    def test_job_score_endpoint_returns_baseline_and_post_fix(self) -> None:
        from app.persistence.db import get_repo

        repo = get_repo()
        doc_id = "doc-score-test"
        job_id = "job-score-test"
        repo.save_document(
            {
                "id": doc_id,
                "filename": "score.pdf",
                "docType": "pdf",
                "path": str(Path(self.tmp_dir.name) / "score.pdf"),
            }
        )
        repo.save_job({"jobId": job_id, "docId": doc_id, "status": "done", "progress": 100, "message": "Done"})
        pack = repo.get_policy_pack("policy-508-wcag20-aa")
        assert pack is not None
        repo.save_job_policy_snapshot(
            job_id=job_id,
            policy_pack_id=str(pack["id"]),
            policy_name=str(pack["name"]),
            policy_version=int(pack["version"]),
            policy_json=pack.get("policy_json", {}),
        )
        before = [
            {
                "id": "issue-before-1",
                "ruleId": "missing_heading_structure",
                "severity": "warning",
                "title": "Missing heading structure",
                "description": "No headings found.",
                "locationHint": "Document",
                "recommendation": "Add headings.",
            }
        ]
        after = [
            {
                "id": "issue-after-1",
                "ruleId": "missing_heading_structure",
                "severity": "warning",
                "title": "Missing heading structure",
                "description": "No headings found.",
                "locationHint": "Document",
                "recommendation": "Add headings.",
            }
        ]
        repo.save_issues(doc_id, "before", before, ["before-1"])
        repo.save_issues(doc_id, "after", after, ["after-1"])

        response = self.client.get(f"/jobs/{job_id}/score")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["jobId"], job_id)
        self.assertTrue(isinstance(payload["scores"], list))
        pass_types = {entry["passType"] for entry in payload["scores"]}
        self.assertIn("baseline", pass_types)
        self.assertIn("post_fix", pass_types)


if __name__ == "__main__":
    unittest.main()
