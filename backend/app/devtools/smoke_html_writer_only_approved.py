"""Smoke: the HTML writer changes and reports ONLY what an executor changed.

The writer decides "was this fixed?" by comparing the tree with the page. It
parses the page byte-preserving (a NUL or an invalid byte kept as a private
stand-in, so it goes back out as the same byte), but the tree holds what the
ANALYSIS read: U+FFFD for the invalid byte, nothing for the NUL. So every
title, link, alt and SVG name holding one compared "different": a common
mixed-encoding CMS page (declared UTF-8, a windows-1252 "é" in the title)
got its title, link text, existing alt and SVG name rewritten with the byte
destroyed, all reported as applied — "We applied 3 fixes" for one approved
alt. The same happened to any title / alt / lang with surrounding spaces
(the parser stores them stripped).

Pinned here:
  * HTTP: approve only the missing alt on that page -> exactly one fix
    applied and charged; the title, the link, the existing alt and the named
    SVG keep their original bytes (0xE9, never EF BF BD);
  * the same with a NUL in the title and a link;
  * surrounding spaces in a title / alt / lang are not a "fix";
  * an executor's REAL change on such a page is still written and reported.

Usage:
    python -m app.devtools.smoke_html_writer_only_approved
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="508_smoke_wonly_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/s.db"
os.environ["MATERIALIZED_ROOT"] = str(Path(_TMP) / "materialized")
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)

from app.models.accessibility import ImageNode, LinkNode, iter_reading_order  # noqa: E402
from app.parsers.html_parser import HTMLParser  # noqa: E402
from app.writers.html_writer import write_remediated_html  # noqa: E402

TMP = Path(_TMP)

CAFE = (
    b'<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><title>Caf\xe9 du Monde</title></head><body>'
    b"<p>Bienvenue au caf\xc3\xa9, notre menu est ci-dessous pour tous les clients du quartier.</p>"
    b'<a href="/menu">Menu du caf\xe9</a> <a href="/about">Notre histoire</a>'
    b'<img src="terrasse.png" alt="Terrasse du caf\xe9">'
    b'<svg width="40" height="40"><title>Logo du caf\xe9</title><text x="1" y="20">Caf\xe9</text></svg>'
    b"<figure><img src=\"x.png\"><figcaption>Photo de la terrasse du caf\xc3\xa9 au printemps</figcaption></figure>"
    b"</body></html>"
)
UNTOUCHED = (
    b"<title>Caf\xe9 du Monde</title>",
    b'<a href="/menu">Menu du caf\xe9</a>',
    b'alt="Terrasse du caf\xe9"',
    b"<title>Logo du caf\xe9</title>",
)
NUL = (
    b'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Annual\x00 report</title></head><body>'
    b"<p>The annual report covers every region and every product line this year.</p>"
    b'<a href="/r">Download\x00 the report</a><img src="x.png"></body></html>'
)
SPACES = (
    b'<!DOCTYPE html><html lang=" en "><head><meta charset="utf-8"><title>  Budget 2026  </title></head><body>'
    b"<p>The budget funds every service the county runs next year.</p>"
    b'<img src="a.png" alt=" Pie chart of spending by department "><img src="b.png"></body></html>'
)


def _writer_roundtrip(name: str, data: bytes, mutate=None):
    src = TMP / f"{name}.html"
    src.write_bytes(data)
    res = HTMLParser().parse_to_tree(str(src))
    approved = []
    for n in iter_reading_order(res.tree.root):
        if isinstance(n, ImageNode) and not n.is_decorative and not n.alt_text:
            n.alt_text = "Photo of the cafe terrace"
            approved.append(("GENERATE_ALT_TEXT", n.id))
    if mutate:
        approved += mutate(res.tree)
    out = TMP / f"{name}.out.html"
    rep = write_remediated_html(src, res.tree, out)
    applied = sorted((a.get("action"), a.get("target_id")) for a in rep["applied"])
    return sorted(approved), applied, out.read_bytes()


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    # ---- writer level ------------------------------------------------------
    approved, applied, out = _writer_roundtrip("cafe", CAFE)
    check("mixed bytes: applied == approved (one alt)", applied == approved, f"{applied} vs {approved}")
    for frag in UNTOUCHED:
        check(f"mixed bytes: {frag[:30]!r}... kept byte for byte", frag in out)
    check("mixed bytes: no U+FFFD written anywhere", b"\xef\xbf\xbd" not in out)
    check("mixed bytes: the named SVG was not re-named", b"aria-label" not in out and b'role="img"' not in out)

    approved, applied, out = _writer_roundtrip("nul", NUL)
    check("NUL: applied == approved (one alt)", applied == approved, f"{applied} vs {approved}")
    check("NUL: title and link keep their NUL", b"<title>Annual\x00 report</title>" in out and b"Download\x00 the report</a>" in out)

    approved, applied, out = _writer_roundtrip("spaces", SPACES)
    check("spaces: applied == approved (one alt)", applied == approved, f"{applied} vs {approved}")
    check("spaces: title, lang and the existing alt are left as written",
          b"<title>  Budget 2026  </title>" in out and b'lang=" en "' in out
          and b'alt=" Pie chart of spending by department "' in out, repr(out[:200]))

    def change_link(tree):
        for n in iter_reading_order(tree.root):
            if isinstance(n, LinkNode) and n.content.text == "Notre histoire":
                n.content.text = "Notre histoire depuis 1920"
                return [("IMPROVE_LINK_TEXT", n.id)]
        return []

    approved, applied, out = _writer_roundtrip("cafe_real_change", CAFE, change_link)
    check("a real link-text change on a mixed-bytes page is written and reported",
          applied == approved and b'<a href="/about">Notre histoire depuis 1920</a>' in out, f"{applied} vs {approved}")
    check("... and the other bytes still survive", all(frag in out for frag in UNTOUCHED))

    def set_title(tree):
        tree.root.metadata.properties["title"] = "Café du Monde — Menu"
        return [("SET_DOCUMENT_TITLE", tree.root.id)]

    approved, applied, out = _writer_roundtrip("cafe_title", CAFE, set_title)
    check("a real title change is written and reported",
          applied == approved and "<title>Café du Monde — Menu</title>".encode("utf-8") in out, f"{applied}")

    failures += _http_path(check)
    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


def _http_path(check) -> int:
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.db.models import UserRow
    from app.db.session_sqlalchemy import session_scope
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    email = "writer-only-approved@example.com"
    r = client.post("/auth/sign-in", json={"email": email, "displayName": "W", "password": "onlyapproved9"})
    if r.status_code != 200:
        check("HTTP: sign-in", False, r.text[:200])
        return 0
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    with session_scope() as s:
        s.execute(select(UserRow).where(UserRow.email == email)).scalars().first().credits_balance = 100

    def balance() -> int:
        return int(client.get("/credits/balance", headers=headers).json()["balance"])

    for name, page, keep in (("cafe", CAFE, UNTOUCHED), ("nul", NUL, (b"<title>Annual\x00 report</title>",))):
        ar = client.post("/pipeline/analyze", files={"file": (f"{name}.html", page, "text/html")}, headers=headers)
        if ar.status_code != 200:
            check(f"HTTP {name}: analyze", False, f"{ar.status_code} {ar.text[:200]}")
            continue
        approved = [v["id"] for v in ar.json()["violations"] if v["ruleId"] == "MISSING_ALT_TEXT"]
        check(f"HTTP {name}: one missing alt to approve", len(approved) == 1, str(approved))
        before = balance()
        rr = client.post(
            "/pipeline/remediate",
            files={"file": (f"{name}.html", page, "text/html")},
            data={"approved_violations": json.dumps(approved), "rejected_violations": "[]"},
            headers=headers,
        )
        if rr.status_code != 200:
            check(f"HTTP {name}: remediate", False, f"{rr.status_code} {rr.text[:300]}")
            continue
        rj = rr.json()
        charged = before - balance()
        actions = [a.get("action") for a in rj["writer"]["applied"]]
        succeeded = [e for e in rj["executions"] if e.get("status") == "success"]
        check(f"HTTP {name}: the writer reports exactly the approved fix", actions == ["GENERATE_ALT_TEXT"], str(rj["writer"]["applied"]))
        check(f"HTTP {name}: one fix succeeded and something was charged for it", len(succeeded) == 1 and charged > 0,
              f"{len(succeeded)} charged={charged}")
        body = client.get(rj["downloadUrl"]).content
        for frag in keep:
            check(f"HTTP {name}: {frag[:28]!r}... downloaded byte for byte", frag in body, repr(re.findall(rb"<title>.*?</title>", body)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
