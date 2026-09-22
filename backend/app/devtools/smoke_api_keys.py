"""Smoke: developer API keys for programmatic scanning.

Pins the whole feature AND its security boundaries:

  - sign in (JWT) -> create an API key -> the plaintext is returned once
  - the key scans via POST /pipeline/analyze (both X-API-Key and
    Authorization: Bearer ak_... forms) -> 200
  - listing keys never leaks the secret
  - revoking a key -> that key then gets 401 on /analyze
  - SECURITY: an API key CANNOT manage keys (create/list) and CANNOT remediate
    (those stay session-JWT-only) -> 401; a bogus/empty key -> 401

Usage:
    python -m app.devtools.smoke_api_keys
"""

from __future__ import annotations

import io
import os
import sys
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_ak_')}/s.db"

from docx import Document  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _docx_bytes() -> bytes:
    d = Document()
    d.add_heading("Report", level=1)
    d.add_paragraph("Body text.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    r = client.post(
        "/auth/sign-in",
        json={"email": "apikey@example.com", "displayName": "AK", "password": "apikeytest123"},
    )
    jwt = r.json()["token"]
    jwt_auth = {"Authorization": f"Bearer {jwt}"}

    # --- create a key (JWT) --------------------------------------------------
    r = client.post("/api-keys", json={"name": "CI scanner"}, headers=jwt_auth)
    check("create api key -> 200", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    body = r.json()
    full_key = body.get("key", "")
    key_id = body.get("id", "")
    check("returned plaintext key with ak_ prefix", full_key.startswith("ak_live_"), full_key[:12])
    check("returned a key prefix + id", bool(body.get("keyPrefix")) and bool(key_id))

    docx = _docx_bytes()

    # --- scan with the key (X-API-Key) ---------------------------------------
    r = client.post(
        "/pipeline/analyze",
        files={"file": ("d.docx", docx, DOCX_MIME)},
        headers={"X-API-Key": full_key},
    )
    check("scan via X-API-Key -> 200", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    check("scan returns violations array", isinstance(r.json().get("violations"), list))

    # --- scan with the key (Authorization: Bearer ak_...) --------------------
    r = client.post(
        "/pipeline/analyze",
        files={"file": ("d.docx", docx, DOCX_MIME)},
        headers={"Authorization": f"Bearer {full_key}"},
    )
    check("scan via 'Bearer ak_...' -> 200", r.status_code == 200, f"{r.status_code}")

    # --- list keys never leaks the secret ------------------------------------
    r = client.get("/api-keys", headers=jwt_auth)
    listed = r.json()
    check("list keys -> 200 with our key", r.status_code == 200 and any(k["id"] == key_id for k in listed))
    check("list never returns the plaintext secret",
          all("key" not in k for k in listed) and full_key not in r.text)

    # --- SECURITY: an API key cannot MANAGE keys -----------------------------
    r = client.post("/api-keys", json={"name": "evil"}, headers={"X-API-Key": full_key})
    check("api key CANNOT create keys (mgmt is JWT-only) -> 401", r.status_code == 401, f"got {r.status_code}")
    r = client.get("/api-keys", headers={"X-API-Key": full_key})
    check("api key CANNOT list keys -> 401", r.status_code == 401, f"got {r.status_code}")

    # --- SECURITY: an API key cannot REMEDIATE (no programmatic spending) -----
    import json as _json
    r = client.post(
        "/pipeline/remediate",
        files={"file": ("d.docx", docx, DOCX_MIME)},
        data={"approved_violations": _json.dumps([]), "rejected_violations": "[]"},
        headers={"X-API-Key": full_key},
    )
    check("api key CANNOT remediate (stays JWT-only) -> 401", r.status_code == 401, f"got {r.status_code}")

    # --- bogus / missing keys -> 401 -----------------------------------------
    r = client.post("/pipeline/analyze", files={"file": ("d.docx", docx, DOCX_MIME)}, headers={"X-API-Key": "ak_live_bogus"})
    check("bogus key -> 401", r.status_code == 401, f"got {r.status_code}")
    # No credential at all is the account-free scan now (read-only, nothing
    # saved, no AI — pinned by smoke_anonymous_scan); a BAD key is still 401.
    r = client.post("/pipeline/analyze", files={"file": ("d.docx", docx, DOCX_MIME)})
    check("no credential -> the anonymous scan (200)", r.status_code == 200, f"got {r.status_code}")

    # --- revoke, then the key is rejected ------------------------------------
    r = client.post(f"/api-keys/{key_id}/revoke", headers=jwt_auth)
    check("revoke -> 200 and marked revoked", r.status_code == 200 and r.json().get("revoked") is True, f"{r.status_code} {r.text[:120]}")
    r = client.post("/pipeline/analyze", files={"file": ("d.docx", docx, DOCX_MIME)}, headers={"X-API-Key": full_key})
    check("revoked key -> 401", r.status_code == 401, f"got {r.status_code}")

    # --- can't revoke someone else's key (no cross-tenant) -------------------
    r2 = client.post("/auth/sign-in", json={"email": "other@example.com", "displayName": "O", "password": "otherpass123"})
    other_auth = {"Authorization": f"Bearer {r2.json()['token']}"}
    r = client.post(f"/api-keys/{key_id}/revoke", headers=other_auth)
    check("another user can't revoke your key -> 404", r.status_code == 404, f"got {r.status_code}")

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
