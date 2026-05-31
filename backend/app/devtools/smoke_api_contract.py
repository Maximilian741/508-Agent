"""Smoke test for API contract endpoints."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any, Dict

# Hermetic DB + an admin email, set before app import. The legacy global
# /manual-review endpoints are admin-only now.
_SMOKE_DB_DIR = tempfile.mkdtemp(prefix="508_smoke_contract_")
os.environ["DATABASE_URL"] = f"sqlite:///{_SMOKE_DB_DIR}/smoke.db"
os.environ["ADMIN_EMAILS"] = "contract-admin@example.com"

from fastapi.testclient import TestClient

from app.main import app


def _assert_single_line(value: str) -> None:
    if "\n" in value or "\r" in value or "\t" in value:
        raise AssertionError(f"Notes contain control characters: {repr(value)}")


def _assert_issue_shape(issue: Dict[str, Any]) -> None:
    required = {"id", "ruleId", "severity", "description", "nodeId", "recommendedActions"}
    missing = required - set(issue.keys())
    if missing:
        raise AssertionError(f"Issue missing keys: {sorted(missing)}")
    if issue["severity"] not in {"error", "warning", "info"}:
        raise AssertionError("Issue severity is invalid")
    for action in issue["recommendedActions"]:
        action_required = {
            "actionCode",
            "description",
            "requiresAi",
            "requiresHumanReview",
            "isAutoApplicable",
            "supportedNodeTypes",
            "relatedFlagCode",
        }
        action_missing = action_required - set(action.keys())
        if action_missing:
            raise AssertionError(f"Action missing keys: {sorted(action_missing)}")


def main() -> int:
    client = TestClient(app)

    # All scan/remediate/manual-review routes require an authenticated session;
    # the global manual-review view requires admin.
    signin = client.post(
        "/auth/sign-in",
        json={
            "email": "contract-admin@example.com",
            "displayName": "Contract",
            "password": "contractpass1",
        },
    )
    if signin.status_code != 200:
        raise AssertionError(f"Sign-in failed: {signin.status_code} {signin.text}")
    auth = {"Authorization": f"Bearer {signin.json()['token']}"}

    payload = {
        "documentId": "contract-doc",
        "sourceFormat": "pdf",
        "content": json.dumps(
            {
                "title": "",
                "images": [
                    {"id": "img-1", "alt_text": "decorative flourish", "decorative": True},
                    {"id": "img-2", "alt_text": "", "decorative": False},
                ],
                "headings": [{"id": "h1", "level": 1, "text": "Intro"}],
            }
        ),
    }

    scan_response = client.post("/scan", json=payload, headers=auth)
    if scan_response.status_code != 200:
        raise AssertionError(f"Scan failed: {scan_response.status_code}")
    scan_data = scan_response.json()
    for key in ("scanId", "documentId", "issues"):
        if key not in scan_data:
            raise AssertionError(f"Missing scan key: {key}")
    if not isinstance(scan_data["issues"], list):
        raise AssertionError("Issues is not a list")
    if len(scan_data["issues"]) < 2:
        raise AssertionError("Expected at least two issues")

    for issue in scan_data["issues"]:
        _assert_issue_shape(issue)

    issue = next((item for item in scan_data["issues"] if item["ruleId"] == "MISSING_ALT_TEXT"), None)
    if issue is None:
        raise AssertionError("Missing alt text issue not found")

    action_code = issue["recommendedActions"][0]["actionCode"]
    remediate_payload = {
        "issueId": issue["id"],
        "targetNodeId": issue["nodeId"],
        "actionCode": action_code,
    }
    remediate_response = client.post("/remediate", json=remediate_payload, headers=auth)
    if remediate_response.status_code != 200:
        raise AssertionError(f"Remediate failed: {remediate_response.status_code}")
    remediate_data = remediate_response.json()
    if "results" not in remediate_data or not isinstance(remediate_data["results"], list):
        raise AssertionError("Remediate response missing results")
    for result in remediate_data["results"]:
        for key in ("actionCode", "targetNodeId", "status", "notes"):
            if key not in result:
                raise AssertionError(f"Remediate result missing key: {key}")
        _assert_single_line(result["notes"])

    # The single-action remediate now auto-applies heuristic alt text and
    # returns status="success", so this path does not always enqueue a manual
    # review item. Assert the manual-review CONTRACT (auth + shapes), not a
    # specific queue size.
    manual_review_response = client.get("/manual-review", headers=auth)
    if manual_review_response.status_code != 200:
        raise AssertionError(f"Manual review failed: {manual_review_response.status_code}")
    manual_review = manual_review_response.json()
    if not isinstance(manual_review, list):
        raise AssertionError("Manual review response is not a list")

    clear_response = client.delete("/manual-review", headers=auth)
    if clear_response.status_code != 200:
        raise AssertionError(f"Manual review clear failed: {clear_response.status_code}")
    clear_data = clear_response.json()
    if "cleared" not in clear_data:
        raise AssertionError("Manual review clear did not report a count")

    manual_review_after = client.get("/manual-review", headers=auth).json()
    if manual_review_after:
        raise AssertionError("Manual review queue not cleared")

    return 0


if __name__ == "__main__":
    sys.exit(main())
