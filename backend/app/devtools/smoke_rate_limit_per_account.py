"""Smoke: signed-in work is rate-limited per ACCOUNT; anonymous and credential
traffic stays strictly per IP.

Robustness finding (high, reproduced): one 60 req/min bucket PER IP covered
/pipeline + /credits + /auth. The batch page makes ~3 calls per file with three
files in flight, so a ~30-file batch — or an office of people behind one NAT —
hit 429 and the UI showed the raw ``{"detail":"rate_limited"}``.

Pins:
  1. NAT office: two accounts behind ONE address make 100 document-work calls
     each — no 429 (the old limiter cut the whole address off at 60).
  2. Each account still has its own ceiling (RATE_LIMIT_USER_PER_MIN, default
     300): the 301st call is a 429, and the neighbour on the same address is
     unaffected.
  3. Anonymous traffic keeps the strict 60/min per IP.
  4. Credential endpoints (/auth/sign-in ...) stay per IP even when the request
     carries a valid session — a token can't buy extra password guesses.
  5. Credentials that don't verify never buy a bucket: rotating forged bearer
     tokens or random ``ak_`` keys is counted against the IP.
  6. A real API key spends its OWNER's account budget (so scans from CI behind
     a busy address keep working); GET /auth/me is per account too.
  7. Signed artifact downloads don't spend the anonymous 60/min.
  8. A 429 is coded: ``detail: rate_limited``, ``code``, a sentence, and
     ``retryAfter`` equal to the Retry-After header (seconds until a slot frees,
     never more than the window).
  9. One address can't multiply budgets by minting accounts: the
     authenticated per-IP backstop (probed on a small middleware instance).

Run: python -m app.devtools.smoke_rate_limit_per_account
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="508_smoke_ratelimit_"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'rl.db').as_posix()}"
os.environ["STORAGE_LOCAL_ROOT"] = str(_TMP / "storage")
os.environ["MATERIALIZED_ROOT"] = str(_TMP / "materialized")
os.environ["TRUST_PROXY_HEADERS"] = "true"
for _key in (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SEMANTIC_PROVIDER", "SMTP_HOST",
    "RATE_LIMIT_USER_PER_MIN", "ANON_SCAN_PER_HOUR", "RATE_LIMIT_AUTHENTICATED_IP_PER_MIN",
):
    os.environ.pop(_key, None)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_HTML = b"<!doctype html><html><head><title>t</title></head><body><p>Hello there.</p></body></html>"


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, detail: object = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, "" if cond else f"  [{str(detail)[:400]}]")
        if not cond:
            failures += 1

    from app.db.models import UserRow
    from app.db.session_sqlalchemy import session_scope
    from app.main import app
    from app.security.rate_limit import RateLimitMiddleware
    from app.security.sessions import mint_session

    def client(ip: str) -> TestClient:
        return TestClient(app, headers={"X-Forwarded-For": ip})

    def signup(c: TestClient, email: str) -> dict:
        r = c.post("/auth/sign-in", json={"email": email, "password": "ratelimit-pass-1"})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def coded_429(r) -> bool:
        if r.status_code != 429:
            return False
        b = r.json()
        ra = int(r.headers.get("Retry-After", "0") or 0)
        return (
            b.get("detail") == "rate_limited" and b.get("code") == "rate_limited"
            and isinstance(b.get("message"), str) and "wait" in b["message"]
            and b.get("retryAfter") == ra and 0 < ra <= 60
        )

    # --- 1 + 2: NAT office -------------------------------------------------------
    setup = client("10.9.0.1")
    alice = signup(setup, "alice-nat@example.com")
    bob = signup(setup, "bob-nat@example.com")
    office = client("203.0.113.77")
    a_codes = [office.get("/credits/balance", headers=alice).status_code for _ in range(100)]
    b_codes = [office.get("/credits/balance", headers=bob).status_code for _ in range(100)]
    check("NAT: account A, 100 calls from one address -> no 429", a_codes == [200] * 100, sorted(set(a_codes)))
    check("NAT: account B, 100 more from the same address -> no 429", b_codes == [200] * 100, sorted(set(b_codes)))
    more = [office.get("/pipeline/jobs", headers=alice).status_code for _ in range(200)]
    check("account A reaches its own 300/min ceiling (not the address's 60)", more.count(200) == 200, sorted(set(more)))
    r = office.get("/credits/balance", headers=alice)
    check("account A's 301st work call -> coded 429", coded_429(r), (r.status_code, r.text[:200], r.headers.get("Retry-After")))
    r = office.get("/credits/balance", headers=bob)
    check("...while account B on the same address is unaffected", r.status_code == 200, r.status_code)

    # --- 3: anonymous stays 60/min per IP ---------------------------------------
    anon = client("203.0.113.88")
    codes = [anon.get("/credits/balance").status_code for _ in range(60)]
    check("anonymous: 60 calls answer normally (401, not 429)", codes == [401] * 60, sorted(set(codes)))
    r = anon.get("/credits/balance")
    check("anonymous: the 61st from that IP -> coded 429", coded_429(r), (r.status_code, r.text[:200]))

    # --- 4: credential endpoints stay per IP, token or not -----------------------
    guess = client("203.0.113.99")
    tok = signup(client("10.9.0.2"), "carol-guess@example.com")
    codes = [
        guess.post("/auth/sign-in", json={"email": f"nobody{i}@example.com", "password": "x"}, headers=tok).status_code
        for i in range(60)
    ]
    check("sign-in with a VALID token attached: 60 answered normally", 429 not in codes, sorted(set(codes)))
    r = guess.post("/auth/sign-in", json={"email": "nobody-last@example.com", "password": "x"}, headers=tok)
    check("...the 61st -> 429: a session never lifts the per-IP credential limit", coded_429(r), (r.status_code, r.text[:160]))

    # --- 5: forged credentials never buy a bucket --------------------------------
    forged = client("203.0.113.111")
    codes = [
        forged.get("/credits/balance", headers={"Authorization": f"Bearer {secrets.token_urlsafe(24)}"}).status_code
        for _ in range(60)
    ]
    r = forged.get("/credits/balance", headers={"Authorization": f"Bearer {secrets.token_urlsafe(24)}"})
    check("61 forged bearer tokens from one IP -> the 61st is 429", 429 not in codes and r.status_code == 429, (sorted(set(codes)), r.status_code))
    keys = client("203.0.113.112")
    codes = [
        keys.get("/credits/balance", headers={"X-API-Key": f"ak_live_{secrets.token_hex(8)}"}).status_code
        for _ in range(60)
    ]
    r = keys.get("/credits/balance", headers={"X-API-Key": f"ak_live_{secrets.token_hex(8)}"})
    check("61 random ak_ keys from one IP -> the 61st is 429", 429 not in codes and r.status_code == 429, (sorted(set(codes)), r.status_code))
    scans = client("203.0.113.113")
    codes = [
        scans.post("/pipeline/analyze", files={"file": ("t.html", _HTML, "text/html")}, headers={"X-API-Key": f"ak_live_{secrets.token_hex(8)}"}).status_code
        for _ in range(11)
    ]
    check(
        "random ak_ keys on the scan endpoint are held to the ANONYMOUS scan budget (11th -> 429)",
        codes == [401] * 10 + [429],
        codes,
    )

    # --- 6: a real API key spends its owner's budget; /auth/me per account -------
    owner = signup(client("10.9.0.3"), "dave-ci@example.com")
    created = client("10.9.0.3").post("/api-keys", json={"name": "CI"}, headers=owner)
    assert created.status_code == 200, created.text
    key = created.json()["key"]
    ci = client("203.0.113.88")  # the address whose anonymous bucket is already spent
    codes = [
        ci.post("/pipeline/analyze", files={"file": ("t.html", _HTML, "text/html")}, headers={"X-API-Key": key}).status_code
        for _ in range(70)
    ]
    check("a real API key scans 70 times from an address whose anonymous bucket is spent", codes == [200] * 70, sorted(set(codes)))
    codes = [ci.get("/auth/me", headers=owner).status_code for _ in range(59)]
    check("GET /auth/me is per account (59 more from that same spent address)", codes == [200] * 59, sorted(set(codes)))

    # --- 7: signed downloads are not the anonymous bucket ------------------------
    with session_scope() as s:
        row = s.get(UserRow, client("10.9.0.4").get("/auth/me", headers=owner).json()["id"])
        row.credits_balance = 50
    page = b"<!doctype html><html><head></head><body><img src='a.png'><p>x</p></body></html>"
    ana = client("10.9.0.4").post("/pipeline/analyze", files={"file": ("d.html", page, "text/html")}, headers=owner).json()
    rem = client("10.9.0.4").post(
        "/pipeline/remediate",
        files={"file": ("d.html", page, "text/html")},
        data={"approved_violations": json.dumps([v["id"] for v in ana["violations"]]), "rejected_violations": "[]"},
        headers=owner,
    )
    url = rem.json().get("downloadUrl") if rem.status_code == 200 else None
    check("(setup) a remediated file to download", bool(url), rem.text[:200])
    if url:
        dl = client("203.0.113.88")  # the same spent address
        codes = [dl.get(url).status_code for _ in range(70)]
        check("70 signed downloads from an address whose anonymous bucket is spent -> all 200", codes == [200] * 70, sorted(set(codes)))

    # --- 9: the authenticated per-IP backstop (small probe instance) --------------
    probe_app = FastAPI()

    @probe_app.get("/credits/ping")
    def _ping():
        return {"ok": True}

    probe_app.add_middleware(
        RateLimitMiddleware, trust_proxy_headers=True, limit=60, user_limit=5, authenticated_ip_limit=8,
    )
    probe = TestClient(probe_app, headers={"X-Forwarded-For": "192.0.2.5"})
    tokens = [mint_session(f"user-{i}") for i in range(3)]
    codes = [probe.get("/credits/ping", headers={"Authorization": f"Bearer {t}"}).status_code for t in tokens for _ in range(3)]
    check("backstop: 3 accounts x 3 calls from one IP -> the 9th is 429 (IP ceiling 8)", codes == [200] * 8 + [429], codes)
    probe2 = TestClient(probe_app, headers={"X-Forwarded-For": "192.0.2.6"})
    t = mint_session("user-solo")
    codes = [probe2.get("/credits/ping", headers={"Authorization": f"Bearer {t}"}).status_code for _ in range(6)]
    check("per-account ceiling on the probe: the 6th call -> 429 (user limit 5)", codes == [200] * 5 + [429], codes)

    defaults = RateLimitMiddleware(app)
    check(
        "shipped defaults: 60/min per IP, 300/min per account, 10/hour anonymous scans, 1200/min per IP backstop",
        (defaults._limit, defaults._user_limit, defaults._anon_scan_limit, defaults._auth_ip_limit) == (60, 300, 10, 1200),
        (defaults._limit, defaults._user_limit, defaults._anon_scan_limit, defaults._auth_ip_limit),
    )

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
