"""Smoke: scan history + regression diff ("what changed since last scan").

The value is telling a returning user "3 NEW since Tuesday, you fixed 5". That
is only honest if an issue can be recognised across scans. Violation ids can't
do it — node ids are ordinal counters, so inserting one paragraph renumbers
everything after it. This pins the content-derived fingerprint instead:

  * the SAME issue keeps its fingerprint when unrelated content is added/removed
  * a genuinely fixed issue disappears from the set
  * a genuinely new issue appears
  * identical-looking issues on one page don't collapse into one

Usage:
    python -m app.devtools.smoke_scan_history
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_hist_')}/s.db")

from pathlib import Path  # noqa: E402

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.db import models as _models  # noqa: F401, E402  (register tables)
from app.db.base import Base  # noqa: E402
from app.db.session_sqlalchemy import ENGINE  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.services.remediation_engine import RemediationEngine  # noqa: E402
from app.services.scan_history import (  # noqa: E402
    diff_fingerprints,
    fingerprint_violations,
    normalize_url_key,
    sanitized_url,
)

# Baseline page: a generic link + an image with no alt.
PAGE_V1 = """<!DOCTYPE html><html lang="en"><head><title>Home</title></head><body>
  <h1>Welcome</h1>
  <a href="/docs">click here</a>
  <img src="a.png">
</body></html>"""

# Same issues, but a paragraph was inserted ABOVE them — every node id after it
# shifts. Fingerprints must NOT change.
PAGE_V2_SHIFTED = """<!DOCTYPE html><html lang="en"><head><title>Home</title></head><body>
  <h1>Welcome</h1>
  <p>A newly added intro paragraph that pushes everything down.</p>
  <p>And another one.</p>
  <a href="/docs">click here</a>
  <img src="a.png">
</body></html>"""

# The link was fixed; the image still has no alt.
PAGE_V3_ONE_FIXED = """<!DOCTYPE html><html lang="en"><head><title>Home</title></head><body>
  <h1>Welcome</h1>
  <a href="/docs">Read the developer documentation</a>
  <img src="a.png">
</body></html>"""

# Link fixed, plus a NEW bad link appears.
PAGE_V4_NEW_ISSUE = """<!DOCTYPE html><html lang="en"><head><title>Home</title></head><body>
  <h1>Welcome</h1>
  <a href="/docs">Read the developer documentation</a>
  <img src="a.png">
  <a href="/pricing">click here</a>
</body></html>"""

# Two identical-looking alt-less images must NOT collapse to one fingerprint.
PAGE_TWINS = """<!DOCTYPE html><html lang="en"><head><title>T</title></head><body>
  <h1>Gallery</h1><img src="a.png"><img src="b.png">
</body></html>"""


def _fps(html: str, tmp: Path, name: str):
    src = tmp / name
    src.write_text(html, encoding="utf-8")
    res = parse_to_tree(str(src))
    run_analyzers(res.tree)
    violations = RemediationEngine().detect_violations(res.tree)
    return fingerprint_violations(violations, res.tree), violations


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    Base.metadata.create_all(bind=ENGINE)
    tmp = Path(tempfile.mkdtemp(prefix="scanhist_"))

    v1, viol1 = _fps(PAGE_V1, tmp, "v1.html")
    check("baseline page produced findings", len(v1) > 0, str(len(v1)))
    check("one fingerprint per finding", len(v1) == len(viol1), f"{len(v1)} vs {len(viol1)}")

    # --- the critical property: node-id churn must NOT look like regressions ---
    v2, _ = _fps(PAGE_V2_SHIFTED, tmp, "v2.html")
    d = diff_fingerprints(v1, v2)
    check("inserting content does NOT invent new issues (no phantom regressions)",
          d["new"] == 0, str(d))
    check("inserting content does NOT falsely resolve issues", d["resolved"] == 0, str(d))
    check("carried-over issues are recognised", d["unchanged"] == len(v1), str(d))

    # --- a genuinely fixed issue disappears ---
    v3, _ = _fps(PAGE_V3_ONE_FIXED, tmp, "v3.html")
    d13 = diff_fingerprints(v1, v3)
    check("fixing the link text is reported as resolved", d13["resolved"] >= 1, str(d13))
    check("fixing one issue invents no new ones", d13["new"] == 0, str(d13))

    # --- a genuinely new issue appears ---
    v4, _ = _fps(PAGE_V4_NEW_ISSUE, tmp, "v4.html")
    d34 = diff_fingerprints(v3, v4)
    check("a newly added bad link is reported as NEW", d34["new"] >= 1, str(d34))
    check("the untouched image issue is not double-counted", d34["resolved"] == 0, str(d34))

    # --- identical-looking issues stay distinct ---
    tw, tw_viol = _fps(PAGE_TWINS, tmp, "twins.html")
    check("two alt-less images -> two distinct fingerprints",
          len(set(tw)) == len(tw_viol), f"{len(set(tw))} unique of {len(tw_viol)}")

    # --- url identity ---
    check("url key ignores the fragment",
          normalize_url_key("https://a.com/p#frag") == normalize_url_key("https://a.com/p"))
    # A query string often selects a genuinely DIFFERENT page (?page=2, ?id=5),
    # so those must not share one history — but the raw query (tokens, emails)
    # must never be stored, so the key carries only a hash of it.
    check("url key DISTINGUISHES pages by query string",
          normalize_url_key("https://a.com/p?page=2") != normalize_url_key("https://a.com/p?page=3"))
    check("url key is stable for the same query",
          normalize_url_key("https://a.com/p?page=2") == normalize_url_key("https://a.com/p?page=2"))
    check("url key never contains the raw query (no token/PII leak)",
          "secret" not in normalize_url_key("https://a.com/p?token=secret123"),
          normalize_url_key("https://a.com/p?token=secret123"))
    check("stored url strips the query entirely",
          sanitized_url("https://a.com/p?token=secret123&email=x@y.z") == "https://a.com/p",
          sanitized_url("https://a.com/p?token=secret123&email=x@y.z"))
    check("url key ignores a trailing slash",
          normalize_url_key("https://a.com/p/") == normalize_url_key("https://a.com/p"))
    check("url key is case-insensitive on host",
          normalize_url_key("https://A.COM/p") == normalize_url_key("https://a.com/p"))
    check("url key separates different paths",
          normalize_url_key("https://a.com/x") != normalize_url_key("https://a.com/y"))
    check("url key separates different hosts",
          normalize_url_key("https://a.com/p") != normalize_url_key("https://b.com/p"))

    # --- round-trip through the DB ---
    from app.services.scan_history import load_previous_scan, save_scan

    class _Score:
        score, grade = 88.0, "B"

    uid = "smoke-user-1"
    url = "https://example.com/page"
    check("no history on first scan", load_previous_scan(uid, url) is None)
    save_scan(uid, url, v1, _Score())
    prev = load_previous_scan(uid, url)
    check("scan is persisted and read back", prev is not None and prev["issueCount"] == len(v1), str(prev))
    check("fingerprints survive the round-trip", prev and prev["fingerprints"] == v1)
    check("history matches the same page again (fragment ignored)",
          load_previous_scan(uid, url + "#section") is not None)
    check("a different query is a DIFFERENT page (its own history)",
          load_previous_scan(uid, url + "?page=7") is None)
    check("history is per-user (no cross-tenant leak)", load_previous_scan("other-user", url) is None)

    # --- adversarial-review regression locks ---------------------------------
    # A layout-only edit (wrapping the page in one <div>) must NOT read as a
    # page full of regressions. Full xpaths are anchored at <html>, so they all
    # change; the fingerprint uses the xpath TAIL for text-less nodes instead.
    WRAPPED = PAGE_V1.replace("<body>", '<body><div class="layout"><div class="inner">').replace(
        "</body>", "</div></div></body>"
    )
    vw, _ = _fps(WRAPPED, tmp, "wrapped.html")
    dw = diff_fingerprints(v1, vw)
    check("wrapping the page in divs does NOT report phantom regressions",
          dw["new"] == 0 and dw["resolved"] == 0, str(dw))

    # A stored set from a DIFFERENT fingerprint version is not comparable —
    # suppress the diff rather than claim everything was fixed and re-broken.
    import json as _json

    from app.db.models import ScanHistoryRow
    from app.db.session_sqlalchemy import session_scope as _scope

    with _scope() as s:
        row = (
            s.query(ScanHistoryRow)
            .filter(ScanHistoryRow.user_id == uid)
            .order_by(ScanHistoryRow.created_at.desc())
            .first()
        )
        row.fingerprints = _json.dumps({"version": "v0-old", "truncated": False, "items": ["abc"]})
    check("a fingerprint-version mismatch suppresses the diff (no false 'all fixed')",
          load_previous_scan(uid, url) is None)

    # Retention: only the most recent scans per (user,url) are kept.
    from app.services.scan_history import _KEEP_PER_URL

    ruser, rurl = "retention-user", "https://example.com/keep"
    for i in range(_KEEP_PER_URL + 6):
        save_scan(ruser, rurl, [f"fp{i}"], _Score())
    with _scope() as s:
        kept = (
            s.query(ScanHistoryRow)
            .filter(ScanHistoryRow.user_id == ruser)
            .count()
        )
    check(f"history is pruned to the most recent {_KEEP_PER_URL} scans (no unbounded growth)",
          kept <= _KEEP_PER_URL, f"kept {kept}")

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
