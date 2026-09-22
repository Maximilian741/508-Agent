"""Smoke: an inline <svg role="img"> is judged like an <img>; other SVGs are not.

The HTML parser skipped every ``<svg>`` outright, so an ``<svg role="img">``
with no name at all produced no finding and the page was reported clean.
Then it over-corrected: ANY SVG that drew <text> was flagged MISSING_ALT_TEXT
(an error) and "fixed" with role="img" + a heuristic label — but a badge
without a role that draws "Fees waived" is already read by a screen reader as
"Fees waived". role="img" makes its children presentational, so the fix hid
those words behind "Image html-img-1 — Fees waived", and charged for it.
Pinned here:

  * only ``role="img"`` makes an SVG an image finding. Without it the drawn
    words are exposed as text, so there is nothing missing; a plain icon (no
    role, no text) could be decorative, so we make no claim; aria-hidden /
    role=presentation SVGs and SVGs inside a link/button are left alone
    (the control's own naming rules cover those);
  * an unnamed role="img" SVG raises MISSING_ALT_TEXT; a named one
    (aria-label, aria-labelledby, a child <title>) does not;
  * the words a role="img" SVG draws (hidden by the role) are offered as the
    grounded caption only when short enough to be a name — a chart's axis
    ticks are not a description;
  * the writer names it with aria-label, the re-parse reads the name back
    (finding cleared), no drawn word is lost, and an SVG nobody approved a
    fix for is left byte-for-byte alone;
  * through the real engine with a provider that returns a description, and
    END TO END with the DEFAULT provider (no AI key): the text badge raises
    nothing and is never touched or charged; the role="img" SVG is named
    with its own words.

Usage:
    python -m app.devtools.smoke_html_inline_svg
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="508_smoke_svg_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/s.db"
os.environ["MATERIALIZED_ROOT"] = str(Path(_TMP) / "materialized")
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)

from lxml import html as lxml_html  # noqa: E402

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.api.pipeline import _action_persists  # noqa: E402
from app.models.accessibility import ImageNode, iter_reading_order  # noqa: E402
from app.parsers.html_parser import HTMLParser, _parse_document  # noqa: E402
from app.writers.html_writer import _visible_word_count, write_remediated_html  # noqa: E402

PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>SVG</title></head><body>
<h1>Permits</h1>
<svg id="badge" width="200" height="60"><rect width="200" height="60" fill="#0a5"/><text x="10" y="35" fill="#fff">Fees waived</text></svg>
<svg id="roletext" role="img" width="200" height="60"><rect width="200" height="60" fill="#0a5"/><text x="10" y="35" fill="#fff">Permit approved</text></svg>
<svg id="roleimg" role="img" width="40" height="40"><circle cx="20" cy="20" r="18"/></svg>
<svg id="icon" width="16" height="16"><path d="M0 0h16v16H0z"/></svg>
<svg id="hidden" role="img" aria-hidden="true"><text x="0" y="10">hidden words</text></svg>
<svg id="pres" role="presentation"><text x="0" y="10">presentational</text></svg>
<a href="/home"><svg id="inlink" role="img"><text x="0" y="10">Home</text></svg></a>
<svg id="named" role="img" aria-label="City seal"><rect width="10" height="10"/></svg>
<svg id="titled" role="img"><title>Opening hours chart</title><text x="0" y="10">9-5</text></svg>
<svg id="notitle"><title>Opening hours</title><text x="0" y="10">9-5</text></svg>
<figure><svg id="fig" role="img"><rect width="10" height="10"/></svg><figcaption>Figure 2: Permit volume by month</figcaption></figure>
<svg id="chart" role="img"><text>0</text><text>10</text><text>20</text><text>30</text><text>Jan</text><text>Feb</text><text>Mar</text><text>Apr</text><text>May</text><text>Jun</text><text>Jul</text><text>Aug</text><text>Sep</text></svg>
<p>Apply for building, event and vendor permits online.</p>
</body></html>"""


def _svg_nodes(tree):
    out = {}
    for n in iter_reading_order(tree.root):
        if isinstance(n, ImageNode) and (n.metadata.properties or {}).get("svg_inline"):
            out[n.metadata.properties["__xpath"]] = n
    return out


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(_TMP)
    src = tmp / "page.html"
    src.write_text(PAGE, encoding="utf-8")
    res = HTMLParser().parse_to_tree(str(src))
    run_analyzers(res.tree)
    doc = _parse_document(src.read_bytes())

    by_id = {}
    for xp, node in _svg_nodes(res.tree).items():
        el = doc.xpath(xp)[0]
        by_id[el.get("id")] = node

    check("only role=img SVGs become image nodes",
          set(by_id) == {"roletext", "roleimg", "named", "titled", "fig", "chart"}, str(sorted(by_id)))
    check("no claim about a text badge without a role (its words are already read), an icon, "
          "aria-hidden, role=presentation, an SVG inside a link, or a role-less titled SVG",
          not ({"badge", "icon", "hidden", "pres", "inlink", "notitle"} & set(by_id)))

    def flags(key):
        return [f.code.value for f in by_id[key].accessibility_flags] if key in by_id else []

    check("role=img SVG drawing 'Permit approved' with no name -> MISSING_ALT_TEXT",
          "MISSING_ALT_TEXT" in flags("roletext"), str(flags("roletext")))
    check("role=img SVG with no name -> MISSING_ALT_TEXT", "MISSING_ALT_TEXT" in flags("roleimg"), str(flags("roleimg")))
    check("aria-label names an SVG (no missing-alt)", by_id["named"].alt_text == "City seal" and "MISSING_ALT_TEXT" not in flags("named"))
    check("a child <title> names an SVG (no missing-alt)", by_id["titled"].alt_text == "Opening hours chart" and "MISSING_ALT_TEXT" not in flags("titled"))
    check("the words the role hides are offered as the caption", (by_id["roletext"].metadata.properties or {}).get("caption") == "Permit approved")
    check("a <figcaption> is the caption for a textless figure SVG",
          (by_id["fig"].metadata.properties or {}).get("caption") == "Figure 2: Permit volume by month")
    check("a chart's pile of axis labels is NOT offered as a caption",
          "caption" not in (by_id["chart"].metadata.properties or {}), str(by_id["chart"].metadata.properties))

    # ---- writer: name exactly the approved SVGs -----------------------------
    by_id["roletext"].alt_text = "Green badge: Permit approved"
    by_id["fig"].alt_text = "Bar chart of permit volume by month"
    out = tmp / "page.out.html"
    rep = write_remediated_html(src, res.tree, out)
    applied = [a for a in rep["applied"] if a.get("action") == "GENERATE_ALT_TEXT"]
    check("writer applied exactly the two approved SVG names", len(applied) == 2, str(rep))
    outdoc = lxml_html.document_fromstring(out.read_bytes().decode("utf-8"))

    def svg(i):
        return outdoc.xpath(f"//svg[@id='{i}']")[0]

    check("role=img text SVG: aria-label written, role kept",
          svg("roletext").get("role") == "img" and svg("roletext").get("aria-label") == "Green badge: Permit approved",
          str(dict(svg("roletext").attrib)))
    check("figure SVG keeps its role and gains the name", svg("fig").get("role") == "img" and svg("fig").get("aria-label") == "Bar chart of permit volume by month")
    for untouched in ("badge", "roleimg", "icon", "hidden", "pres", "inlink", "named", "titled", "notitle", "chart"):
        before = doc.xpath(f"//svg[@id='{untouched}']")[0]
        check(f"unapproved SVG #{untouched} left exactly as it was", dict(svg(untouched).attrib) == dict(before.attrib),
              f"{dict(before.attrib)} -> {dict(svg(untouched).attrib)}")
    check("no drawn word was lost", _visible_word_count(out.read_bytes()) == _visible_word_count(src.read_bytes()))

    again = HTMLParser().parse_to_tree(str(out))
    run_analyzers(again.tree)
    doc2 = _parse_document(out.read_bytes())
    after = {doc2.xpath(xp)[0].get("id"): n for xp, n in _svg_nodes(again.tree).items()}
    check("re-parse reads the written names back",
          after["roletext"].alt_text == "Green badge: Permit approved" and after["fig"].alt_text == "Bar chart of permit volume by month")
    check("re-analysis: the fixed SVGs no longer raise MISSING_ALT_TEXT",
          not any(f.code.value == "MISSING_ALT_TEXT" for k in ("roletext", "fig") for f in after[k].accessibility_flags))
    check("re-analysis: the unfixed role=img SVG still does (nothing was claimed for it)",
          any(f.code.value == "MISSING_ALT_TEXT" for f in after["roleimg"].accessibility_flags))

    # ---- real engine with a provider that returns a grounded description ----
    failures += _engine_path(check, tmp)
    check("GENERATE_ALT_TEXT persists for html (honesty matrix)", _action_persists("GENERATE_ALT_TEXT", "html"))

    # ---- end to end with the DEFAULT provider (no AI key) --------------------
    failures += _default_provider_http(check)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


def _engine_path(check, tmp: Path) -> int:
    from app.ai.semantic_inference import InferenceResult
    from app.services.remediation_planner import RemediationPolicy, plan_remediations
    from app.services.remediators.generate_alt_text_executor import GenerateAltTextExecutor

    class _Grounded:
        """Stands in for a real vision/AI provider: describes from the caption."""

        cost_capped = False

        def suggest_alt_text(self, **kw):
            cap = kw.get("caption") or ""
            return InferenceResult(text=f"Badge reading {cap}" if cap else "", confidence=0.9, provider="stub")

    src = tmp / "engine.html"
    src.write_text(PAGE, encoding="utf-8")
    res = HTMLParser().parse_to_tree(str(src))
    run_analyzers(res.tree)
    policy = RemediationPolicy(allow_ai_actions=True, require_human_review_for_all=False)
    doc = _parse_document(src.read_bytes())
    target_id = next(n.id for xp, n in _svg_nodes(res.tree).items() if doc.xpath(xp)[0].get("id") == "roletext")
    plans = [p for p in plan_remediations(res.tree, policy) if p.target_node_id == target_id]
    check("engine: the role=img text SVG gets an alt-text plan", bool(plans), str([p.flag.code.value for p in plans]))
    if not plans:
        return 0
    result = GenerateAltTextExecutor(client=_Grounded()).execute(plans[0], res.tree)
    check("engine: alt executor succeeds from the SVG's own words", result.status.value == "success", result.notes)
    out = tmp / "engine.out.html"
    rep = write_remediated_html(src, res.tree, out)
    check("engine: the writer confirms it (applied entry for that node)",
          any(a.get("action") == "GENERATE_ALT_TEXT" and a.get("target_id") == target_id for a in rep["applied"]), str(rep))
    check("engine: the bytes carry it", b'aria-label="Badge reading Permit approved"' in out.read_bytes())
    return 0


BADGE_ONLY = (
    b'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Permits</title></head><body>'
    b"<h1>Permits</h1><p>Apply for building, event and vendor permits online.</p>"
    b'<svg id="badge" width="200" height="60"><rect width="200" height="60" fill="#0a5"/>'
    b'<text x="10" y="35" fill="#fff">Fees waived</text></svg></body></html>'
)
BOTH = BADGE_ONLY.replace(
    b"</body>",
    b'<svg id="seal" role="img" width="200" height="60"><rect width="200" height="60"/>'
    b'<text x="10" y="35" fill="#fff">Permit approved</text></svg></body>',
)


def _default_provider_http(check) -> int:
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.db.models import UserRow
    from app.db.session_sqlalchemy import session_scope
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    email = "svg-default-provider@example.com"
    r = client.post("/auth/sign-in", json={"email": email, "displayName": "S", "password": "svgdefault12"})
    if r.status_code != 200:
        check("HTTP: sign-in", False, r.text[:200])
        return 0
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    with session_scope() as s:
        s.execute(select(UserRow).where(UserRow.email == email)).scalars().first().credits_balance = 100

    def balance() -> int:
        return int(client.get("/credits/balance", headers=headers).json()["balance"])

    ar = client.post("/pipeline/analyze", files={"file": ("badge.html", BADGE_ONLY, "text/html")}, headers=headers)
    rules = [v["ruleId"] for v in ar.json().get("violations", [])] if ar.status_code == 200 else None
    check("HTTP default provider: a role-less badge that draws its words raises nothing",
          ar.status_code == 200 and "MISSING_ALT_TEXT" not in (rules or []), f"{ar.status_code} {rules}")

    ar = client.post("/pipeline/analyze", files={"file": ("both.html", BOTH, "text/html")}, headers=headers)
    if ar.status_code != 200:
        check("HTTP default provider: analyze", False, f"{ar.status_code} {ar.text[:200]}")
        return 0
    violations = ar.json()["violations"]
    approved = [v["id"] for v in violations]
    check("HTTP default provider: only the role=img SVG is a finding",
          [v["ruleId"] for v in violations] == ["MISSING_ALT_TEXT"], str([(v["ruleId"], v["id"]) for v in violations]))
    before = balance()
    rr = client.post(
        "/pipeline/remediate",
        files={"file": ("both.html", BOTH, "text/html")},
        data={"approved_violations": json.dumps(approved), "rejected_violations": "[]"},
        headers=headers,
    )
    if rr.status_code != 200:
        check("HTTP default provider: remediate", False, f"{rr.status_code} {rr.text[:300]}")
        return 0
    rj = rr.json()
    charged = before - balance()
    body = client.get(rj["downloadUrl"]).content
    outdoc = lxml_html.document_fromstring(body.decode("utf-8"))
    badge = outdoc.xpath("//svg[@id='badge']")[0]
    seal = outdoc.xpath("//svg[@id='seal']")[0]
    check("HTTP default provider: the role-less badge is untouched (its words stay exposed)",
          badge.get("role") is None and badge.get("aria-label") is None, str(dict(badge.attrib)))
    succeeded = [e for e in rj["executions"] if e.get("status") == "success"]
    if succeeded:
        check("HTTP default provider: the role=img SVG is named with the words it draws",
              "Permit approved" in (seal.get("aria-label") or ""), str(dict(seal.attrib)))
        check("HTTP default provider: charged for exactly that one fix",
              charged > 0 and [a.get("action") for a in rj["writer"]["applied"]] == ["GENERATE_ALT_TEXT"], f"{charged} {rj['writer']}")
    else:
        check("HTTP default provider: a refused name is not written and not charged",
              seal.get("aria-label") is None and charged == 0, f"{charged} {dict(seal.attrib)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
