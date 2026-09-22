"""Smoke: an inline <svg> that is content gets judged like an <img>.

The HTML parser skipped every ``<svg>`` outright, so a badge that DRAWS
"Fees waived" — or an ``<svg role="img">`` with no name at all — produced no
finding and the page was reported clean. Pinned here:

  * an SVG is an image finding only when it is unambiguously content:
    ``role="img"``, or it draws visible <text>. A plain icon (no role, no
    text) could be decorative, so we make no claim; aria-hidden /
    role=presentation SVGs and SVGs inside a link/button are left alone
    (the control's own naming rules cover those);
  * an unnamed content SVG raises MISSING_ALT_TEXT; a named one (aria-label,
    aria-labelledby, a child <title>) does not;
  * the text the SVG draws is offered as the grounded caption only when it is
    short enough to be a name (a chart's axis ticks are not a description);
  * the writer names it with role="img" + aria-label, the re-parse reads the
    name back (finding cleared), no drawn word is lost, and an SVG nobody
    approved a fix for is left byte-for-byte alone;
  * through the real engine with a provider that returns a real description,
    GENERATE_ALT_TEXT succeeds and persists for html.

Usage:
    python -m app.devtools.smoke_html_inline_svg
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_svg_')}/s.db")
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
<svg id="roleimg" role="img" width="40" height="40"><circle cx="20" cy="20" r="18"/></svg>
<svg id="icon" width="16" height="16"><path d="M0 0h16v16H0z"/></svg>
<svg id="hidden" aria-hidden="true"><text x="0" y="10">hidden words</text></svg>
<svg id="pres" role="presentation"><text x="0" y="10">presentational</text></svg>
<a href="/home"><svg id="inlink"><text x="0" y="10">Home</text></svg></a>
<svg id="named" role="img" aria-label="City seal"><rect width="10" height="10"/></svg>
<svg id="titled"><title>Opening hours chart</title><text x="0" y="10">9-5</text></svg>
<figure><svg id="fig" role="img"><rect width="10" height="10"/></svg><figcaption>Figure 2: Permit volume by month</figcaption></figure>
<svg id="chart"><text>0</text><text>10</text><text>20</text><text>30</text><text>Jan</text><text>Feb</text><text>Mar</text><text>Apr</text><text>May</text><text>Jun</text><text>Jul</text><text>Aug</text><text>Sep</text></svg>
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

    tmp = Path(tempfile.mkdtemp(prefix="508_svg_"))
    src = tmp / "page.html"
    src.write_text(PAGE, encoding="utf-8")
    res = HTMLParser().parse_to_tree(str(src))
    run_analyzers(res.tree)
    doc = _parse_document(src.read_bytes())

    by_id = {}
    for xp, node in _svg_nodes(res.tree).items():
        el = doc.xpath(xp)[0]
        by_id[el.get("id")] = node

    check("content SVGs become image nodes: badge, role=img, named, titled, figure, chart",
          set(by_id) == {"badge", "roleimg", "named", "titled", "fig", "chart"}, str(sorted(by_id)))
    check("no claim about a plain icon, aria-hidden, role=presentation, or an SVG inside a link",
          not ({"icon", "hidden", "pres", "inlink"} & set(by_id)))

    def flags(key):
        return [f.code.value for f in by_id[key].accessibility_flags] if key in by_id else []

    check("SVG drawing 'Fees waived' with no name -> MISSING_ALT_TEXT", "MISSING_ALT_TEXT" in flags("badge"), str(flags("badge")))
    check("role=img SVG with no name -> MISSING_ALT_TEXT", "MISSING_ALT_TEXT" in flags("roleimg"), str(flags("roleimg")))
    check("aria-label names an SVG (no missing-alt)", by_id["named"].alt_text == "City seal" and "MISSING_ALT_TEXT" not in flags("named"))
    check("a child <title> names an SVG (no missing-alt)", by_id["titled"].alt_text == "Opening hours chart" and "MISSING_ALT_TEXT" not in flags("titled"))
    check("the drawn words are offered as the caption", (by_id["badge"].metadata.properties or {}).get("caption") == "Fees waived")
    check("a <figcaption> is the caption for a textless figure SVG",
          (by_id["fig"].metadata.properties or {}).get("caption") == "Figure 2: Permit volume by month")
    check("a chart's pile of axis labels is NOT offered as a caption",
          "caption" not in (by_id["chart"].metadata.properties or {}), str(by_id["chart"].metadata.properties))

    # ---- writer: name exactly the approved SVGs -----------------------------
    by_id["badge"].alt_text = "Green badge: Fees waived"
    by_id["fig"].alt_text = "Bar chart of permit volume by month"
    out = tmp / "page.out.html"
    rep = write_remediated_html(src, res.tree, out)
    applied = [a for a in rep["applied"] if a.get("action") == "GENERATE_ALT_TEXT"]
    check("writer applied exactly the two approved SVG names", len(applied) == 2, str(rep))
    outdoc = lxml_html.document_fromstring(out.read_bytes().decode("utf-8"))

    def svg(i):
        return outdoc.xpath(f"//svg[@id='{i}']")[0]

    check("badge: role=img + aria-label written", svg("badge").get("role") == "img" and svg("badge").get("aria-label") == "Green badge: Fees waived",
          str(dict(svg("badge").attrib)))
    check("figure SVG keeps its role and gains the name", svg("fig").get("role") == "img" and svg("fig").get("aria-label") == "Bar chart of permit volume by month")
    for untouched in ("roleimg", "icon", "hidden", "pres", "inlink", "named", "titled", "chart"):
        before = doc.xpath(f"//svg[@id='{untouched}']")[0]
        check(f"unapproved SVG #{untouched} left exactly as it was", dict(svg(untouched).attrib) == dict(before.attrib),
              f"{dict(before.attrib)} -> {dict(svg(untouched).attrib)}")
    check("no drawn word was lost", _visible_word_count(out.read_bytes()) == _visible_word_count(src.read_bytes()))

    again = HTMLParser().parse_to_tree(str(out))
    run_analyzers(again.tree)
    doc2 = _parse_document(out.read_bytes())
    after = {doc2.xpath(xp)[0].get("id"): n for xp, n in _svg_nodes(again.tree).items()}
    check("re-parse reads the written names back",
          after["badge"].alt_text == "Green badge: Fees waived" and after["fig"].alt_text == "Bar chart of permit volume by month")
    check("re-analysis: the fixed SVGs no longer raise MISSING_ALT_TEXT",
          not any(f.code.value == "MISSING_ALT_TEXT" for k in ("badge", "fig") for f in after[k].accessibility_flags))
    check("re-analysis: the unfixed role=img SVG still does (nothing was claimed for it)",
          any(f.code.value == "MISSING_ALT_TEXT" for f in after["roleimg"].accessibility_flags))

    # ---- real engine with a provider that returns a grounded description ----
    failures += _engine_path(check, tmp)
    check("GENERATE_ALT_TEXT persists for html (honesty matrix)", _action_persists("GENERATE_ALT_TEXT", "html"))

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
    badge_id = next(n.id for xp, n in _svg_nodes(res.tree).items() if doc.xpath(xp)[0].get("id") == "badge")
    plans = [p for p in plan_remediations(res.tree, policy) if p.target_node_id == badge_id]
    check("engine: the badge SVG gets an alt-text plan", bool(plans), str([p.flag.code.value for p in plans]))
    if not plans:
        return 0
    result = GenerateAltTextExecutor(client=_Grounded()).execute(plans[0], res.tree)
    check("engine: alt executor succeeds from the SVG's own words", result.status.value == "success", result.notes)
    out = tmp / "engine.out.html"
    rep = write_remediated_html(src, res.tree, out)
    check("engine: the writer confirms it (applied entry for that node)",
          any(a.get("action") == "GENERATE_ALT_TEXT" and a.get("target_id") == badge_id for a in rep["applied"]), str(rep))
    check("engine: the bytes carry it", b'aria-label="Badge reading Fees waived"' in out.read_bytes())
    return 0


if __name__ == "__main__":
    sys.exit(main())
