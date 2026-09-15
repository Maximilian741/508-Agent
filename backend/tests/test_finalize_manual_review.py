from __future__ import annotations

import importlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter


class FinalizeManualReviewTests(unittest.TestCase):
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

        # Routes now require a session JWT — auto-authenticate as a fixed test
        # user. The seeded document is owned by this id.
        from app.api.deps import require_user_id

        self.user_id = "pytest-user"
        self.main_module.app.dependency_overrides[require_user_id] = lambda: self.user_id

    def tearDown(self) -> None:
        from app.persistence import db as persistence_db

        if persistence_db._CONN is not None:
            persistence_db._CONN.close()
            persistence_db._CONN = None
        os.environ.pop("DATABASE_PATH", None)
        self.tmp_dir.cleanup()

    @staticmethod
    def _build_min_pdf_bytes() -> bytes:
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()

    def test_finalize_is_retired_and_writes_nothing(self) -> None:
        """POST /documents/{id}/finalize wrote approved edits into the file and
        served it back with no credit check. It is retired: 410, and no final
        artifact, "finalized" fix report or post_manual score is produced.
        Manual review decisions are still recorded, because the Audit flow
        queues rejected findings there."""
        from app.api import documents as documents_api
        from app.persistence.db import get_repo

        repo = get_repo()
        doc_id = "doc-finalize-retired"
        job_id = "job-finalize-retired"
        pdf_path = Path(self.tmp_dir.name) / "finalize.pdf"
        pdf_path.write_bytes(self._build_min_pdf_bytes())
        repo.save_document(
            {
                "id": doc_id,
                "ownerId": self.user_id,
                "filename": "finalize.pdf",
                "docType": "pdf",
                "path": str(pdf_path),
            }
        )
        repo.save_job({"jobId": job_id, "docId": doc_id, "status": "done", "progress": 100, "message": "Done"})
        repo.save_issues(
            doc_id,
            "before",
            [
                {
                    "id": "issue-alt",
                    "ruleId": "missing_alt_text",
                    "severity": "error",
                    "title": "Missing alt text",
                    "description": "Figure appears to be missing alt text.",
                    "locationHint": "Page 1",
                    "recommendation": "Provide alt text.",
                    "evidence": {"anchors": [{"page": 1, "mcid": 0}]},
                }
            ],
            ["missing_alt_text|error|page1"],
        )
        repo.save_fix_report(
            doc_id,
            {
                "docId": doc_id,
                "before": {"issueCount": 1, "bySeverity": {"error": 1}},
                "after": {"issueCount": 1, "bySeverity": {"error": 1}},
                "delta": {"fixed": [], "remaining": [{"id": "issue-alt"}], "introduced": []},
            },
        )
        repo.add_manual_review_items(
            doc_id,
            [
                {
                    "id": "mr-finalize-1",
                    "issueId": "missing_alt_text",
                    "targetNodeId": "fig-1",
                    "reason": "Missing alt text",
                    "status": "approved",
                    "approvedText": "Descriptive alt text.",
                    "anchors": [{"page": 1, "mcid": 0}],
                }
            ],
        )

        patch_resp = self.client.patch(
            "/manual-review/mr-finalize-1",
            json={"status": "approved", "approvedText": "Descriptive alt text."},
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertTrue(patch_resp.json().get("readyToFinalize"))

        finalize = self.client.post(f"/documents/{doc_id}/finalize")
        self.assertEqual(finalize.status_code, 410)
        self.assertIn("/pipeline/remediate", finalize.json().get("detail", ""))
        self.assertFalse((documents_api.FIXED_DIR / doc_id).exists())

        score_resp = self.client.get(f"/jobs/{job_id}/score")
        self.assertEqual(score_resp.status_code, 200)
        pass_types = {entry.get("passType") for entry in score_resp.json().get("scores", [])}
        self.assertNotIn("post_manual", pass_types)

        fix_report = repo.get_fix_report(doc_id)
        self.assertFalse(bool((fix_report or {}).get("finalized")))


if __name__ == "__main__":
    unittest.main()
