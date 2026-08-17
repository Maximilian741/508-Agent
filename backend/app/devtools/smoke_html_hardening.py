"""Smoke: HTML parser is safe on hostile depth, and the writer never loses words.

Three findings from a stress audit, all on the HTML path — the one format
where the input is a PUBLIC URL (/scan-url), so "pathological" means "any
page on the internet", not "a malicious upload":

  1. EXPONENTIAL PARSE. A section-tag wrapper (<div>, <section>, ...) whose
     subtree produced no node returned None, and the caller then walked that
     same subtree AGAIN — work(k) = 2 * work(k-1). A 684-byte page with 23
     nested <div> took 17s, doubling per level; ~26 deep pinned a request
     thread forever. Now the empty child list is returned and extended once.

  2. SILENT TRUNCATION AT DEPTH 256. libxml2's default parser clamps depth
     at 256 and DROPS everything deeper with no error; the writer serialized
     the truncated tree as the "fixed" file. Legacy pages with unclosed
     <font>/<div> chains reach 256 easily. Now huge_tree=True.

  3. NO CONTENT-LOSS GATE. The PDF tagger refuses to ship an output whose
     op count changed; the HTML writer had no equivalent. It now counts
     visible WORDS in source vs output and refuses (no charge) if any were
     lost. Words, not characters: fake-list conversion legitimately strips a
     typed "- " marker, and that must pass.

Usage:
    python -m app.devtools.smoke_html_hardening
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_hh_')}/s.db")

from pathlib import Path  # noqa: E402

from app.parsers import parse_to_tree  # noqa: E402
from app.writers.html_writer import _visible_word_count  # noqa: E402


def _wrap_page(depth: int, leaf: str = "<span>leaf text only</span>") -> str:
    return (
        "<html><head><title>W</title></head><body><h1>Real heading</h1><p>Real paragraph.</p>"
        + '<div class="wrap">' * depth + leaf + "</div>" * depth + "</body></html>"
    )


def _count_text_nodes(tree) -> int:
    from app.models.accessibility import iter_reading_order

    return sum(1 for n in iter_reading_order(tree.root) if getattr(n.content, "text", None))


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="508_hh_"))

    # ---- 1. exponential is gone --------------------------------------------
    # 23 deep took 17s before; 40 deep would have taken ~37 minutes.
    for depth, budget in ((23, 1.0), (40, 1.0), (400, 2.0)):
        p = tmp / f"wrap{depth}.html"
        p.write_text(_wrap_page(depth), encoding="utf-8")
        t = time.monotonic()
        parse_to_tree(str(p))
        dt = time.monotonic() - t
        check(f"{depth}-deep node-less wrapper chain parses in <{budget}s", dt < budget, f"{dt:.2f}s")

    # ---- 2. content past depth 256 survives ---------------------------------
    # Put REAL content (a heading with words) at depth 300. With the default
    # libxml2 clamp it vanished silently; the tree must still contain it.
    deep_leaf = "<h2>Deep heading survives the parse</h2><p>and so does this paragraph</p>"
    p = tmp / "deep300.html"
    p.write_text(_wrap_page(300, deep_leaf), encoding="utf-8")
    res = parse_to_tree(str(p))
    texts = []
    from app.models.accessibility import iter_reading_order

    for n in iter_reading_order(res.tree.root):
        t = getattr(n.content, "text", None)
        if t:
            texts.append(t)
    check("content nested 300 deep is NOT silently dropped by the parser",
          any("Deep heading survives" in t for t in texts), str(texts[:6]))

    # ---- 3. the loss gate: word count semantics -----------------------------
    src = b"<html><body><p>- alpha beta</p><p>- gamma</p><p>1. delta epsilon</p></body></html>"
    listified = b"<html><body><ul><li>alpha beta</li><li>gamma</li></ul><ol><li>delta epsilon</li></ol></body></html>"
    dropped = b"<html><body><p>- alpha beta</p><p>1. delta epsilon</p></body></html>"
    check("word count ignores typed list markers (source)", _visible_word_count(src) == 5, str(_visible_word_count(src)))
    check("fake-list conversion loses NO words (must pass the gate)",
          _visible_word_count(listified) == _visible_word_count(src),
          f"{_visible_word_count(listified)} vs {_visible_word_count(src)}")
    check("a dropped paragraph DOES lose words (must trip the gate)",
          _visible_word_count(dropped) < _visible_word_count(src),
          f"{_visible_word_count(dropped)} vs {_visible_word_count(src)}")
    check("script/style text is not counted as visible",
          _visible_word_count(b"<html><body><p>one two</p><script>var x = 1 2 3;</script><style>a b c</style></body></html>") == 2)
    check("unmeasurable output fails CLOSED (0 words trips the gate against any real source)",
          _visible_word_count(b"") == 0)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
