from __future__ import annotations

import importlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter


class EvidenceBundleApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_508_agent.db"
        self.bundle_dir = Path(self.tmp_dir.name) / "bundles"
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

    def _write_min_pdf(self, path: Path) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        with path.open("wb") as handle:
            writer.write(handle)

    def _seed_doc_job_and_findings(self) -> tuple[str, str]:
        from app.persistence.db import get_repo

        repo = get_repo()
        doc_id = "doc-evidence-test"
        job_id = "job-evidence-test"
        pdf_path = Path(self.tmp_dir.name) / "sample.pdf"
        fixed_path = Path(self.tmp_dir.name) / "sample-fixed.pdf"
        self._write_min_pdf(pdf_path)
        self._write_min_pdf(fixed_path)

        repo.save_document(
            {
                "id": doc_id,
                "ownerId": self.user_id,
                "filename": "sample.pdf",
                "docType": "pdf",
                "path": str(pdf_path),
                "fixedPath": str(fixed_path),
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
                "ruleId": "missing_alt_text",
                "severity": "error",
                "title": "Missing alt text",
                "description": "Image missing alternate text.",
                "locationHint": "Page 1",
                "recommendation": "Add /Alt text.",
                "evidence": {"page": 1},
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
                "evidence": {"section": "Body"},
            }
        ]
        repo.save_issues(doc_id, "before", before, ["before-1"])
        repo.save_issues(doc_id, "after", after, ["after-1"])
        repo.save_fix_report(
            doc_id,
            {
                "docId": doc_id,
                "before": {"issueCount": 1},
                "after": {"issueCount": 1},
                "delta": {"fixed": [], "remaining": [], "introduced": []},
            },
        )
        repo.add_manual_review_items(
            doc_id,
            [
                {
                    "id": "mr-1",
                    "issueId": "missing_alt_text",
                    "targetNodeId": "node-4",
                    "reason": "Needs human-authored description.",
                    "status": "approved",
                    "approvedText": "A chart showing quarterly growth.",
                    "createdAt": "2026-02-25T00:00:00Z",
                }
            ],
        )
        repo.save_job_score(
            job_id=job_id,
            pass_type="baseline",
            score_total=72,
            status="needs_review",
            counts_by_severity={"critical": 0, "serious": 1, "moderate": 0, "minor": 0},
            points_by_category={"issuePenalty": 8.0, "coveragePenalty": 0.0},
            coverage={"applicable": 1, "executed": 1, "skipped": 0},
        )
        return doc_id, job_id

    def test_create_list_and_download_evidence_bundle(self) -> None:
        doc_id, job_id = self._seed_doc_job_and_findings()

        from app.evidence import bundle_builder

        with patch.object(bundle_builder, "BUNDLES_DIR", self.bundle_dir):
            create = self.client.post(f"/jobs/{job_id}/evidence-bundle", json={})
        self.assertEqual(create.status_code, 200, create.text)
        body = create.json()
        bundle_id = body["bundleId"]
        self.assertEqual(body["jobId"], job_id)
        self.assertEqual(body["docId"], doc_id)
        self.assertIn("/evidence-bundles/", body["downloadUrl"])

        from app.persistence.db import get_repo

        bundle = get_repo().get_evidence_bundle(bundle_id)
        self.assertIsNotNone(bundle)
        assert bundle is not None
        zip_path = Path(str(bundle["bundlePath"]))
        self.assertTrue(zip_path.exists(), f"Missing bundle zip: {zip_path}")
        self.assertTrue(str(bundle.get("bundleHash")))

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
            required = {
                "manifest.json",
                "policy/policy.json",
                "reports/report.json",
                "reports/checks.csv",
                "reports/delta.json",
                "reports/manual-review.json",
                "reports/summary.pdf",
                "provenance/tool.json",
                "provenance/hashes.json",
            }
            self.assertTrue(required.issubset(names), f"Missing files: {required - names}")

            hashes_payload = json.loads(zf.read("provenance/hashes.json").decode("utf-8"))
            self.assertIn("files", hashes_payload)
            file_hashes = hashes_payload["files"]
            for rel_path in required - {"provenance/hashes.json"}:
                self.assertIn(rel_path, file_hashes)

        list_resp = self.client.get(f"/documents/{doc_id}/evidence-bundles")
        self.assertEqual(list_resp.status_code, 200)
        bundles = list_resp.json()
        self.assertGreaterEqual(len(bundles), 1)
        bundle_ids = [item.get("bundleId") for item in bundles if isinstance(item, dict)]
        self.assertIn(bundle_id, bundle_ids)

        download = self.client.get(f"/evidence-bundles/{bundle_id}/download")
        self.assertEqual(download.status_code, 200)
        self.assertIn("application/zip", download.headers.get("content-type", ""))
        self.assertIn("attachment;", download.headers.get("content-disposition", "").lower())


if __name__ == "__main__":
    unittest.main()
