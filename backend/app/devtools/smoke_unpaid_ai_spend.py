"""Smoke: an approved fix that can't persist never buys a provider call.

Pre-launch audit finding (critical, reproduced): POST /pipeline/remediate ran
the executors for EVERY approved plan, then declined to charge when
``persisted_fixes == 0``. Approving only actions absent from
``_PERSISTED_ACTIONS[fmt]`` — IMPROVE_LINK_TEXT / GENERATE_TABLE_CAPTION on a
PDF, GENERATE_TABLE_CAPTION on a PPTX — therefore bought unlimited paid AI
calls for zero revenue. Measured: 21 identical requests = 63 paid calls =
$12.60 of simulated Claude spend, balance unchanged.

``requires_ai=False`` is NOT the gate: IMPROVE_LINK_TEXT is declared
requires_ai=False and calls ``suggest_link_text`` anyway.

The honesty rule stands — we do not start charging for a fix that never
reaches the output bytes. We stop the SPEND instead: the executor is skipped
and the user is told the fix needs manual remediation. This pins:

  1. approving ONLY non-persisting PDF actions makes ZERO paid calls,
     and still reports charged=false (nothing was fixed, nothing is billed)
  2. the user still sees the item, as a skipped manual-remediation row, so
     the score's pendingManual count is unchanged
  3. repeating the request does not accumulate spend
  4. a genuinely persisting AI action on the SAME format still runs and is
     still charged — the gate does not break paid work

No real provider is used and no API key is set: the default provider is
replaced by a fake "paid" one reporting Claude-shaped token usage, so the
app's own cost accounting runs exactly as in production and every call is
counted.

Usage:
    python -m app.devtools.smoke_unpaid_ai_spend
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="508_smoke_unpaid_ai_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/smoke.db"
os.environ["STORAGE_LOCAL_ROOT"] = str(_TMP / "storage")
os.environ["MATERIALIZED_ROOT"] = str(_TMP / "materialized")
os.environ["MAX_AI_COST_PER_JOB_USD"] = "0.50"
for _key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SEMANTIC_PROVIDER", "SMTP_HOST"):
    os.environ.pop(_key, None)

from fastapi.testclient import TestClient  # noqa: E402

_USAGE = {"model": "claude-sonnet-4-6", "usage": {"input_tokens": 50000, "output_tokens": 3334}}
_CALLS: list = []


def _install_fake_paid_provider():
    """Stand in for ClaudeProvider: record every call, report real usage."""
    import app.ai.semantic_inference as si

    class _FakePaidProvider(si.HeuristicProvider):
        name = "fake-paid"

        def _bill(self, kind: str, text: str):
            _CALLS.append(kind)
            return si.InferenceResult(
                text=text, confidence=0.9, provider=self.name,
                raw=json.loads(json.dumps(_USAGE)),
            )

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


_install_fake_paid_provider()

from app.main import app  # noqa: E402


def _link_pdf(count: int = 12) -> bytes:
    """A PDF whose only AI-fixable defect is non-descriptive link text —
    IMPROVE_LINK_TEXT, which the PDF writer cannot persist."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    y = 60
    for i in range(count):
        url = f"https://example.com/reports/region-{i}.pdf"
        page.insert_text((50, y), url, fontsize=10)
        page.insert_link({"kind": fitz.LINK_URI, "uri": url,
                          "from": fitz.Rect(50, y - 10, 400, y + 4)})
        y += 20
    out = doc.tobytes()
    doc.close()
    return out


def _link_html() -> bytes:
    """HTML with 'click here' links: IMPROVE_LINK_TEXT, which DOES persist.

    (It used to be images with nearby text. Without pixels to look at, alt
    text now comes from the author's caption with no provider call at all, so
    alt is no longer the paid-and-persisting example.)"""
    parts = ["<!doctype html><html><head><title>T</title></head><body lang='en'><h1>Budget</h1>"]
    for i in range(3):
        parts.append(f"<p>Region {i}: <a href='https://example.com/reports/region-{i}.pdf'>click here</a></p>")
    parts.append("</body></html>")
    return "".join(parts).encode("utf-8")


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: object = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, "" if cond else f"  [{extra}]")
        if not cond:
            failures += 1

    client = TestClient(app, headers={"X-Forwarded-For": "10.42.0.1"})
    r = client.post("/auth/sign-in", json={"email": "unpaid-ai@example.com", "password": "unpaidpass1"})
    assert r.status_code == 200, r.text
    hdr = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.post("/auth/grant-starter", headers=hdr).status_code == 200

    def balance() -> int:
        return client.get("/credits/balance", headers=hdr).json()["balance"]

    def remediate(name: str, blob: bytes, approved: list) -> dict:
        resp = client.post(
            "/pipeline/remediate", headers=hdr,
            files={"file": (name, blob, "application/octet-stream")},
            data={"approved_violations": json.dumps(approved)},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    # --- 1. PDF: approve ONLY the non-persisting AI action --------------------
    pdf = _link_pdf()
    analyzed = client.post(
        "/pipeline/analyze", headers=hdr,
        files={"file": ("links.pdf", pdf, "application/pdf")},
    )
    assert analyzed.status_code == 200, analyzed.text
    violations = analyzed.json()["violations"]
    link_ids = [v["id"] for v in violations if v["ruleId"] == "LINK_TEXT_NON_DESCRIPTIVE"]
    check("the PDF really does flag non-descriptive links (sanity)", len(link_ids) >= 5, len(link_ids))

    from app.api.pipeline import _action_persists

    check(
        "IMPROVE_LINK_TEXT genuinely cannot persist into a PDF (sanity)",
        not _action_persists("IMPROVE_LINK_TEXT", "pdf"),
    )

    before = balance()
    _CALLS.clear()
    body = remediate("links.pdf", pdf, link_ids)
    first_calls = list(_CALLS)
    check("no paid AI call for a fix that cannot reach the PDF bytes", not first_calls, first_calls)
    check("the run is still NOT charged (honesty rule intact)", body.get("charged") is False, body.get("charged"))
    check("persistedFixes stays 0", int(body.get("persistedFixes") or 0) == 0, body.get("persistedFixes"))
    check("no credits were spent", balance() == before, (before, balance()))

    # --- 2. the user still sees the item, as manual remediation ---------------
    execs = body.get("executions") or []
    link_execs = [e for e in execs if e.get("actionCode") == "IMPROVE_LINK_TEXT"]
    check("the approved links are still reported back", len(link_execs) == len(link_ids), len(link_execs))
    check(
        "they are reported as skipped, not as a success we can't deliver",
        all(e.get("status") == "skipped" for e in link_execs),
        [e.get("status") for e in link_execs],
    )
    check(
        "the note tells the user it needs manual remediation",
        all("manual remediation" in (e.get("notes") or "") for e in link_execs),
        [e.get("notes") for e in link_execs][:1],
    )
    check(
        "none of them is claimed as a success (nothing reached the bytes)",
        not any(e.get("status") == "success" for e in link_execs),
        [e.get("status") for e in link_execs],
    )

    # --- 3. repeating the request accumulates no spend ------------------------
    _CALLS.clear()
    for _ in range(3):
        remediate("links.pdf", pdf, link_ids)
    check("3 more identical requests still spend nothing", not _CALLS, _CALLS)
    check("and still cost the user nothing", balance() == before, (before, balance()))

    # --- 4. a PERSISTING AI action on the same account still runs -------------
    html = _link_html()
    analyzed = client.post(
        "/pipeline/analyze", headers=hdr,
        files={"file": ("page.html", html, "text/html")},
    )
    assert analyzed.status_code == 200, analyzed.text
    alt_ids = [v["id"] for v in analyzed.json()["violations"] if v["ruleId"] == "LINK_TEXT_NON_DESCRIPTIVE"]
    check("the HTML really does flag generic link text (sanity)", len(alt_ids) >= 2, len(alt_ids))
    check("IMPROVE_LINK_TEXT genuinely persists into HTML (sanity)", _action_persists("IMPROVE_LINK_TEXT", "html"))

    before = balance()
    _CALLS.clear()
    body = remediate("page.html", html, alt_ids)
    check("a fix that DOES persist still reaches the paid provider", bool(_CALLS), _CALLS)
    check("and it is charged", body.get("charged") is True, body.get("charged"))
    check("and the user's balance actually moves", balance() < before, (before, balance()))

    print()
    print("FAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
