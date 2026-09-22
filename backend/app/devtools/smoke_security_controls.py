"""Smoke test for negative / security paths.

Covers the guarantees the happy-path tests don't: password policy, anonymous
rejection, admin gating, OOXML upload validation, and webhook fail-closed.

Usage:
    python -m app.devtools.smoke_security_controls
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile

_TMP = tempfile.mkdtemp(prefix="508_smoke_sec_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/v.db"
os.environ["ADMIN_EMAILS"] = "boss@example.com"
# Ensure the webhook has no signing secret so we can assert it fails closed.
os.environ.pop("STRIPE_WEBHOOK_SECRET", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def main() -> int:
    c = TestClient(app)
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    def signin(email: str, pw: str = "goodpassword1"):
        return c.post("/auth/sign-in", json={"email": email, "displayName": "U", "password": pw})

    # Password policy
    check("short password -> 400", c.post("/auth/sign-in", json={"email": "a@x.com", "displayName": "A", "password": "short"}).status_code == 400)
    check("no password -> 400", c.post("/auth/sign-in", json={"email": "b@x.com", "displayName": "B"}).status_code == 400)

    r = signin("user@example.com")
    check("valid signup -> 200", r.status_code == 200)
    user_h = {"Authorization": f"Bearer {r.json()['token']}"}
    check("wrong password -> 401", c.post("/auth/sign-in", json={"email": "user@example.com", "password": "wrongpassword"}).status_code == 401)

    # Anonymous rejection
    check("anon /credits/balance -> 401", c.get("/credits/balance").status_code == 401)
    check("anon /documents -> 401", c.get("/documents").status_code == 401)

    # Admin gating (fail closed)
    check("non-admin GET /audit-log -> 403", c.get("/audit-log", headers=user_h).status_code == 403)
    # A listed address is not admin until it is VERIFIED: typing it at sign-up
    # grants nothing. The operator verifies it with the ops-only command.
    squat_h = {"Authorization": f"Bearer {signin('boss@example.com').json()['token']}"}
    check("listed but unverified email -> /audit-log 403", c.get("/audit-log", headers=squat_h).status_code == 403)
    from app.devtools.bootstrap_admin import bootstrap_admin

    bootstrap_admin("boss@example.com", "goodpassword1")
    admin_h = {"Authorization": f"Bearer {signin('boss@example.com').json()['token']}"}
    check("admin GET /audit-log -> 200", c.get("/audit-log", headers=admin_h).status_code == 200)
    check("admin whoami isAdmin=true", c.get("/api/admin/whoami", headers=admin_h).json().get("isAdmin") is True)
    check("user whoami isAdmin=false", c.get("/api/admin/whoami", headers=user_h).json().get("isAdmin") is False)

    # OOXML validation: a plain zip masquerading as .docx must be rejected
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "not an ooxml package")
    r = c.post("/pipeline/analyze", headers=user_h, files={"file": ("fake.docx", buf.getvalue(), DOCX_MIME)})
    check("plain-zip-as-docx rejected (400)", r.status_code == 400)

    # Webhook fails closed without a signing secret
    r = c.post("/billing/webhook", content=b'{"type":"checkout.session.completed"}', headers={"Stripe-Signature": "t=1,v1=deadbeef"})
    check("webhook without secret -> 503 (fail closed)", r.status_code == 503)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
