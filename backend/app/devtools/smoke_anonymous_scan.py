"""Smoke: the account-free scan works, and can never spend AI money or save anything.

POST /pipeline/analyze with NO credential used to answer 401, so the product's
"drop a file, see what's wrong, sign up only to fix it" flow was impossible:
a first-time visitor met a sign-in wall before seeing a single finding.

Anonymous callers now get the real findings (with autoFixable, location and the
summary plan), under guards that make the free path structurally cheap:

  1. No AI, of any kind: no provider method is called, no inference client is
     constructed (not even the offline heuristic one — no executor runs), and
     build_default_provider is never invoked. ``?execute=true`` is ignored.
     Proven against a fake PAID provider that counts every touch.
  2. Nothing persisted: every table in the database has the same row count
     after anonymous scans (no score row, no audit row, no user), nothing is
     written under the materialized/storage roots, and the private copy kept
     for thumbnails is gone from the temp dir.
  3. A smaller upload cap (ANON_SCAN_MAX_MB) with a sentence that says how to
     get the full one; the same file is fine for a signed-in user.
  4. A strict per-IP budget (ANON_SCAN_PER_HOUR) answered with a coded 429
     (``rate_limited``, ``scope: anonymous_scan``) that points at a free account — while a signed-in
     user on the same address is unaffected.
  5. Credentials that are PRESENTED but invalid are still 401, never a silent
     downgrade to anonymous; remediation still requires a session.
  6. Authenticated behaviour is unchanged: the score row is saved, and
     ``?execute=true`` still runs the (offline) executors.

Run: python -m app.devtools.smoke_anonymous_scan
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="508_smoke_anon_"))
_DB = _TMP / "anon.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["STORAGE_LOCAL_ROOT"] = str(_TMP / "storage")
os.environ["MATERIALIZED_ROOT"] = str(_TMP / "materialized")
os.environ["TRUST_PROXY_HEADERS"] = "true"  # one bucket per section via X-Forwarded-For
os.environ["ANON_SCAN_MAX_MB"] = "1"
os.environ["ANON_SCAN_PER_HOUR"] = "6"
for _key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SEMANTIC_PROVIDER", "SMTP_HOST"):
    os.environ.pop(_key, None)

from fastapi.testclient import TestClient  # noqa: E402


def _page(pad_bytes: int = 0) -> bytes:
    parts = ["<!doctype html><html><head></head><body><h1>Budget</h1><h3>Detail</h3>"]
    for i in range(6):
        parts.append(f'<p>Figure {i}: revenue for region {i}.</p><img src="chart{i}.png">')
    for i in range(6):
        parts.append(f'<p>Details: <a href="https://example.com/r{i}.pdf">click here</a></p>')
    parts.append("<table><tr><td>Region</td><td>Q1</td></tr><tr><td>North</td><td>1</td></tr></table>")
    if pad_bytes:
        word = "accessible "
        parts.append("<p>" + word * (pad_bytes // len(word)) + "</p>")
    parts.append("</body></html>")
    return "".join(parts).encode("utf-8")


def _install_fake_paid_provider():
    import app.ai.semantic_inference as si

    touches = {"calls": 0, "clients": 0, "builds": 0}

    class _FakePaid(si.HeuristicProvider):
        name = "fake-paid"

        def _bill(self, text: str):
            touches["calls"] += 1
            return si.InferenceResult(text=text, confidence=0.9, provider=self.name, raw={})

        def alt_text(self, payload):
            return self._bill("A bar chart")

        def link_text(self, payload):
            return self._bill("Download the report")

        def table_caption(self, payload):
            return self._bill("Revenue by region")

        def document_title(self, payload):
            return self._bill("Budget")

        def document_language(self, payload):
            return self._bill("en")

    def _build(*a, **k):
        touches["builds"] += 1
        return _FakePaid()

    si.build_default_provider = _build
    real_init = si.SemanticInferenceClient.__init__

    def _counting_init(self, *args, **kwargs):
        touches["clients"] += 1
        kwargs.setdefault("provider", _FakePaid())
        real_init(self, *args, **kwargs)

    si.SemanticInferenceClient.__init__ = _counting_init
    return touches


def _row_counts() -> dict:
    con = sqlite3.connect(str(_DB))
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        con.close()


def _files_under(root: Path) -> list:
    return sorted(str(p) for p in root.rglob("*") if p.is_file()) if root.exists() else []


def _loc_temp_files() -> set:
    return {p.name for p in Path(tempfile.gettempdir()).glob("508loc_*")}


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, detail: object = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, "" if cond else f"  [{str(detail)[:400]}]")
        if not cond:
            failures += 1

    touches = _install_fake_paid_provider()
    from app.db.models import AnalysisResultRow, UserRow
    from app.db.session_sqlalchemy import session_scope
    from app.main import app
    from sqlalchemy import func, select

    def client(ip: str) -> TestClient:
        return TestClient(app, headers={"X-Forwarded-For": ip})

    page = _page()
    anon = client("198.51.100.10")

    # --- 1 + 2: findings, no AI, nothing saved ------------------------------------
    before_rows = _row_counts()
    before_files = _files_under(_TMP / "materialized") + _files_under(_TMP / "storage")
    before_tmp = _loc_temp_files()
    t0 = dict(touches)
    r = anon.post("/pipeline/analyze", files={"file": ("budget.html", page, "text/html")})
    check("anonymous analyze -> 200", r.status_code == 200, r.text[:300])
    body = r.json() if r.status_code == 200 else {}
    vs = body.get("violations") or []
    check("anonymous analyze returns real findings", len(vs) >= 4, [v.get("ruleId") for v in vs])
    check(
        "...each with autoFixable and a location",
        bool(vs) and all(isinstance(v.get("autoFixable"), bool) and isinstance(v.get("location"), dict) for v in vs),
    )
    s = body.get("summary") or {}
    check(
        "...and the plan summary (total / autoFixable / needsYou / cost)",
        s.get("total") == len(vs) and s.get("autoFixable", -1) >= 1 and s.get("needsYou") == len(vs) - s.get("autoFixable", 0)
        and s.get("cost") == 3,
        s,
    )
    link = [v for v in vs if v["ruleId"] == "LINK_TEXT_NON_DESCRIPTIVE"]
    check(
        "...and the location points at the words ('click here' inside 'Details: click here')",
        bool(link) and link[0]["location"]["highlight"] == "click here" and link[0]["location"]["snippet"] == "Details: click here",
        link[:1],
    )
    r2 = anon.post("/pipeline/analyze?execute=true", files={"file": ("budget.html", page, "text/html")})
    check("anonymous ?execute=true -> 200 with NO executions (execute ignored)", r2.status_code == 200 and r2.json().get("executions") == [], r2.text[:200])
    check("no AI provider method was called", touches["calls"] == t0["calls"], touches)
    check("no inference client was constructed (not even offline)", touches["clients"] == t0["clients"], touches)
    check("build_default_provider was never invoked", touches["builds"] == t0["builds"], touches)
    check("aiProvider is still reported (from config, nothing built)", r.json().get("aiProvider") == "heuristic", r.json().get("aiProvider"))
    check("no row was written to ANY table", _row_counts() == before_rows, (before_rows, _row_counts()))
    check(
        "no file was written under the materialized or storage roots",
        _files_under(_TMP / "materialized") + _files_under(_TMP / "storage") == before_files,
    )
    check("the private copy kept for thumbnails is gone", _loc_temp_files() - before_tmp == set(), _loc_temp_files() - before_tmp)

    # --- 5: invalid credentials are not a back door to "anonymous" ----------------
    bad = client("198.51.100.20")
    r = bad.post("/pipeline/analyze", files={"file": ("budget.html", page, "text/html")}, headers={"Authorization": "Bearer not-a-real-token"})
    check("a presented-but-invalid session -> 401, not an anonymous scan", r.status_code == 401 and r.json().get("code") == "authentication_required", r.text[:200])
    r = bad.post("/pipeline/analyze", files={"file": ("budget.html", page, "text/html")}, headers={"X-API-Key": "ak_live_bogus"})
    check("a bogus API key -> 401", r.status_code == 401, r.status_code)
    r = bad.post(
        "/pipeline/remediate",
        files={"file": ("budget.html", page, "text/html")},
        data={"approved_violations": "[]", "rejected_violations": "[]"},
    )
    check("anonymous remediate is still 401 (fixing needs an account)", r.status_code == 401, r.status_code)

    # --- 3: the smaller upload cap -------------------------------------------------
    big = _page(pad_bytes=int(1.5 * 1024 * 1024))
    cap = client("198.51.100.30")
    r = cap.post("/pipeline/analyze", files={"file": ("big.html", big, "text/html")})
    msg = (r.json() or {}).get("message", "") if r.headers.get("content-type", "").startswith("application/json") else ""
    check(
        "anonymous 1.5 MB file over the 1 MB anonymous cap -> 413 too_large",
        r.status_code == 413 and r.json().get("code") == "too_large",
        r.text[:200],
    )
    check("...with a sentence that says how to get the full cap", "1 MB" in msg and "free account" in msg, msg)

    # --- 6: authenticated behaviour unchanged --------------------------------------
    user = client("198.51.100.40")
    si = user.post("/auth/sign-in", json={"email": "anon-smoke@example.com", "password": "anonsmoke1"})
    assert si.status_code == 200, si.text
    auth = {"Authorization": f"Bearer {si.json()['token']}"}
    with session_scope() as sess:
        rows_before = int(sess.execute(select(func.count()).select_from(AnalysisResultRow)).scalar() or 0)
    r = user.post("/pipeline/analyze", files={"file": ("big.html", big, "text/html")}, headers=auth)
    check("the same 1.5 MB file is fine for a signed-in user", r.status_code == 200, r.text[:200])
    with session_scope() as sess:
        rows_after = int(sess.execute(select(func.count()).select_from(AnalysisResultRow)).scalar() or 0)
    check("a signed-in scan still saves its score row", rows_after == rows_before + 1, (rows_before, rows_after))
    r = user.post("/pipeline/analyze?execute=true", files={"file": ("budget.html", page, "text/html")}, headers=auth)
    check("a signed-in ?execute=true still runs the executors", r.status_code == 200 and len(r.json().get("executions") or []) > 0, r.text[:200])
    check("...and even then, never on the paid provider", touches["calls"] == t0["calls"], touches)

    # --- 4: the strict anonymous budget --------------------------------------------
    burst = client("198.51.100.50")
    codes = [burst.post("/pipeline/analyze", files={"file": ("b.html", page, "text/html")}).status_code for _ in range(6)]
    check("6 anonymous scans in an hour are allowed", codes == [200] * 6, codes)
    r = burst.post("/pipeline/analyze", files={"file": ("b.html", page, "text/html")})
    b = r.json() if r.status_code == 429 else {}
    check(
        "the 7th -> 429 code rate_limited, scope anonymous_scan (detail still 'rate_limited')",
        r.status_code == 429 and b.get("code") == "rate_limited" and b.get("scope") == "anonymous_scan"
        and b.get("detail") == "rate_limited",
        r.text[:200],
    )
    check(
        "...with Retry-After and a sentence pointing at a free account",
        int(r.headers.get("Retry-After", "0") or 0) > 0 and b.get("retryAfter") == int(r.headers.get("Retry-After", "0") or 0)
        and "free account" in (b.get("message") or ""),
        (r.headers.get("Retry-After"), b),
    )
    si2 = burst.post("/auth/sign-in", json={"email": "same-ip@example.com", "password": "sameippass1"})
    r = burst.post(
        "/pipeline/analyze",
        files={"file": ("b.html", page, "text/html")},
        headers={"Authorization": f"Bearer {si2.json()['token']}"},
    )
    check("a signed-in user on that same address is unaffected", r.status_code == 200, r.status_code)
    with session_scope() as sess:
        users = int(sess.execute(select(func.count()).select_from(UserRow)).scalar() or 0)
    check("(sanity) only the two real sign-ups created users", users == 2, users)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
