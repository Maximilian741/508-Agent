"""Smoke: an HTML image finding says WHICH image, in the author's own words.

An image has no text to quote, so a finding on it could only say "an image
is missing alt text" — on a page with forty images the person could not tell
which. For the shared location contract (``location.snippet``) the parser
records, on every image node (``<img>`` and content ``<svg>``):
  * ``snippet`` — the start tag as written (attribute order kept; the HTML
    parser lower-cases attribute NAMES, which HTML ignores anyway), long values
    (a data: URI) elided, at most 200 characters;
  * ``line`` — the 1-based source line, unaffected by an XHTML declaration
    that the parser strips before handing the text to lxml.

Usage:
    python -m app.devtools.smoke_html_locations
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_hloc_')}/s.db")

from app.models.accessibility import ImageNode, iter_reading_order  # noqa: E402
from app.parsers.html_parser import HTMLParser  # noqa: E402

PAGE = """<!DOCTYPE html>
<html lang="en"><head><title>Permits</title></head>
<body>
<h1>Permits</h1>
<img src="charts/q3.png" width="400" class="chart">
<p>Some words here.</p>
<img src="data:image/png;base64,{b64}">
<svg viewBox="0 0 10 10"><text>Fees waived</text></svg>
</body></html>
""".replace("{b64}", "iVBORw0KGgo" + "A" * 400)

XHTML = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>x</title></head>
<body>
<img src="logo.png" />
</body></html>
"""


def _images(path: Path):
    res = HTMLParser().parse_to_tree(str(path))
    return [n for n in iter_reading_order(res.tree.root) if isinstance(n, ImageNode)]


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="508_hloc_"))
    page = tmp / "page.html"
    page.write_text(PAGE, encoding="utf-8")
    imgs = _images(page)
    props = [n.metadata.properties or {} for n in imgs]
    check("three image nodes (two <img>, one drawn-text <svg>)", len(imgs) == 3, str(len(imgs)))
    check("the chart's snippet is its start tag, attributes in the author's order",
          props[0].get("snippet") == '<img src="charts/q3.png" width="400" class="chart">', repr(props[0].get("snippet")))
    check("the chart is on line 5", props[0].get("line") == 5, str(props[0].get("line")))
    check("a data: URI is elided, and the snippet stays under 200 characters",
          props[1].get("snippet", "").startswith('<img src="data:image/png;base64,iVBOR')
          and "…" in props[1].get("snippet", "") and len(props[1]["snippet"]) <= 200, repr(props[1].get("snippet")))
    check("the SVG badge says which SVG, and where", props[2].get("snippet", "").lower() == '<svg viewbox="0 0 10 10">' and props[2].get("line") == 8,
          f"{props[2].get('snippet')!r} line {props[2].get('line')}")

    x = tmp / "x.html"
    x.write_text(XHTML, encoding="utf-8")
    ximgs = _images(x)
    check("XHTML: the stripped XML declaration does not shift line numbers",
          len(ximgs) == 1 and (ximgs[0].metadata.properties or {}).get("line") == 5,
          str([(n.metadata.properties or {}).get("line") for n in ximgs]))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
