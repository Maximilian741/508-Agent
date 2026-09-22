"""Smoke: long and deep HTML pages analyse and fix in seconds, not minutes.

Measured before the fix, on one worker thread (and /scan-url fetches any
public page, so this is also a denial-of-service surface):
  * 200,000 sibling paragraphs: 400 s to parse. Every node's locator came
    from lxml's getpath(), and libxml2 finds each step's [n] by counting the
    element's same-name siblings — O(N^2) for N siblings (a long data table
    or a big CMS export);
  * a 50,000-deep chain of <span>s: 11 s to parse, 34 s to write. Iterating
    lxml lazily released each element proxy immediately, and every release
    walks from that node up to the nearest node that still has a proxy — the
    document, in a deep tree — so every full pass was O(depth^2);
  * the writer resolved each locator with doc.xpath(), which re-scans a
    parent's children for each positional step (O(N) per cell of a long
    table).
Pinned: the fast locator is IDENTICAL to lxml's getpath() for every element
of a mixed page (so the writer finds the same elements), the writer's index
resolves exactly what doc.xpath() does, and the pathological pages finish
under generous bounds with their findings intact.

Usage:
    python -m app.devtools.smoke_html_wide_pages
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_wide_')}/s.db")
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.models.accessibility import ImageNode, TableNode, iter_reading_order  # noqa: E402
from app.parsers.html_parser import HTMLParser, _parse_document, _PathBudget  # noqa: E402
from app.writers.html_writer import _PathIndex, write_remediated_html  # noqa: E402

MIXED = """<!DOCTYPE html><!-- saved from url --><html lang="en"><head><title>t</title></head><body>
<div><p>a</p><p>b</p><!-- c --><p>c<span>x</span><span>y</span></p><o:p>word</o:p><o:p>two</o:p></div>
<div><table><thead><tr><th>h</th><th>i</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr>
<tr><td>3</td><td><ul><li>n</li><li>m<ol><li>deep</li></ol></li></ul></td></tr></tbody></table></div>
<section><svg xmlns="http://www.w3.org/2000/svg"><text>s</text><g><text>t</text></g></svg><img src=a.png></section>
<p>tail</p><?php echo 1; ?><p>last</p></body></html>"""


def _flags(tree):
    out = {}
    for n in iter_reading_order(tree.root):
        for f in n.accessibility_flags or []:
            out[f.code.value] = out.get(f.code.value, 0) + 1
    return out


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    # ---- 1. the fast locator is exactly lxml's -----------------------------
    doc = _parse_document(MIXED.encode("utf-8"))
    tree = doc.getroottree()
    fast = _PathBudget(tree)
    els = [e for e in doc.iter() if isinstance(e.tag, str)]
    mismatches = [(tree.getpath(e), fast.getpath(e)) for e in els if tree.getpath(e) != fast.getpath(e)]
    check(f"fast getpath == lxml getpath for all {len(els)} elements of a mixed page", not mismatches, str(mismatches[:3]))
    idx = _PathIndex(doc)
    wrong = []
    for e in els:
        path = tree.getpath(e)
        if idx.resolve(path) is not e:
            wrong.append(path)
    check("the writer's index resolves every locator to the same element doc.xpath() does", not wrong, str(wrong[:3]))
    check("an index-less step that is no longer unique resolves to nothing (ambiguous)",
          idx.resolve("/html/body/div") is None and len(doc.xpath("/html/body/div")) == 2)
    idx.release()

    tmp = Path(tempfile.mkdtemp(prefix="508_wide_"))

    def run(name: str, html: str, bound: float):
        p = tmp / f"{name}.html"
        p.write_text(html, encoding="ascii")
        t = time.monotonic()
        res = HTMLParser().parse_to_tree(str(p))
        run_analyzers(res.tree)
        for n in iter_reading_order(res.tree.root):
            if isinstance(n, ImageNode):
                n.alt_text = "Chart of permits"
        rep = write_remediated_html(p, res.tree, tmp / f"{name}.out.html")
        dt = time.monotonic() - t
        check(f"{name}: analyse + fix in under {bound:.0f}s", dt < bound, f"{dt:.1f}s")
        return res, rep

    # ---- 2. 100,000 siblings ------------------------------------------------
    res, rep = run("flat", "<html><body>" + "<p>word</p>" * 100000 + "<img src=d.png></body></html>", 30.0)
    check("flat: the image at the very end is still found and fixed",
          _flags(res.tree).get("MISSING_ALT_TEXT") == 1 and any(a.get("action") == "GENERATE_ALT_TEXT" for a in rep["applied"]), str(rep["applied"][:3]))

    # ---- 3. a 20,000-row table ---------------------------------------------
    rows = "".join(f"<tr><td>Permit {i}</td><td>{i}</td><td>open</td></tr>" for i in range(20000))
    res, rep = run("table", "<html><body><table><tr><th>Name</th><th>No.</th><th>State</th></tr>" + rows + "</table></body></html>", 30.0)
    tables = [n for n in iter_reading_order(res.tree.root) if isinstance(n, TableNode)]
    check("table: all 20,001 rows are in the tree", len(tables) == 1 and len(tables[0].children) == 20001, str(len(tables[0].children) if tables else 0))

    # ---- 4. 50,000-deep inline chain ---------------------------------------
    res, rep = run("deep_spans", "<html><body><p>" + "<span>w " * 50000 + "</span>" * 50000 + "</p><img src=c.png></body></html>", 10.0)
    check("deep spans: the image after the chain is found and fixed", any(a.get("action") == "GENERATE_ALT_TEXT" for a in rep["applied"]))

    # ---- 5. 30,000-deep div chain ------------------------------------------
    res, rep = run("deep_divs", "<html><body>" + "<div>" * 30000 + "<img src=a.png><a href=/x>click here</a>" + "</div>" * 30000 + "</body></html>", 10.0)
    check("deep divs: the image and the link at the bottom are reported",
          {"MISSING_ALT_TEXT", "LINK_TEXT_NON_DESCRIPTIVE"} <= set(_flags(res.tree)), str(_flags(res.tree)))
    check("deep divs: the image fix reached the file", any(a.get("action") == "GENERATE_ALT_TEXT" for a in rep["applied"]), str(rep))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
