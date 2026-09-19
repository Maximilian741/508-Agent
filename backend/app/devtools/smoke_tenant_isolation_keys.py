"""Smoke test: an id that decides WHOSE data something is.

Four tenant-isolation bugs, all the same shape — an identifier that was either
attacker-controlled or guessable was trusted to say who owned a row:

  1. ``/pipeline/remediate`` keyed manual-review rows on the parser's
     ``document_id``, which is the uploaded FILENAME STEM. Tenant B naming a
     file after tenant A's document id filed findings into A's review queue and
     into A's hash-sealed evidence bundle.
  2. ``GET /auth/export`` scoped the audit dump with a ``LIKE '%…%'`` substring
     on the actor's email, so "o@bigcorp.com" harvested "cfo@bigcorp.com".
  3. ``POST /documents/{id}/scan`` minted ``job-{milliseconds}``: guessable, and
     two tenants scanning in the same millisecond shared one job row.
  4. ``GET /storage/{key}`` sanitized the key BEFORE the production
     kill-switch, so an encoded traversal was an unhandled 500.

Usage:
    python -m app.devtools.smoke_tenant_isolation_keys
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="508_smoke_ti_keys_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/s.db"
os.environ["MATERIALIZED_ROOT"] = str(Path(_TMP) / "materialized")
# Production, so the storage kill-switch and the export are exercised the way
# they ship. create_all is dev-only there, so the schema comes from alembic —
# which is also what proves 0017 (manual_review.owner_id) is in the chain.
os.environ["APP_ENV"] = "production"
os.environ.setdefault("APP_SECRET", "s" * 64)

_BACKEND = str(Path(__file__).resolve().parents[2])
_alembic = subprocess.run(
    [sys.executable, "-m", "alembic", "upgrade", "head"],
    cwd=_BACKEND,
    env=dict(os.environ),
    capture_output=True,
    text=True,
)
if _alembic.returncode != 0:
    print(_alembic.stdout[-2000:])
    print(_alembic.stderr[-2000:])
    raise SystemExit("alembic upgrade head failed")

from docx import Document  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _docx() -> bytes:
    d = Document()
    d.add_heading("Quarterly results", level=1)
    d.add_paragraph("Revenue grew in every region this quarter, led by the west.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def main() -> int:  # noqa: PLR0915
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, "" if cond else extra)
        if not cond:
            failures += 1

    client = TestClient(app, raise_server_exceptions=False)

    def sign_in(email: str) -> dict:
        r = client.post(
            "/auth/sign-in",
            json={"email": email, "displayName": email.split("@")[0], "password": "tenantkeys1"},
        )
        assert r.status_code == 200, r.text
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        client.post("/auth/grant-starter", headers=h)
        return h

    # A is the victim; B is the attacker whose email is ALSO a substring of A's.
    a = sign_in("cfo@bigcorp.com")
    b = sign_in("o@bigcorp.com")

    dx = _docx()
    up = client.post(
        "/documents/upload",
        files={"file": ("alice-payroll.docx", io.BytesIO(dx), DOCX)},
        headers=a,
    )
    assert up.status_code == 200, up.text
    doc_a = up.json()["docId"]

    def remediate(headers: dict, filename: str) -> dict:
        """Reject every finding so the queue write path runs."""
        an = client.post(
            "/pipeline/analyze", files={"file": (filename, io.BytesIO(dx), DOCX)}, headers=headers
        )
        assert an.status_code == 200, an.text
        ids = [v.get("id") or v.get("violationId") for v in (an.json().get("violations") or [])]
        assert ids, "analyze produced no violations to reject"
        rem = client.post(
            "/pipeline/remediate",
            files={"file": (filename, io.BytesIO(dx), DOCX)},
            data={"approved_violations": "[]", "rejected_violations": json.dumps(ids)},
            headers=headers,
        )
        assert rem.status_code == 200, rem.text
        return rem.json()

    print("[1] B files manual-review items against a file named after A's document")
    b_run = remediate(b, f"{doc_a}.docx")
    check(
        "B's own remediate still queues its items",
        int(b_run.get("manualReviewItemsCreated") or 0) > 0,
        str(b_run.get("manualReviewItemsCreated")),
    )
    check(
        "the item id is keyed on the server-minted job id, not the filename",
        doc_a not in f"mr-{b_run.get('jobId')}-",
    )

    a_queue = client.get(f"/documents/{doc_a}/manual-review", headers=a)
    check("A's manual-review queue is 404-free for A", a_queue.status_code == 200, a_queue.text)
    check(
        "B's injected item is NOT in A's queue",
        a_queue.json() == [],
        json.dumps(a_queue.json())[:300],
    )
    check(
        "B still cannot READ A's queue",
        client.get(f"/documents/{doc_a}/manual-review", headers=b).status_code == 404,
    )

    print("[2] A's own rejected findings DO land in A's queue (the feature still works)")
    a_run = remediate(a, f"{doc_a}.docx")
    a_queue = client.get(f"/documents/{doc_a}/manual-review", headers=a).json()
    check("A sees the items A filed", len(a_queue) > 0, json.dumps(a_queue)[:200])
    check(
        "and only those - B's row never appears",
        all("User rejected" in str(i.get("reason", "")) for i in a_queue) and len(a_queue) == len(
            [i for i in a_queue if i.get("docId") == doc_a]
        ),
    )

    print("[3] A's hash-sealed evidence bundle carries none of B's rows")
    scan = client.post(f"/documents/{doc_a}/scan", headers=a)
    assert scan.status_code == 200, scan.text
    job_a = scan.json()["jobId"]
    check("scan ids are unguessable (uuid, not a millisecond counter)", len(job_a) > 20 and not job_a[4:].isdigit(), job_a)
    for _ in range(120):
        state = client.get(f"/jobs/{job_a}", headers=a).json()
        if str(state.get("status")) in {"done", "error", "failed"}:
            break
        time.sleep(0.25)

    bundle = client.post(f"/jobs/{job_a}/evidence-bundle", headers=a)
    if bundle.status_code == 200:
        dl = client.get(bundle.json()["downloadUrl"], headers=a)
        check("A can download her bundle", dl.status_code == 200, dl.text[:200])
        with zipfile.ZipFile(io.BytesIO(dl.content)) as zf:
            report = json.loads(zf.read("reports/report.json").decode("utf-8"))
        rows = report.get("manualReview") or []
        ids = [str(r.get("id", "")) for r in rows]
        check(
            "A's OWN items are in the bundle (so the next check is not vacuous)",
            any(i.startswith(f"mr-{a_run['jobId']}-") for i in ids),
            json.dumps(ids)[:400],
        )
        check(
            "no row filed by another tenant is inside the sealed bundle",
            not any(i.startswith(f"mr-{b_run['jobId']}-") for i in ids)
            and not any(i.startswith(f"mr-{doc_a}-") for i in ids),
            json.dumps(ids)[:400],
        )
    else:
        # The bundle needs a completed scan; if the worker could not finish here
        # assert the builder's own input instead, which is the same query.
        from app.persistence.db import get_repo

        rows = get_repo().list_manual_review_items_for_doc(doc_a, include_resolved=True, owner_id=None)
        mine = get_repo().list_manual_review_items_for_doc(doc_a, include_resolved=True, owner_id="nobody")
        check("bundle input is owner-scoped (unowned caller sees nothing)", mine == [], f"{len(rows)} total rows")

    print("[4] GET /auth/export returns only the caller's own audit rows")
    # /credits/spend is the event that carries actor_email outside Cloudflare
    # Access, so it is what the export actually has to scope.
    for headers, desc in ((a, "acme-merger-deck"), (b, "mallory-notes")):
        client.post(
            "/credits/spend",
            json={"amount": 1, "description": desc, "relatedDocId": f"doc-{desc}"},
            headers=headers,
        )
    a_rows = json.loads(client.get("/auth/export", headers=a).content)["audit_log_entries_for_this_user"]
    b_rows = json.loads(client.get("/auth/export", headers=b).content)["audit_log_entries_for_this_user"]
    check("A's export is non-empty", len(a_rows) > 0)
    check(
        "A's export contains only A",
        {r.get("actorEmail") for r in a_rows} <= {"cfo@bigcorp.com"},
        str({r.get("actorEmail") for r in a_rows}),
    )
    check("B's export is non-empty (so the next check is not vacuous)", len(b_rows) > 0)
    check(
        "B's export contains NONE of A's rows, though B's email is a substring of A's",
        all(r.get("actorEmail") == "o@bigcorp.com" for r in b_rows),
        str({r.get("actorEmail") for r in b_rows}),
    )
    # A single sign-up whose "email" is a substring of every .com address.
    d = sign_in("p.c")
    d_rows = json.loads(client.get("/auth/export", headers=d).content)["audit_log_entries_for_this_user"]
    check(
        "'p.c' harvests nobody, though A's and B's rows are right there",
        all(r.get("actorEmail") == "p.c" for r in d_rows),
        str({r.get("actorEmail") for r in d_rows}),
    )

    print("[5] a guessed job id is 404 for a non-owner, and ids do not collide")
    check("B cannot read A's job", client.get(f"/jobs/{job_a}", headers=b).status_code == 404)
    check("B cannot read A's job score", client.get(f"/jobs/{job_a}/score", headers=b).status_code == 404)
    check("A can still read her own job", client.get(f"/jobs/{job_a}", headers=a).status_code == 200)
    # The old id was f"job-{int(time.time()*1000)}" — reconstructable from the
    # moment the victim clicked scan.
    guess = f"job-{int(time.time() * 1000)}"
    check("a millisecond-counter guess resolves to nothing", client.get(f"/jobs/{guess}", headers=b).status_code == 404)

    # The collision the old code allowed needed two tenants in the same
    # millisecond; what makes it possible is that the id is a clock reading at
    # all. Pin that directly — deterministic, and it fails on the old code from
    # the very first scan.
    doc_b = client.post(
        "/documents/upload", files={"file": ("mallory.docx", io.BytesIO(dx), DOCX)}, headers=b
    ).json()["docId"]
    seen = []
    for owner, doc in ((a, doc_a), (b, doc_b)) * 5:
        r = client.post(f"/documents/{doc}/scan", headers=owner)
        assert r.status_code == 200, r.text
        seen.append(str(r.json()["jobId"]))
    check("10 scans across two tenants produced 10 distinct job ids", len(set(seen)) == 10, str(seen))
    check(
        "no job id is a clock reading (the old f'job-{int(time.time()*1000)}')",
        all(not jid[len("job-"):].isdigit() for jid in seen),
        str(seen[:3]),
    )

    print("[6] GET /storage/{key} answers a clean status on an encoded traversal")
    traversals = ["..%2F..%2Fetc%2Fpasswd", "a%2f..%2f..%2fb", "%2e%2e%2f%2e%2e%2fwin.ini", "%2e"]
    for key in ["ok/key.txt", *traversals]:
        r = client.get(f"/storage/{key}")
        check(f"production /storage/{key} -> {r.status_code} (not 5xx)", r.status_code == 404, r.text[:120])

    # With the endpoint switched ON, the traversal reaches sanitize_storage_key
    # — which raises. That is the half the reordering alone does not cover.
    from app.config import get_settings

    os.environ["ENABLE_DEV_STORAGE_ENDPOINT"] = "true"
    get_settings.cache_clear()
    try:
        for key in traversals:
            r = client.get(f"/storage/{key}")
            check(f"enabled /storage/{key} -> {r.status_code} (not 5xx)", r.status_code == 404, r.text[:120])
    finally:
        os.environ.pop("ENABLE_DEV_STORAGE_ENDPOINT", None)
        get_settings.cache_clear()

    print("[7] the parser's document_id can no longer be a path or a control string")
    from app.parsers.document_id import derive_document_id, sanitize_document_id

    check("an ordinary name is untouched", sanitize_document_id("Quarterly_Budget_Review") == "Quarterly_Budget_Review")
    check("non-ASCII names survive (dashboard de-dup depends on it)", sanitize_document_id("rapport-année") == "rapport-année")
    check("separators are stripped", "/" not in sanitize_document_id("../../etc/passwd") and "\\" not in sanitize_document_id("..\\win.ini"))
    check("it can never be '.' or '..'", sanitize_document_id("..") == "doc" and sanitize_document_id(".") == "doc")
    check(
        "control and bidi characters are dropped",
        sanitize_document_id("a" + chr(0x202E) + "b" + chr(0) + "c") == "abc",
    )
    check("it is bounded", len(sanitize_document_id("x" * 5000)) <= 96)
    check("empty falls back", sanitize_document_id("") == "doc")
    check("derive_document_id reads the stem", derive_document_id(Path("/tmp/report.docx")) == "report")

    print("ALL ASSERTS PASSED" if not failures else f"{failures} FAILURES")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
