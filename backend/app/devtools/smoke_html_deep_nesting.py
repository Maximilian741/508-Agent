"""Smoke: a deeply nested HTML page is analyzed all the way down.

The HTML tree builder recursed two Python frames per DOM level, so a page
nested ~450 elements deep (legacy <font>/<div> soup, page builders, or any
public URL handed to /scan-url) hit RecursionError and the parser threw the
whole structure away: the report listed only "no title / no language" for a
page full of unlabeled images and skipped headings — presented as nearly
clean. Pinned here:

  * the walk is iterative: 700- and 3000-deep wrapper chains still yield the
    heading jump, the missing alt, the vague link and the header-less table;
  * wrapper <div>s past depth 64 are spliced instead of nested, so the TREE
    stays shallow for the analyzers that walk it;
  * hundreds of nested lists/tables no longer crash the nested-table
    analyzer (it recursed too);
  * the writer can still reach an element whose getpath() locator libxml2's
    XPath engine refuses (a cell ~1000 levels deep), so the fix lands;
  * a hostile page nesting thousands of TABLES is bounded (a locator budget)
    and says so with ANALYSIS_TRUNCATED instead of taking a minute — the
    report never looks complete when it is not;
  * node ids are minted in the same order as the recursive builder did.

Usage:
    python -m app.devtools.smoke_html_deep_nesting
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_deep_')}/s.db")

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.models.accessibility import TableCellNode, TableCellType, iter_reading_order  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.writers.html_writer import _visible_word_count, write_remediated_html  # noqa: E402

LEAF = (
    "<h1>Deep page</h1><h3>Skipped level</h3><img src='photo.png'>"
    "<p>For details <a href='/details'>click here</a></p>"
    "<table><tr><td>Name</td><td>Qty</td></tr><tr><td>Bolts</td><td>40</td></tr><tr><td>Nuts</td><td>12</td></tr></table>"
)
WANT = {"HEADING_LEVEL_JUMP", "MISSING_ALT_TEXT", "LINK_TEXT_NON_DESCRIPTIVE", "TABLE_MISSING_HEADERS"}


def _rules(tree) -> dict:
    out: dict = {}
    for n in iter_reading_order(tree.root):
        for f in n.accessibility_flags:
            out[f.code.value] = out.get(f.code.value, 0) + 1
    return out


def _depth(tree) -> int:
    best = 0
    stack = [(tree.root, 0)]
    while stack:
        node, d = stack.pop()
        best = max(best, d)
        stack.extend((c, d + 1) for c in node.children)
    return best


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="508_deep_"))

    def page(name: str, body: str) -> Path:
        p = tmp / f"{name}.html"
        p.write_text(f"<html lang='en'><head><title>T</title></head><body>{body}</body></html>", encoding="utf-8")
        return p

    # ---- 1. deep wrapper chains are analyzed to the bottom -----------------
    for kind, depth in (("div", 700), ("div", 3000), ("span", 3000), ("font", 1500)):
        if kind == "font":  # the classic unclosed legacy chain
            body = "<font size=2>" * depth + LEAF
        else:
            body = f"<{kind}>" * depth + LEAF + f"</{kind}>" * depth
        p = page(f"{kind}{depth}", body)
        t = time.monotonic()
        res = parse_to_tree(str(p))
        run_analyzers(res.tree)
        dt = time.monotonic() - t
        rules = _rules(res.tree)
        check(f"{depth}-deep <{kind}> chain: every finding below it is still reported", WANT <= set(rules), str(rules))
        check(f"{depth}-deep <{kind}> chain: not flagged as truncated", "ANALYSIS_TRUNCATED" not in rules, str(rules))
        check(f"{depth}-deep <{kind}> chain: tree stays shallow (wrappers spliced past 64)", _depth(res.tree) <= 80, str(_depth(res.tree)))
        check(f"{depth}-deep <{kind}> chain: parse+analyze under 3s", dt < 3.0, f"{dt:.2f}s")

    # ---- 2. deep semantic nesting no longer crashes an analyzer -------------
    p = page("lists600", "<ul><li>item " * 600 + LEAF + "</li></ul>" * 600)
    res = parse_to_tree(str(p))
    try:
        run_analyzers(res.tree)
        ok = True
    except RecursionError:
        ok = False
    check("600 nested lists: analyzers run (no RecursionError)", ok)
    check("600 nested lists: findings at the bottom are reported", ok and WANT <= set(_rules(res.tree)), str(_rules(res.tree)) if ok else "")

    p = page("tables300", "<table><tr><td>" * 300 + LEAF + "</td></tr></table>" * 300)
    res = parse_to_tree(str(p))
    try:
        run_analyzers(res.tree)
        rules = _rules(res.tree)
        ok = True
    except RecursionError:
        rules, ok = {}, False
    check("300 nested tables: nested-table analyzer runs (it used to recurse)", ok)
    check("300 nested tables: every inner table is flagged TABLE_NESTED once", rules.get("TABLE_NESTED") == 300, str(rules))

    # ---- 3. the writer reaches a cell libxml2's XPath refuses ---------------
    p = page("deepcell", "<div>" * 1200 + LEAF + "</div>" * 1200)
    res = parse_to_tree(str(p))
    cells = [n for n in iter_reading_order(res.tree.root) if isinstance(n, TableCellNode)]
    first_row = cells[:2]
    for c in first_row:
        c.cell_type = TableCellType.HEADER  # what an approved ADD_TABLE_HEADERS does
    out = tmp / "deepcell.out.html"
    rep = write_remediated_html(p, res.tree, out)
    promoted = [a for a in rep["applied"] if a.get("action") == "ADD_TABLE_HEADERS"]
    check("1200-deep table: both header cells were written (locator resolved)", len(promoted) == 2, str(rep))
    check("1200-deep table: no element_not_resolved skips", not any(s.get("reason") == "element_not_resolved" for s in rep["skipped"]), str(rep["skipped"][:3]))
    body = out.read_bytes()
    check("1200-deep table: output carries <th scope=col>", body.count(b'<th scope="col">') == 2)
    check("1200-deep table: no word lost", _visible_word_count(body) == _visible_word_count(p.read_bytes()))

    # ---- 4. a hostile table bomb is bounded and says so ---------------------
    p = page("tablebomb", "<table><tr><td>" * 3000 + LEAF + "</td></tr></table>" * 3000)
    t = time.monotonic()
    res = parse_to_tree(str(p))
    run_analyzers(res.tree)
    dt = time.monotonic() - t
    rules = _rules(res.tree)
    check("3000 nested tables: bounded (under 6s, was ~52s)", dt < 6.0, f"{dt:.2f}s")
    check("3000 nested tables: ANALYSIS_TRUNCATED tells the user the report is partial", rules.get("ANALYSIS_TRUNCATED") == 1, str(rules))
    t = time.monotonic()
    rep = write_remediated_html(p, res.tree, tmp / "tablebomb.out.html")
    check("3000 nested tables: the writer is bounded too (under 6s)", time.monotonic() - t < 6.0)
    check("3000 nested tables: output keeps every word", _visible_word_count((tmp / "tablebomb.out.html").read_bytes()) == _visible_word_count(p.read_bytes()))

    # ---- 5. ids are minted in the recursive builder's order -----------------
    p = page("ids", "<h2>A</h2><section><p>one</p><ul><li>x<a href='/y'>y</a></li></ul>"
                    "<table><tr><th>h</th></tr><tr><td>d</td></tr></table></section><img src='z.png'>")
    res = parse_to_tree(str(p))
    ids = [n.id for n in iter_reading_order(res.tree.root)][1:]
    expected = ["html-h-1", "html-section-1", "html-p-1", "html-list-1", "html-li-1", "html-link-1",
                "html-table-1", "html-row-1", "html-cell-1", "html-row-2", "html-cell-2", "html-img-1"]
    check("node ids and order are exactly what the recursive builder produced", ids == expected, str(ids))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
