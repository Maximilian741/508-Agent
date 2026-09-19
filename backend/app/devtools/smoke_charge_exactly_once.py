"""Smoke: every delivery path charges exactly once, and only the owner's files ship.

Four adversarial-review findings, pinned together because they share one money
path (the deferred charge taken when /remediate's client disconnected):

  * POST /pipeline/batch-zip checked only ownerEmail (empty unless Cloudflare
    Access is on): any signed-in account could zip another tenant's file, or
    its meta.json. Now owner-scoped like GET /pipeline/jobs -> 404.
  * batch-zip delivered chargePending jobs with no debit. Now it takes the
    deferred charge — once, however many times the job is zipped.
  * The deferred charge read the manifest flag, charged, then wrote the flag
    back: 10 concurrent downloads = 10 debits. Now keyed in the ledger in the
    same transaction as the debit -> exactly one.
  * spend_credits_for_user was read-check-write, unlocked on sqlite: 8
    concurrent spends of a 5-credit balance all succeeded. Now one conditional
    UPDATE -> never overdraws, and the ledger sum equals the balance delta.
  * The deferred charge's idempotency key lived in a namespace the CALLER
    could write: one /credits/spend of 1 credit at "pipeline-job:<jobId>"
    satisfied the one-shot check and bought a 3-credit file for 1. The key is
    now a reserved prefix /credits/spend refuses, and the check is keyed on a
    ledger kind only the server writes.

Also pins the failure order: a deferred charge the wallet can't cover withholds
the file (402) and records nothing, and the same download works after a top-up.

Usage:
    python -m app.devtools.smoke_charge_exactly_once
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import zipfile
from datetime import datetime
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="508_smoke_once_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/s.db"
os.environ["MATERIALIZED_ROOT"] = str(Path(_TMP) / "materialized")

from docx import Document  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from starlette.requests import Request  # noqa: E402

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
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    from app.api.credits import (
        SPEND_ONCE_KIND,
        InsufficientCreditsError,
        spend_credits_for_user,
        spend_credits_once_for_user,
    )
    from app.config import get_settings
    from app.db.models import CreditLedgerRow, UserRow
    from app.db.session_sqlalchemy import session_scope
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    def sign_in(email: str):
        r = client.post("/auth/sign-in", json={"email": email, "displayName": "X", "password": "exactlyonce1"})
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        client.post("/auth/grant-starter", headers=h)
        with session_scope() as s:
            return h, s.execute(select(UserRow.id).where(UserRow.email == email)).scalar_one()

    def balance(uid: str) -> int:
        with session_scope() as s:
            return int(s.execute(select(UserRow.credits_balance).where(UserRow.id == uid)).scalar_one() or 0)

    def set_balance(uid: str, n: int) -> None:
        with session_scope() as s:
            s.execute(select(UserRow).where(UserRow.id == uid)).scalar_one().credits_balance = n

    def spends(uid: str, key: str | None = None) -> list:
        # Both debit kinds: the ordinary "spend" and the reserved one-shot kind
        # the deferred charge uses (a caller can only ever write "spend", which
        # is exactly what stops it cancelling a job's debit — case 7).
        with session_scope() as s:
            q = select(CreditLedgerRow.amount).where(
                CreditLedgerRow.user_id == uid,
                CreditLedgerRow.kind.in_(("spend", SPEND_ONCE_KIND)),
            )
            if key is not None:
                q = q.where(CreditLedgerRow.related_doc_id == key)
            return [int(a) for a in s.execute(q).scalars()]

    def meta(job_id: str) -> dict:
        p = get_settings().materialized_root / "pipeline" / job_id / "meta.json"
        return json.loads(p.read_text(encoding="utf-8"))

    hA, uA = sign_in("owner@example.com")
    hB, uB = sign_in("other-tenant@example.com")
    set_balance(uB, 0)

    dx = _docx()
    a = client.post("/pipeline/analyze", files={"file": ("Quarterly_Budget_Review.docx", dx, DOCX)}, headers=hA)
    title_id = {v["ruleId"]: v["id"] for v in a.json()["violations"]}.get("DOCUMENT_TITLE_MISSING")
    check("fixture has a fixable DOCUMENT_TITLE_MISSING", bool(title_id))

    # /remediate with the client already gone -> artifact kept, charge deferred.
    real_is_disconnected = Request.is_disconnected

    async def _gone(self):  # noqa: ANN001
        return True

    def pending_job(name: str) -> dict:
        Request.is_disconnected = _gone
        try:
            rr = client.post("/pipeline/remediate", files={"file": (name, dx, DOCX)},
                             data={"approved_violations": json.dumps([title_id]), "rejected_violations": "[]"},
                             headers=hA)
        finally:
            Request.is_disconnected = real_is_disconnected
        body = rr.json()
        check(f"{name}: remediate with client gone -> chargePending, not charged",
              rr.status_code == 200 and body.get("chargePending") is True and body.get("charged") is False,
              f"{rr.status_code} {str(body)[:200]}")
        return body

    # ---- 1. batch-zip is owner-scoped ------------------------------------
    job1 = pending_job("Quarterly_Budget_Review.docx")
    zip_job1 = {"jobs": [{"jobId": job1["jobId"], "filename": job1["filename"]}]}
    a_before = balance(uA)
    r = client.post("/pipeline/batch-zip", json=zip_job1, headers=hB)
    check("other tenant zips the owner's job -> 404", r.status_code == 404, f"got {r.status_code}")
    r = client.post("/pipeline/batch-zip", json={"jobs": [{"jobId": job1["jobId"], "filename": "meta.json"}]}, headers=hB)
    check("other tenant zips the owner's meta.json -> 404", r.status_code == 404, f"got {r.status_code}")
    check("...and the refused attempts debited nobody",
          balance(uA) == a_before and spends(uB) == [] and meta(job1["jobId"])["chargePending"] is True)
    r = client.post("/pipeline/batch-zip", json={"jobs": [{"jobId": job1["jobId"], "filename": "meta.json"}]}, headers=hA)
    check("the owner can't zip meta.json either (only the recorded artifact) -> 404", r.status_code == 404,
          f"got {r.status_code}")
    source_name = job1["filename"].replace("-remediated", "")
    r = client.post("/pipeline/batch-zip", json={"jobs": [{"jobId": job1["jobId"], "filename": source_name}]}, headers=hA)
    check("...nor the original upload -> 404", r.status_code == 404, f"got {r.status_code}")

    # ---- 2. batch-zip takes a pending job's deferred charge exactly once --
    key1 = f"pipeline-job:{job1['jobId']}"
    a_before = balance(uA)
    names = []
    for _ in range(3):
        r = client.post("/pipeline/batch-zip", json=zip_job1, headers=hA)
        names = zipfile.ZipFile(io.BytesIO(r.content)).namelist() if r.status_code == 200 else []
    check("owner zips the pending job (x3) -> 200 with the file", names == [job1["filename"]], str(names))
    check("batch-zip x3 debited exactly once (3 credits)", spends(uA, key1) == [-3] and a_before - balance(uA) == 3,
          f"rows={spends(uA, key1)} {a_before} -> {balance(uA)}")
    check("manifest flipped to charged", meta(job1["jobId"])["chargePending"] is False and meta(job1["jobId"])["charged"])
    r = client.get(job1["downloadUrl"])
    check("signed download after the zip paid -> 200, no second debit",
          r.status_code == 200 and spends(uA, key1) == [-3], f"{r.status_code} {spends(uA, key1)}")

    # ---- 3. N concurrent downloads of one pending job debit once ----------
    job2 = pending_job("Annual_Program_Report.docx")
    key2 = f"pipeline-job:{job2['jobId']}"
    a_before = balance(uA)
    n = 10
    barrier = threading.Barrier(n)
    codes: list = []

    def download() -> None:
        barrier.wait()
        codes.append(client.get(job2["downloadUrl"]).status_code)

    threads = [threading.Thread(target=download) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check(f"{n} concurrent downloads -> all 200", sorted(codes) == [200] * n, str(sorted(codes)))
    check(f"{n} concurrent downloads -> exactly one debit", spends(uA, key2) == [-3], str(spends(uA, key2)))
    check("balance dropped by exactly one charge", a_before - balance(uA) == 3, f"{a_before} -> {balance(uA)}")

    # ---- 4. failure order: an unpayable deferred charge -------------------
    job3 = pending_job("Regional_Safety_Plan.docx")
    key3 = f"pipeline-job:{job3['jobId']}"
    set_balance(uA, 0)
    r = client.get(job3["downloadUrl"])
    check("wallet empty -> signed download withheld (402)", r.status_code == 402, f"got {r.status_code}")
    r = client.post("/pipeline/batch-zip", json={"jobs": [{"jobId": job3["jobId"], "filename": job3["filename"]}]},
                    headers=hA)
    check("wallet empty -> batch-zip withheld (402), not a free zip", r.status_code == 402, f"got {r.status_code}")
    check("...nothing recorded, still pending", spends(uA, key3) == [] and meta(job3["jobId"])["chargePending"] is True)
    set_balance(uA, 10)
    r = client.get(job3["downloadUrl"])
    check("after a top-up the same download works (never stuck) and pays once",
          r.status_code == 200 and spends(uA, key3) == [-3] and balance(uA) == 7,
          f"{r.status_code} {spends(uA, key3)} bal={balance(uA)}")

    # ---- 5. N concurrent spends never overdraw ----------------------------
    _, uC = sign_in("spender@example.com")
    set_balance(uC, 20)
    spent_before = sum(spends(uC))
    n = 12
    barrier = threading.Barrier(n)
    results: list = []

    def spend() -> None:
        barrier.wait()
        try:
            results.append(("ok", spend_credits_for_user(uC, 5, "race")))
        except InsufficientCreditsError:
            results.append(("insufficient", None))
        except Exception as exc:  # a lock timeout would be a real failure too
            results.append(("error", repr(exc)[:120]))

    threads = [threading.Thread(target=spend) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    oks = [r for r in results if r[0] == "ok"]
    check(f"{n} concurrent spends of 5 on a balance of 20 -> exactly 4 succeed", len(oks) == 4, str(results))
    check("...the rest are insufficient_credits (no errors)",
          sum(1 for r in results if r[0] == "insufficient") == n - 4, str(results))
    check("final balance 0, never negative", balance(uC) == 0, f"got {balance(uC)}")
    check("ledger sum equals the balance delta", sum(spends(uC)) - spent_before == -20,
          f"ledger {sum(spends(uC)) - spent_before} vs delta -20")
    check("returned balances are distinct (each spend saw its own debit)",
          sorted(r[1] for r in oks) == [0, 5, 10, 15], str(sorted(r[1] for r in oks)))

    # ---- 6. the exactly-once primitive itself -----------------------------
    set_balance(uC, 50)
    barrier = threading.Barrier(8)
    once: list = []

    def spend_once() -> None:
        barrier.wait()
        once.append(spend_credits_once_for_user(uC, 5, "remediate_docx", idempotency_key="pipeline-job:race"))

    threads = [threading.Thread(target=spend_once) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with session_scope() as s:
        rows = s.execute(select(func.count()).select_from(CreditLedgerRow)
                         .where(CreditLedgerRow.related_doc_id == "pipeline-job:race")).scalar_one()
    check("8 racing spend_once calls -> exactly one charged_now", sum(1 for _, c in once if c) == 1, str(once))
    check("...one ledger row, one debit", rows == 1 and balance(uC) == 45, f"rows={rows} bal={balance(uC)}")

    # ---- 7. the key's namespace is the SERVER'S, not the caller's ---------
    # /credits/spend lets the caller choose relatedDocId, and the one-shot
    # lookup used to match any kind="spend" row with that id: pre-writing
    # "pipeline-job:<jobId>" for 1 credit cancelled the job's 3-credit deferred
    # charge, buying the file for a third of its price. Two independent guards
    # now: the reserved prefix is refused outright, and the lookup is keyed on
    # a ledger kind only the server can write.
    job4 = pending_job("Vendor_Compliance_Summary.docx")
    key4 = f"pipeline-job:{job4['jobId']}"
    set_balance(uA, 20)
    r = client.post("/credits/spend", json={"amount": 1, "description": "x", "relatedDocId": key4}, headers=hA)
    check("pre-writing the job's idempotency key via /credits/spend -> refused (422)",
          r.status_code == 422, f"got {r.status_code} {r.text[:120]}")
    check("...and nothing was debited for it", spends(uA, key4) == [], str(spends(uA, key4)))

    # Even a row written straight into the ledger under the caller's own kind
    # must not satisfy the one-shot check.
    with session_scope() as s:
        s.add(CreditLedgerRow(user_id=uA, at=datetime.utcnow(), kind="spend", amount=-1,
                              description="squat", related_doc_id=key4))
    a_before = balance(uA)
    r = client.get(job4["downloadUrl"])
    check("a caller-written 'spend' row at the same key does NOT cancel the deferred charge",
          r.status_code == 200 and a_before - balance(uA) == 3,
          f"{r.status_code} {a_before} -> {balance(uA)}")
    check("...and the job is paid exactly once, under the reserved kind",
          spends(uA, key4) == [-1, -3] and meta(job4["jobId"])["chargePending"] is False,
          str(spends(uA, key4)))
    a_before = balance(uA)
    r = client.get(job4["downloadUrl"])
    check("...a second download still debits nothing", r.status_code == 200 and balance(uA) == a_before,
          f"{r.status_code} {a_before} -> {balance(uA)}")

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
