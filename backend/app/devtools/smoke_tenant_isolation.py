"""Smoke test: document tenant isolation.

User A uploads a document. User B (a different authenticated user) must not see
it in their list and must get 404 when reading it by id. Anonymous callers get
401 on every document route, and a forged ``X-Account-Id`` header is rejected.

Usage:
    python -m app.devtools.smoke_tenant_isolation
"""

from __future__ import annotations

import io
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="508_smoke_tenant_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/smoke.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

_MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _signin(client: TestClient, email: str) -> dict:
    r = client.post(
        "/auth/sign-in",
        json={"email": email, "displayName": email.split("@")[0], "password": "tenantpass1"},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def main() -> int:
    client = TestClient(app)

    a = _signin(client, "tenant-a@example.com")
    b = _signin(client, "tenant-b@example.com")

    print("[1] user A uploads a document")
    up = client.post(
        "/documents/upload",
        files={"file": ("a.pdf", io.BytesIO(_MINIMAL_PDF), "application/pdf")},
        headers=a,
    )
    assert up.status_code == 200, up.text
    doc_id = up.json()["docId"]
    assert doc_id and not doc_id.startswith("doc-"), f"doc id should be an unguessable uuid, got {doc_id!r}"
    print("    docId=", doc_id)

    print("[2] user A sees it in their list; user B does not")
    a_list = client.get("/documents", headers=a).json()
    assert any(d.get("docId") == doc_id for d in a_list), a_list
    b_list = client.get("/documents", headers=b).json()
    assert not any(d.get("docId") == doc_id for d in b_list), b_list

    print("[3] user B reading A's doc by id -> 404 (not 403, no enumeration)")
    assert client.get(f"/documents/{doc_id}/download", headers=b).status_code == 404
    assert client.get(f"/documents/{doc_id}/summary", headers=b).status_code == 404
    assert client.get(f"/documents/{doc_id}/status", headers=b).status_code == 404

    print("[4] anonymous -> 401 on document routes")
    assert client.get("/documents").status_code == 401
    assert client.get(f"/documents/{doc_id}/download").status_code == 401
    assert (
        client.post(
            "/documents/upload",
            files={"file": ("x.pdf", io.BytesIO(_MINIMAL_PDF), "application/pdf")},
        ).status_code
        == 401
    )

    print("[5] forged X-Account-Id (no Bearer) -> 401")
    assert client.get("/documents", headers={"X-Account-Id": "tenant-a@example.com"}).status_code == 401

    print("ALL ASSERTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
