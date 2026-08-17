"""Smoke: a lost /remediate response is recoverable, and never double-charged.

/pipeline/remediate is synchronous, and its response was the ONLY place the
download URL lived. Close the tab, or have the CDN cut a long request at its
timeout, and the file was written, sitting on disk, charged for — and
unreachable. Two fixes, both pinned here through the real app:

  * GET /pipeline/jobs lists the caller's recent remediations (owner-scoped
    by the userId now recorded on each job manifest) with FRESH signed URLs
    that actually download. Another user sees nothing.
  * If the client had already disconnected when /remediate reached the
    charge, the credit is NOT taken then; the manifest is marked
    chargePending and the debit happens on first successful download —
    exactly once. Simulated here by flipping the manifest the way the
    disconnect branch does, then downloading twice.

Usage:
    python -m app.devtools.smoke_remediate_jobs
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_jobs_')}/s.db"
os.environ.setdefault("APP_SECRET", "smoke-remediate-jobs-secret")

from pathlib import Path  # noqa: E402

import docx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402


def _docx_with_issues(path: Path) -> None:
    d = docx.Document()
    d.add_paragraph("A document with no title and a fake list.")
    d.add_paragraph("- first typed bullet")
    d.add_paragraph("- second typed bullet")
    d.add_paragraph("click here")
    d.save(str(path))


def _signed_in(client: TestClient, email: str) -> tuple[dict, str]:
    r = client.post("/auth/sign-in", json={"email": email, "displayName": "Jobs Smoke", "password": "starterpass1"})
    assert r.status_code == 200, r.text
    tok = r.json()["token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    r = client.post("/auth/grant-starter", headers=hdr)
    assert r.status_code == 200, r.text
    return hdr, r.json()["user"]["id"]


def _balance(client: TestClient, hdr: dict) -> int:
    r = client.get("/credits/balance", headers=hdr)
    assert r.status_code == 200, r.text
    return int(r.json()["balance"])


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    client = TestClient(app)
    hdr_a, user_a = _signed_in(client, "jobs-a@example.com")
    hdr_b, _user_b = _signed_in(client, "jobs-b@example.com")

    src = Path(tempfile.mkdtemp(prefix="508_jobs_")) / "in.docx"
    _docx_with_issues(src)

    # Analyze, approve everything, remediate.
    with open(src, "rb") as fh:
        r = client.post("/pipeline/analyze", headers=hdr_a,
                        files={"file": ("in.docx", fh, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    check("analyze succeeds", r.status_code == 200, r.text[:200])
    approved = [v["id"] for v in r.json().get("violations", [])]
    check("the sample document has findings to approve", len(approved) > 0)

    before = _balance(client, hdr_a)
    with open(src, "rb") as fh:
        r = client.post("/pipeline/remediate", headers=hdr_a,
                        files={"file": ("in.docx", fh, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                        data={"approved_violations": json.dumps(approved), "rejected_violations": "[]"})
    check("remediate succeeds", r.status_code == 200, r.text[:300])
    job_id = (r.headers.get("x-job-id") or "")
    after = _balance(client, hdr_a)
    check("a normal (connected) remediate charges once", after < before, f"{before} -> {after}")
    cost = before - after

    # ---- /jobs lists it, owner-scoped, with a URL that works ----------------
    r = client.get("/pipeline/jobs", headers=hdr_a)
    check("GET /pipeline/jobs succeeds", r.status_code == 200, r.text[:200])
    jobs = r.json().get("jobs", [])
    check("the remediation appears in the caller's job list", len(jobs) >= 1, str(jobs)[:200])
    if jobs:
        j = jobs[0]
        check("listed job is marked charged", j["charged"] is True and j["chargePending"] is False, str(j))
        r2 = client.get(j["downloadUrl"], headers=hdr_a)
        check("the fresh signed URL from /jobs downloads the file", r2.status_code == 200 and len(r2.content) > 1000,
              f"{r2.status_code} {len(r2.content)}")
        job_id = j["jobId"]
    r = client.get("/pipeline/jobs", headers=hdr_b)
    check("another user's job list does NOT include it", r.status_code == 200 and r.json().get("jobs") == [], r.text[:200])

    # ---- deferred charge: simulate the disconnect branch --------------------
    # Flip the manifest exactly the way remediate does when the client is gone,
    # refund the credit it took (so the ledger reads as "not yet charged"), and
    # download twice: the first download must debit, the second must not.
    settings = get_settings()
    meta_path = settings.materialized_root / "pipeline" / job_id / "meta.json"
    check("job manifest exists on disk", meta_path.exists(), str(meta_path))
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        check("manifest records the userId (needed to attribute a lost job)", meta.get("userId") == user_a, str(meta.get("userId")))
        meta["chargePending"] = True
        meta["charged"] = False
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        r = client.get("/pipeline/jobs", headers=hdr_a)
        pend = [j for j in r.json()["jobs"] if j["jobId"] == job_id]
        check("/jobs shows the deferred charge as pending", bool(pend) and pend[0]["chargePending"] is True, str(pend)[:200])
        b0 = _balance(client, hdr_a)
        url = pend[0]["downloadUrl"] if pend else jobs[0]["downloadUrl"]
        r1 = client.get(url, headers=hdr_a)
        b1 = _balance(client, hdr_a)
        check("first download of a charge-pending job DEBITS the credit", r1.status_code == 200 and b1 == b0 - cost, f"{b0} -> {b1} (cost {cost})")
        r2 = client.get(url, headers=hdr_a)
        b2 = _balance(client, hdr_a)
        check("second download does NOT charge again (exactly once)", r2.status_code == 200 and b2 == b1, f"{b1} -> {b2}")
        meta2 = json.loads(meta_path.read_text(encoding="utf-8"))
        check("manifest flipped to charged after the deferred debit",
              meta2.get("chargePending") is False and meta2.get("charged") is True and meta2.get("chargedAtDownload"), str({k: meta2.get(k) for k in ("chargePending", "charged", "chargedAtDownload")}))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
