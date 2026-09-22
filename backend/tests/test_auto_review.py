from __future__ import annotations

import importlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.ai.auto_review import validate_alt_text


class AutoReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_508_agent.db"
        os.environ["DATABASE_PATH"] = str(self.db_path)
        os.environ["DATABASE_URL"] = f"sqlite:///{self.db_path}"

        from app.persistence import db as persistence_db

        if persistence_db._CONN is not None:
            persistence_db._CONN.close()
            persistence_db._CONN = None
        try:
            from app.config import get_settings

            get_settings.cache_clear()  # type: ignore[attr-defined]
        except Exception:
            pass
        persistence_db.init_db()

        import app.main as main_module

        self.main_module = importlib.reload(main_module)
        self.client = TestClient(self.main_module.app)

        # Routes now require a session JWT — auto-authenticate as a fixed test
        # user. The seeded documents are owned by this id.
        from app.api.deps import require_user_id

        self.user_id = "pytest-user"
        self.main_module.app.dependency_overrides[require_user_id] = lambda: self.user_id

    def tearDown(self) -> None:
        from app.persistence import db as persistence_db

        if persistence_db._CONN is not None:
            persistence_db._CONN.close()
            persistence_db._CONN = None
        os.environ.pop("DATABASE_PATH", None)
        os.environ.pop("DATABASE_URL", None)
        # On Windows the sqlite file can linger locked briefly; don't let temp
        # cleanup failure fail an otherwise-passing test.
        try:
            self.tmp_dir.cleanup()
        except Exception:
            pass

    @staticmethod
    def _min_pdf_bytes() -> bytes:
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()

    def _seed_pdf_doc(self, doc_id: str, filename: str) -> None:
        from app.persistence.db import get_repo

        pdf_path = Path(self.tmp_dir.name) / filename
        pdf_path.write_bytes(self._min_pdf_bytes())
        get_repo().save_document(
            {
                "id": doc_id,
                "ownerId": self.user_id,
                "filename": filename,
                "docType": "pdf",
                "path": str(pdf_path),
            }
        )

    def test_validator_rejects_generic_and_too_long(self) -> None:
        ok, status = validate_alt_text("image of a chart")
        self.assertFalse(ok)
        self.assertEqual(status, "generic_prefix")

        ok2, status2 = validate_alt_text("x" * 300)
        self.assertFalse(ok2)
        self.assertEqual(status2, "too_long")

        ok3, status3 = validate_alt_text("Bar chart showing quarterly revenue growth.")
        self.assertTrue(ok3)
        self.assertEqual(status3, "pass")

    def test_ai_review_is_retired_and_never_calls_the_model(self) -> None:
        """POST /documents/{id}/ai-review sent document context straight to
        OpenAI, outside SemanticInferenceClient's per-job cost cap, with no
        credit check or rate limit. It is retired: both modes answer 410, the
        model is never called, and the queued item is left untouched."""
        from app.persistence.db import get_connection, get_repo, _utc_now

        doc_id = "doc-ai-review"
        self._seed_pdf_doc(doc_id, "sample.pdf")
        seed_item = {
            "id": "mr-ai-1",
            "issueId": "missing_alt_text",
            "targetNodeId": "fig-1",
            "reason": "Missing alt text",
            "status": "pending",
            "anchors": [{"page": 1, "mcid": 0}],
        }
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO manual_review(id, doc_id, item_json, created_at, resolved)
            VALUES(?,?,?,?,0)
            """,
            (seed_item["id"], doc_id, json.dumps(seed_item), _utc_now()),
        )
        conn.commit()

        with patch(
            "app.ai.auto_review._openai_propose",
            side_effect=AssertionError("the model must not be called"),
        ) as propose:
            for mode in ("propose", "apply"):
                resp = self.client.post(f"/documents/{doc_id}/ai-review", json={"mode": mode, "maxItems": 5})
                self.assertEqual(resp.status_code, 410, resp.text)
                self.assertIn("/pipeline/remediate", resp.json().get("detail", ""))
        propose.assert_not_called()

        item = get_repo().get_manual_review_item("mr-ai-1")
        assert item is not None
        self.assertEqual(item.get("status"), "pending")
        self.assertFalse(item.get("approvedText"))

    def test_ai_review_apply_never_approves_a_stored_proposal(self) -> None:
        """Even a stored high-confidence proposal is not auto-approved: the
        route that applied it is gone, so the item stays pending for a human."""
        from app.persistence.db import get_connection, get_repo, _utc_now

        doc_id = "doc-ai-review-stored"
        self._seed_pdf_doc(doc_id, "sample-stored.pdf")
        seed_item = {
            "id": "mr-ai-stored-1",
            "issueId": "missing_alt_text",
            "targetNodeId": "fig-1",
            "reason": "Missing alt text",
            "status": "pending",
            "anchors": [{"page": 1, "mcid": 0}],
            "aiDecision": {
                "action": "approve",
                "approvedText": "Chart of annual sales by quarter.",
                "confidence": 0.99,
                "rationale": "test",
                "model": "test-model",
            },
            "aiConfidence": 0.99,
            "aiStatus": "proposed",
            "validatorStatus": "pass",
            "aiModel": "test-model",
            "aiUpdatedAt": "2026-02-26T00:00:00Z",
        }
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO manual_review(id, doc_id, item_json, created_at, resolved, ai_decision_json, ai_confidence, ai_status, validator_status, ai_model, ai_updated_at)
            VALUES(?,?,?,?,0,?,?,?,?,?,?)
            """,
            (
                seed_item["id"],
                doc_id,
                json.dumps(seed_item),
                _utc_now(),
                json.dumps(seed_item["aiDecision"]),
                seed_item["aiConfidence"],
                seed_item["aiStatus"],
                seed_item["validatorStatus"],
                seed_item["aiModel"],
                seed_item["aiUpdatedAt"],
            ),
        )
        conn.commit()

        apply_resp = self.client.post(
            f"/documents/{doc_id}/ai-review",
            json={"mode": "apply", "maxItems": 5, "minConfidence": 0.8},
        )
        self.assertEqual(apply_resp.status_code, 410)

        item = get_repo().get_manual_review_item("mr-ai-stored-1")
        assert item is not None
        self.assertEqual(item.get("status"), "pending")
        self.assertEqual(item.get("aiStatus"), "proposed")


if __name__ == "__main__":
    unittest.main()
