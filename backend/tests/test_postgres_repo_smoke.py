from __future__ import annotations

import os
import unittest


POSTGRES_TEST_URL = os.getenv("POSTGRES_TEST_URL", "").strip()


@unittest.skipUnless(POSTGRES_TEST_URL, "POSTGRES_TEST_URL not set")
class PostgresRepoSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["DATABASE_URL"] = POSTGRES_TEST_URL
        os.environ.pop("DATABASE_PATH", None)
        from app.persistence import db as persistence_db

        persistence_db._REPO = None  # type: ignore[attr-defined]
        persistence_db._CONN = None  # type: ignore[attr-defined]
        persistence_db.init_db()

    def test_repo_basic_roundtrip(self) -> None:
        from app.persistence.db import get_repo

        repo = get_repo()
        doc_id = "doc-postgres-smoke"
        job_id = "job-postgres-smoke"
        repo.save_document({"id": doc_id, "filename": "smoke.pdf", "docType": "pdf", "path": "local_path:/tmp/smoke.pdf"})
        repo.save_job({"jobId": job_id, "docId": doc_id, "status": "done", "progress": 100, "message": "ok"})
        repo.save_issues(
            doc_id,
            "before",
            [
                {
                    "id": "iss-1",
                    "ruleId": "missing_alt_text",
                    "severity": "error",
                    "description": "missing alt",
                    "locationHint": "Page 1",
                    "recommendation": "add alt",
                }
            ],
            ["k1"],
        )
        repo.save_fix_report(doc_id, {"docId": doc_id, "delta": {"fixed": [], "remaining": [], "introduced": []}})
        repo.add_manual_review_items(doc_id, [{"id": "mr-postgres-1", "status": "pending"}])

        self.assertIsNotNone(repo.get_document(doc_id))
        self.assertIsNotNone(repo.get_job(job_id))
        self.assertEqual(len(repo.get_issues(doc_id, "before")), 1)
        self.assertIsNotNone(repo.get_fix_report(doc_id))
        self.assertGreaterEqual(len(repo.list_manual_review_items_for_doc(doc_id)), 1)


if __name__ == "__main__":
    unittest.main()

