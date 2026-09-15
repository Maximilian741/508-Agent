"""Smoke: AI spend is capped per JOB, and free paths never reach a paid provider.

Pre-launch audit finding (high, reproduced), three parts:

  1. Legacy POST /remediate was a free, unthrottled AI proxy: fresh clients, and
     so a fresh cost cap, on every request (70 calls from one zero-credit
     account cost $14 of simulated Claude). It is retired: 410, provider untouched.
  2. get_default_executors() built a SEPARATE SemanticInferenceClient per AI
     executor, so MAX_AI_COST_PER_JOB_USD capped each executor, not the job: one
     remediation of an image/link-heavy page spent $1.40 against a $0.50 cap.
     One client is now shared by every executor in a job.
  3. POST /pipeline/analyze?execute=true (free, API-key accessible) ran the
     executors on the DEFAULT provider ($0.80 per request). It is pinned offline
     like the URL scan. requires_ai=False is not a gate: IMPROVE_LINK_TEXT calls
     the inference client anyway.

No real provider is used and no API key is set: the default provider is replaced
by a fake "paid" one that reports Claude-shaped token usage, so the app's own
cost accounting (_estimate_call_cost_usd) runs exactly as in production, and
every call is counted.

Run: python -m app.devtools.smoke_ai_spend_gates
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="508_smoke_ai_gates_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/smoke.db"
os.environ["STORAGE_LOCAL_ROOT"] = str(_TMP / "storage")
os.environ["MATERIALIZED_ROOT"] = str(_TMP / "materialized")
os.environ["MAX_AI_COST_PER_JOB_USD"] = "0.50"
for _key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SEMANTIC_PROVIDER"):
    os.environ.pop(_key, None)

from fastapi.testclient import TestClient  # noqa: E402

_CAP_USD = 0.50
# One call's usage: 50k in / 3,334 out on claude-sonnet-4-6, about $0.20 by the
# app's own price table.
_USAGE = {"model": "claude-sonnet-4-6", "usage": {"input_tokens": 50000, "output_tokens": 3334}}


def _page() -> bytes:
    """Images with nearby text, 'click here' links, tables and no lang: every
    AI-backed executor has work to do."""
    parts = ["<!doctype html><html><head></head><body><h1>Budget</h1>"]
    for i in range(20):
        parts.append(f"<p>Figure {i}: revenue for region {i} across the fiscal year.</p><img src=\"chart{i}.png\">")
    for i in range(20):
        parts.append(f"<p>Details: <a href=\"https://example.com/reports/r{i}.pdf\">click here</a></p>")
    for t in range(4):
        rows = "".join(f"<tr><td>R{t}{r}</td><td>{r * 10}</td></tr>" for r in range(4))
        parts.append(f"<table><tr><th>Region</th><th>Q1</th></tr>{rows}</table>")
    parts.append("</body></html>")
    return "".join(parts).encode("utf-8")


def _install_fake_paid_provider():
    import app.ai.semantic_inference as si

    ledger: list = []  # (kind, usd) per provider call
    built = {"paid_clients": 0}

    class _FakePaidProvider(si.HeuristicProvider):
        name = "fake-paid"

        def _bill(self, kind: str, text: str):
            ledger.append((kind, si._estimate_call_cost_usd(_USAGE)))
            return si.InferenceResult(text=text, confidence=0.9, provider=self.name, raw=json.loads(json.dumps(_USAGE)))

        def alt_text(self, payload):
            return self._bill("alt_text", "Bar chart of quarterly revenue by region")

        def link_text(self, payload):
            return self._bill("link_text", "Download the regional revenue report")

        def table_caption(self, payload):
            return self._bill("table_caption", "Quarterly revenue by region")

        def document_title(self, payload):
            return self._bill("document_title", "Annual Budget Report")

        def document_language(self, payload):
            return self._bill("document_language", "en")

    si.build_default_provider = lambda *a, **k: _FakePaidProvider()

    real_init = si.SemanticInferenceClient.__init__

    def _counting_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        if isinstance(self.provider, _FakePaidProvider):
            built["paid_clients"] += 1

    si.SemanticInferenceClient.__init__ = _counting_init
    return si, ledger, built


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    si, ledger, built = _install_fake_paid_provider()
    one_call = si._estimate_call_cost_usd(_USAGE)
    check("sanity: one fake call is priced like a real Claude call", 0.15 < one_call < 0.25, str(one_call))
    budget = _CAP_USD + one_call + 1e-9  # the cap is checked before a call, so the crossing call can land

    from app.analyzers.registry import run_analyzers
    from app.parsers import parse_to_tree
    from app.services.remediation_planner import RemediationPolicy, plan_remediations
    from app.services.remediators.registry import execute_plans, get_default_executors, get_offline_executors

    # ---- 1. one client per job, shared by every AI-backed executor ----------
    job = [e for e in get_default_executors() if hasattr(e, "_client")]
    check("at least four AI-backed executors exist (sanity)", len(job) >= 4, str(len(job)))
    check("every AI-backed executor in a job shares ONE client (one cost cap)", len({id(e._client) for e in job}) == 1)
    next_job = [e for e in get_default_executors() if hasattr(e, "_client")]
    check(
        "the next job gets its own client (the budget is per job, not per process)",
        {id(e._client) for e in next_job}.isdisjoint({id(e._client) for e in job}),
    )
    before_offline = built["paid_clients"]
    offline = [e for e in get_offline_executors() if hasattr(e, "_client")]
    check(
        "offline executors are all on the heuristic provider",
        bool(offline) and all(type(e._client.provider) is si.HeuristicProvider for e in offline),
    )
    check("building offline executors never constructs a paid client", built["paid_clients"] == before_offline)

    # ---- 2. the cap bounds the WHOLE job (engine level) ---------------------
    src = _TMP / "budget.html"
    src.write_bytes(_page())
    policy = RemediationPolicy(allow_ai_actions=True, require_human_review_for_all=False)

    def run_job() -> tuple:
        res = parse_to_tree(str(src))
        run_analyzers(res.tree)
        start = len(ledger)
        execute_plans(res.tree, plan_remediations(res.tree, policy))
        spent = ledger[start:]
        return len(spent), sum(usd for _, usd in spent), sorted({kind for kind, _ in spent})

    calls, usd, kinds = run_job()
    check("the job really used AI (so the cap check is not vacuous)", calls > 0, str(kinds))
    check(
        f"one job's AI spend stays within the per-job cap plus the crossing call (${usd:.2f})",
        usd <= budget,
        f"calls={calls} kinds={kinds}",
    )
    calls2, usd2, _ = run_job()
    check("a second job starts with a fresh budget", calls2 > 0 and usd2 <= budget, f"calls={calls2} ${usd2:.2f}")

    # ---- 3. free API paths never reach the paid provider --------------------
    from app.main import app

    client = TestClient(app)
    signin = client.post(
        "/auth/sign-in",
        json={"email": "ai-gates-smoke@example.com", "displayName": "AG", "password": "aigatespass1"},
    )
    assert signin.status_code == 200, signin.text
    auth = {"Authorization": f"Bearer {signin.json()['token']}"}
    page = _page()

    def free(label: str, send):
        start_calls, start_clients = len(ledger), built["paid_clients"]
        resp = send()
        check(f"{label}: no paid provider call", len(ledger) == start_calls, f"calls={len(ledger) - start_calls}")
        check(
            f"{label}: no paid inference client constructed",
            built["paid_clients"] == start_clients,
            f"clients={built['paid_clients'] - start_clients}",
        )
        return resp

    images = json.dumps({"title": "", "images": [{"alt_text": ""} for _ in range(10)]})
    r = free("POST /scan", lambda: client.post("/scan", json={"documentId": "d1", "sourceFormat": "pdf", "content": images}, headers=auth))
    check("POST /scan still answers (read-only analysis)", r.status_code == 200, r.text[:200])
    r = free(
        "POST /remediate",
        lambda: client.post("/remediate", json={"targetNodeId": "img-1", "actionCode": "GENERATE_ALT_TEXT"}, headers=auth),
    )
    check("legacy POST /remediate is retired (410), not a free AI proxy", r.status_code == 410, r.text[:200])
    r = free(
        "POST /pipeline/analyze",
        lambda: client.post("/pipeline/analyze", files={"file": ("budget.html", page, "text/html")}, headers=auth),
    )
    check("POST /pipeline/analyze answers", r.status_code == 200, r.text[:200])
    violation_ids = [v["id"] for v in (r.json().get("violations") or [])] if r.status_code == 200 else []
    r = free(
        "POST /pipeline/analyze?execute=true",
        lambda: client.post("/pipeline/analyze?execute=true", files={"file": ("budget.html", page, "text/html")}, headers=auth),
    )
    executions = (r.json().get("executions") or []) if r.status_code == 200 else []
    check("analyze?execute=true still runs the executors (offline)", r.status_code == 200 and len(executions) > 0, r.text[:200])
    check(
        "analyze?execute=true still produces link-text results, from the offline provider",
        any(e.get("actionCode") == "IMPROVE_LINK_TEXT" for e in executions),
        str(sorted({e.get("actionCode") for e in executions})),
    )

    # ---- 4. one PAID remediation stays inside one cap -----------------------
    grant = client.post("/auth/grant-starter", headers=auth)
    check("starter grant for the paid job", grant.status_code == 200, grant.text[:200])
    start = len(ledger)
    r = client.post(
        "/pipeline/remediate",
        files={"file": ("budget.html", page, "text/html")},
        data={"approved_violations": json.dumps(violation_ids)},
        headers=auth,
    )
    spent = sum(usd for _, usd in ledger[start:])
    check("POST /pipeline/remediate succeeds", r.status_code == 200, r.text[:200])
    check("the paid job really used AI", len(ledger) > start)
    check(
        f"one /pipeline/remediate job stays within the per-job AI cap (${spent:.2f})",
        spent <= budget,
        f"calls={len(ledger) - start}",
    )

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
