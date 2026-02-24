from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class PolicyApiTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        from app.persistence import db as persistence_db

        if persistence_db._CONN is not None:
            persistence_db._CONN.close()
            persistence_db._CONN = None
        os.environ.pop("DATABASE_PATH", None)
        self.tmp_dir.cleanup()

    def test_seeded_policy_packs_exist(self) -> None:
        from app.persistence.db import get_repo

        repo = get_repo()
        policies = repo.list_policy_packs()
        names = {str(policy.get("name")) for policy in policies}
        self.assertIn("Section 508 (WCAG 2.0 AA)", names)
        self.assertIn("WCAG 2.2 AA (docs)", names)
        self.assertIn("PDF/UA-focused (Tagged PDF)", names)

    def test_policies_endpoints_return_seeded_packs(self) -> None:
        import app.main as main_module

        main_module = importlib.reload(main_module)
        client = TestClient(main_module.app)

        response = client.get("/api/policies")
        self.assertEqual(response.status_code, 200)
        policies = response.json()
        self.assertTrue(isinstance(policies, list))
        self.assertGreaterEqual(len(policies), 3)

        first_id = policies[0]["id"]
        detail = client.get(f"/api/policies/{first_id}")
        self.assertEqual(detail.status_code, 200)
        payload = detail.json()
        for required_key in ("id", "name", "description", "version", "targets", "updated_at"):
            self.assertIn(required_key, payload)


if __name__ == "__main__":
    unittest.main()

