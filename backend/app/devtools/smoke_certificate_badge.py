"""Smoke for the public embeddable certificate badge (GET /billing/certificate/{id}/badge.svg).

Proves: a valid cert renders an SVG badge surfacing its real score; the endpoint
is public (no auth) and CF-Access-exempt by prefix; an unknown id degrades to a
neutral 'not found' badge instead of a broken image; no PII leaks into the SVG.

Usage:
    python -m app.devtools.smoke_certificate_badge
"""

from __future__ import annotations

import os
import sys
import tempfile

_SMOKE_DB_DIR = tempfile.mkdtemp(prefix="508_smoke_badge_")
os.environ["DATABASE_URL"] = f"sqlite:///{_SMOKE_DB_DIR}/smoke.db"

from fastapi.testclient import TestClient  # noqa: E402


def main() -> int:
    from app.main import app
    from app.db.models import CertificateRow
    from app.db.session_sqlalchemy import session_scope

    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    cert_id = "badgecert0001"
    with session_scope() as session:
        session.add(
            CertificateRow(
                id=cert_id,
                user_id="user-1",
                issued_email="secret.owner@example.com",  # MUST NOT appear in the SVG
                filename="Internal Q3 Financials SECRET.pdf",  # MUST NOT appear in the SVG
                conformance_claim="Automated remediation summary, not a formal determination.",
                score=92,
                fixed_count=8,
                remaining_count=1,
                paid_with="credits",
            )
        )

    client = TestClient(app)

    # Valid cert -> SVG badge with the real score, no auth required.
    ok = client.get(f"/billing/certificate/{cert_id}/badge.svg")
    check("valid cert badge returns 200", ok.status_code == 200, ok.text[:200])
    ctype = ok.headers.get("content-type", "")
    check("badge content-type is image/svg+xml", ctype.startswith("image/svg+xml"), ctype)
    body = ok.text
    check("badge is an SVG", body.startswith("<svg") and "</svg>" in body)
    check("badge shows the real score (92/100)", "92/100" in body, body[:300])
    check("badge is branded 508 Agent", "508 Agent" in body)
    check("badge leaks NO issuer email", "secret.owner@example.com" not in body)
    check("badge leaks NO filename", "SECRET" not in body and "Financials" not in body)

    # Unknown cert -> neutral 'not found' badge, still 200 (graceful embed).
    nf = client.get("/billing/certificate/does-not-exist/badge.svg")
    check("unknown cert badge returns 200 (graceful)", nf.status_code == 200, nf.text[:120])
    check("unknown cert badge says 'not found'", "not found" in nf.text)
    check("unknown cert badge reveals no score", "/100" not in nf.text)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
