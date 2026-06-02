"""Smoke test for the gated, server-bound remediation-summary certificate.

The certificate is bound to a SERVER-side analysis (by documentId) — the client
never supplies the score/claim. Free for active subscribers, costs credits
otherwise; a third party can verify an issued certificate by id.

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

_DOC = "report"
_PAYLOAD = {"documentId": _DOC}


def _signin(client: TestClient, email: str):
    r = client.post("/auth/sign-in", json={"email": email, "displayName": "C", "password": "certpass12345"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["user"]["id"]


def _seed_analysis(user_id: str, document_id: str = _DOC, filename: str = "report.pdf",
                   score: int = 96, fixed: int = 5, remaining: int = 0) -> None:
    """Insert a server analysis record so a certificate can bind to it."""
    from datetime import datetime

    from app.db.models import AnalysisResultRow
    from app.db.session_sqlalchemy import session_scope

    with session_scope() as s:
        s.add(AnalysisResultRow(
            id=f"{user_id}::{document_id}", user_id=user_id, document_id=document_id,
            filename=filename, source_format="pdf",
            initial_issues=fixed + remaining, fixed_automatically=fixed, pending_manual=remaining,
            score=score, grade="A", created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ))


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
    a_auth, a_id = _signin(client, "cert-a@example.com")
    client.post("/auth/grant-starter", headers=a_auth)

    # A document that was never analyzed cannot be certified.
    check("unanalyzed document -> 400", client.post("/billing/issue-certificate", headers=a_auth, json=_PAYLOAD).status_code == 400)

    _seed_analysis(a_id)
    start = bal(a_auth)
    r = client.post("/billing/issue-certificate", headers=a_auth, json=_PAYLOAD)
    check("issue cert (credits) -> 200", r.status_code == 200)
    cert = r.json()
    check("paidWith=credits", cert.get("paidWith") == "credits")
    check("balance dropped by cost", bal(a_auth) == start - 2)
    check("cert has id + verifyUrl", bool(cert.get("certificateId")) and "/verify?cert=" in cert.get("verifyUrl", ""))
    # Numbers come from the SERVER record, not the client.
    check("score from server analysis (96)", cert.get("score") == 96 and cert.get("fixedCount") == 5)
    # Honesty: the claim is server-generated and must NOT assert formal conformance.
    _claim = (cert.get("conformanceClaim") or "")
    check("claim is server-generated honest summary", "Automated accessibility remediation summary" in _claim)
    check("claim does NOT assert formal conformance", "Conforms to WCAG" not in _claim and "not a formal" in _claim.lower())
    # Client cannot inject a conformance claim or score (extra fields rejected).
    _forged = {"documentId": _DOC, "conformanceClaim": "Conforms to WCAG 2.1 AA", "score": 100}
    check("client-supplied claim/score rejected -> 422", client.post("/billing/issue-certificate", headers=a_auth, json=_forged).status_code == 422)

    # Public verification — must NOT leak the raw issuer email or document name
    # (the link is shared with auditors). Identity/filename are redacted.
    v = client.get(f"/billing/certificate/{cert['certificateId']}")
    check("verify -> 200", v.status_code == 200)
    vj = v.json()
    check("verify redacts email (no raw address)", vj.get("issuedTo") == "c***@example.com" and "cert-a@example.com" != vj.get("issuedTo"))
    check("verify redacts filename to type only", vj.get("filename") == "document.pdf" and "report" not in (vj.get("filename") or ""))
    check("verify still proves score/claim", isinstance(vj.get("score"), int) and bool(vj.get("conformanceClaim")))
    check("verify bogus id -> 404", client.get("/billing/certificate/deadbeefdeadbeef").status_code == 404)

    # User B: no subscription, no credits -> 402 (analysis seeded so it reaches the paywall).
    b_auth, b_id = _signin(client, "cert-b@example.com")
    _seed_analysis(b_id)
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
    _seed_analysis(c_id)
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
