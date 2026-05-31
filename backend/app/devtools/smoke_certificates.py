"""Smoke test for the gated, verifiable conformance certificate.

Certificates are free for active subscribers and cost credits otherwise; a
third party can verify an issued certificate by id.

Usage:
    python -m app.devtools.smoke_certificates
"""

from __future__ import annotations

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="508_smoke_cert_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/cert.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

_PAYLOAD = {
    "filename": "report.pdf",
    "score": 96,
    "fixedCount": 5,
    "remainingCount": 0,
    "sourceFormat": "pdf",
}


def _signin(client: TestClient, email: str):
    r = client.post("/auth/sign-in", json={"email": email, "displayName": "C", "password": "certpass12345"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["user"]["id"]


def main() -> int:
    client = TestClient(app)
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    def bal(auth: dict) -> int:
        return client.get("/credits/balance", headers=auth).json()["balance"]

    # User A: no subscription, has credits -> pays per certificate.
    a_auth, _ = _signin(client, "cert-a@example.com")
    client.post("/auth/grant-starter", headers=a_auth)
    start = bal(a_auth)
    r = client.post("/billing/issue-certificate", headers=a_auth, json=_PAYLOAD)
    check("issue cert (credits) -> 200", r.status_code == 200)
    cert = r.json()
    check("paidWith=credits", cert.get("paidWith") == "credits")
    check("balance dropped by cost", bal(a_auth) == start - 2)
    check("cert has id + verifyUrl", bool(cert.get("certificateId")) and "/verify?cert=" in cert.get("verifyUrl", ""))
    # Honesty: the claim is server-generated and must NOT assert formal conformance.
    _claim = (cert.get("conformanceClaim") or "")
    check("claim is server-generated honest summary", "Automated accessibility remediation summary" in _claim)
    check("claim does NOT assert formal conformance", "Conforms to WCAG" not in _claim and "not a formal" in _claim.lower())
    # Client cannot inject a conformance claim (extra fields rejected).
    _forged = dict(_PAYLOAD); _forged["conformanceClaim"] = "Conforms to WCAG 2.1 AA"
    check("client conformanceClaim rejected -> 422", client.post("/billing/issue-certificate", headers=a_auth, json=_forged).status_code == 422)

    # Public verification.
    v = client.get(f"/billing/certificate/{cert['certificateId']}")
    check("verify -> 200", v.status_code == 200)
    check("verify matches", v.json().get("filename") == "report.pdf" and v.json().get("issuedTo") == "cert-a@example.com")
    check("verify bogus id -> 404", client.get("/billing/certificate/deadbeefdeadbeef").status_code == 404)

    # User B: no subscription, no credits -> 402.
    b_auth, _ = _signin(client, "cert-b@example.com")
    check("no credits + no sub -> 402", client.post("/billing/issue-certificate", headers=b_auth, json=_PAYLOAD).status_code == 402)

    # User C: active subscription -> free certificate.
    c_auth, c_id = _signin(client, "cert-c@example.com")
    from datetime import datetime

    from app.db.models import SubscriptionRow
    from app.db.session_sqlalchemy import session_scope

    with session_scope() as s:
        s.add(SubscriptionRow(
            id="sub_cert_c", user_id=c_id, plan="team", status="active",
            stripe_customer_id=None, current_period_end=None,
            created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ))
    c_start = bal(c_auth)
    r = client.post("/billing/issue-certificate", headers=c_auth, json=_PAYLOAD)
    check("subscriber issues cert -> 200", r.status_code == 200)
    check("subscriber paidWith=subscription", r.json().get("paidWith") == "subscription")
    check("subscriber balance unchanged", bal(c_auth) == c_start)

    check("anon issue -> 401", client.post("/billing/issue-certificate", json=_PAYLOAD).status_code == 401)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
