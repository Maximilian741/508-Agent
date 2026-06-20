"""HTTP-seam smoke for the HTML format through /pipeline/analyze + remediate.

smoke_html proves the parser/writer round-trip at the function level; this
proves the *integration*: the new ``.html`` suffix gate accepts the upload, the
writer is dispatched, credits are charged at the html rate (3), and the download
comes back as ``text/html`` with the approved fixes baked into the bytes.

Mirrors smoke_pipeline_remediate (in-process TestClient, plain asserts).

Usage:
    python -m app.devtools.smoke_html_pipeline
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_SMOKE_DB_DIR = tempfile.mkdtemp(prefix="508_smoke_html_http_")
os.environ["DATABASE_URL"] = f"sqlite:///{_SMOKE_DB_DIR}/smoke.db"

from fastapi.testclient import TestClient  # noqa: E402

HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<h1>Quarterly Report</h1>
<h3>Revenue</h3>
<img src="chart.png">
<p><a href="/full">click here</a></p>
</body>
</html>
"""


def main() -> int:
    from app.main import app
    from app.parsers import parse_to_tree
    from app.models.accessibility import ImageNode, iter_reading_order

    client = TestClient(app)

    signin = client.post(
        "/auth/sign-in",
        json={"email": "html-pipe@example.com", "displayName": "H", "password": "htmlpass12"},
    )
    assert signin.status_code == 200, signin.text
    headers = {"Authorization": f"Bearer {signin.json()['token']}"}
    grant = client.post("/auth/grant-starter", headers=headers)
    assert grant.status_code == 200, grant.text

    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "page.html"
        src.write_text(HTML, encoding="utf-8")

        # ---- analyze (suffix gate must accept .html) ----
        with src.open("rb") as fh:
            ar = client.post(
                "/pipeline/analyze",
                files={"file": ("page.html", fh, "text/html")},
                headers=headers,
            )
        assert ar.status_code == 200, f"analyze failed: {ar.status_code} {ar.text}"
        data = ar.json()
        assert data["summary"]["sourceFormat"] == "html", data["summary"]
        rule_ids = [v["ruleId"] for v in data["violations"]]
        for expected in ("MISSING_ALT_TEXT", "DOCUMENT_TITLE_MISSING", "DOCUMENT_LANGUAGE_MISSING"):
            assert expected in rule_ids, f"expected {expected} in {rule_ids}"
        print(f"[smoke] analyze accepted .html and flagged {sorted(set(rule_ids))}")

        approved = [v["id"] for v in data["violations"]]

        # ---- remediate (writer dispatch + credit charge at html rate) ----
        with src.open("rb") as fh:
            rr = client.post(
                "/pipeline/remediate",
                files={"file": ("page.html", fh, "text/html")},
                data={"approved_violations": json.dumps(approved), "rejected_violations": json.dumps([])},
                headers=headers,
            )
        assert rr.status_code == 200, f"remediate failed: {rr.status_code} {rr.text}"
        rdata = rr.json()
        assert rdata["filename"].endswith(".html"), rdata["filename"]
        applied_actions = {a.get("action") for a in rdata.get("writer", {}).get("applied", [])}
        assert {"SET_DOCUMENT_TITLE", "SET_DOCUMENT_LANGUAGE", "GENERATE_ALT_TEXT",
                "NORMALIZE_HEADING_LEVEL"}.issubset(applied_actions), applied_actions
        print(f"[smoke] remediate produced {rdata['filename']} ({len(rdata['executions'])} executions)")

        # ---- download (content-type + fixes in bytes) ----
        dl = client.get(rdata["downloadUrl"])
        assert dl.status_code == 200, f"download failed: {dl.status_code}"
        ctype = dl.headers.get("content-type", "")
        assert ctype.startswith("text/html"), f"expected text/html, got {ctype!r}"
        out = Path(d) / rdata["filename"]
        out.write_bytes(dl.content)
        text = dl.content.decode("utf-8", "replace")
        assert "lang=" in text, "remediated html must declare a language"
        assert "<title>" in text and text.split("<title>")[1].split("</title>")[0].strip(), "title must be set"
        assert "<h2" in text and "<h3" not in text, "heading jump must be fixed"
        # The approved alt fix must be baked in.
        reparsed = parse_to_tree(str(out))
        imgs = [n for n in iter_reading_order(reparsed.tree.root) if isinstance(n, ImageNode)]
        assert imgs and (imgs[0].alt_text or "").strip(), "alt text must persist into the downloaded html"
        print(f"[smoke] download is text/html with baked alt={imgs[0].alt_text!r}, lang + title + h2 fixed")

        # ---- credit charge: html costs 3 (25 starter -> 22) ----
        me = client.get("/auth/me", headers=headers)
        if me.status_code == 200:
            bal = me.json().get("credits")
            if isinstance(bal, int):
                assert bal == 22, f"expected 22 credits after one html remediation (25-3), got {bal}"
                print(f"[smoke] charged html rate: balance {bal} (25 - 3)")

    print("ok html pipeline smoke passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAIL html pipeline smoke: {exc}")
        sys.exit(1)
    except Exception as exc:  # pragma: no cover
        print(f"FAIL html pipeline smoke: {exc.__class__.__name__}: {exc}")
        sys.exit(1)
