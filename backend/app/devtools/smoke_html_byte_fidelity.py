"""Smoke: bytes the text model can't hold still come back out exactly.

What broke, each silently:
  * a single NUL byte ended the document inside libxml2 (it reads a str as a
    C string). Everything after it vanished from the ANALYSIS — the page's
    unlabeled images and "click here" links were never reported — and from
    the "fixed" file; the content-loss gate parses the same way, so it saw
    two equally short documents and let the truncated page ship;
  * a UTF-16 page saved without a byte-order mark decoded as NUL-interleaved
    garbage: the analysis said "title and language missing" and nothing else,
    and /remediate 422'd;
  * a page with no charset declaration that was pure ASCII came back with raw
    UTF-8 bytes (lxml turns &nbsp; / &copy; / &mdash; into the characters):
    Firefox never guesses UTF-8 for an undeclared page served over HTTP, so
    every non-breaking space rendered as "Â ";
  * a byte that is invalid in the page's declared encoding (a windows-1252
    "é" in a page that says utf-8) was re-encoded as U+FFFD — the original
    byte destroyed, in text we never touched;
  * an empty/whitespace-only or comment-only page analysed as "title and
    language missing" and /remediate charged to wrap a <title> around nothing.

Usage:
    python -m app.devtools.smoke_html_byte_fidelity
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="508_smoke_bytes_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/s.db"
os.environ["MATERIALIZED_ROOT"] = str(Path(_TMP) / "materialized")
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)

from app.models.accessibility import (  # noqa: E402
    HeadingNode,
    ImageNode,
    LinkNode,
    ParagraphNode,
    iter_reading_order,
)
from app.parsers.html_parser import HTMLParser, decode_html_bytes  # noqa: E402
from app.writers.html_writer import _visible_word_count, write_remediated_html  # noqa: E402

TMP = Path(_TMP)
ALT = "Chart — café"


def _roundtrip(name: str, data: bytes):
    src = TMP / f"{name}.html"
    src.write_bytes(data)
    res = HTMLParser().parse_to_tree(str(src))
    for n in iter_reading_order(res.tree.root):
        if isinstance(n, ImageNode):
            n.alt_text = ALT
    res.tree.root.metadata.language = "en"
    out = TMP / f"{name}.out.html"
    report = write_remediated_html(src, res.tree, out)
    again = HTMLParser().parse_to_tree(str(out))
    return res.tree, out.read_bytes(), again.tree, report


def _kinds(tree):
    return [type(n).__name__ for n in iter_reading_order(tree.root) if n is not tree.root]


def _texts(tree):
    return [n.content.text for n in iter_reading_order(tree.root)
            if isinstance(n, (ParagraphNode, HeadingNode, LinkNode)) and n.content and n.content.text]


def _alts(tree):
    return [n.alt_text for n in iter_reading_order(tree.root) if isinstance(n, ImageNode)]


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    # ---- 1. a NUL byte mid-page ---------------------------------------------
    nul = (b"<html><head><title>Quarterly report</title></head><body><h1>Report</h1>\x00"
           b"<p>Revenue grew in every region.</p><img src='chart.png'>"
           b"<a href='/more'>click here</a></body></html>")
    tree, out, again, rep = _roundtrip("nul", nul)
    check("NUL: analysis still sees the content AFTER the NUL (paragraph, image, link)",
          _kinds(tree) == ["HeadingNode", "ParagraphNode", "ImageNode", "LinkNode"], str(_kinds(tree)))
    check("NUL: the fixed file keeps every word after the NUL",
          b"Revenue grew in every region." in out and b">click here</a>" in out, repr(out[-200:]))
    check("NUL: the NUL byte itself is still there, in place", b"</h1>\x00<p>" in out, repr(out[:160]))
    check("NUL: the approved alt landed", _alts(again) == [ALT], str(_alts(again)))
    check("NUL: the content-loss gate counts the words after the NUL",
          _visible_word_count(nul) == _visible_word_count(out) and _visible_word_count(nul) >= 8,
          f"{_visible_word_count(nul)} vs {_visible_word_count(out)}")

    # A NUL INSIDE a tag would change the tree if kept as a stand-in ("<im?g"
    # is not an <img>): the writer then uses the tree the analysis saw.
    in_tag = (b"<html><head><title>T</title></head><body><p>Intro words here.</p>"
              b"<im\x00g src='x.png'><p>After the image.</p></body></html>")
    tree, out, again, rep = _roundtrip("nul_in_tag", in_tag)
    check("NUL inside a tag: the analysis's image is the one that gets the alt",
          _alts(again) == [ALT] and b"<img " in out, repr(out))
    check("NUL inside a tag: the markup is not written back escaped", b"&lt;" not in out, repr(out))
    check("NUL inside a tag: every word survives", b"Intro words here." in out and b"After the image." in out)
    # UTF-32 (no BOM) is NUL-riddled markup; it must never come back as
    # escaped tag soup.
    u32 = "<html><head><title>Wide</title></head><body><p>Thirty two bits</p><img src='w.png'></body></html>".encode("utf-32-le")
    tree, out, again, rep = _roundtrip("utf32", u32)
    check("UTF-32 (no BOM): read as the page it is, fixed, markup intact",
          _texts(tree) == ["Thirty two bits"] and _alts(again) == [ALT] and b"&lt;" not in out, repr(out[:120]))

    # ---- 2. UTF-16 without a BOM --------------------------------------------
    wide_src = "<html><head><title>Résumé</title></head><body><p>Hello wide world</p><img src='x.png'></body></html>"
    tree, out, again, rep = _roundtrip("utf16", wide_src.encode("utf-16-le"))
    check("UTF-16LE (no BOM): the analysis reads the real page",
          (tree.root.metadata.properties or {}).get("title") == "Résumé" and _texts(tree) == ["Hello wide world"],
          repr((tree.root.metadata.properties or {}).get("title")))
    check("UTF-16LE (no BOM): the fix is written", {a.get("action") for a in rep["applied"]} >= {"GENERATE_ALT_TEXT"}, str(rep))
    check("UTF-16LE (no BOM): output is still UTF-16LE with no BOM added",
          not out.startswith(b"\xff\xfe") and "Hello wide world".encode("utf-16-le") in out)
    check("UTF-16LE (no BOM): re-parse reads the alt back", _alts(again) == [ALT], str(_alts(again)))
    _, info = decode_html_bytes("<p>x</p>".encode("utf-16-be"))
    check("UTF-16BE (no BOM) is recognised too", info.encoding == "utf-16-be", info.encoding)
    _, info = decode_html_bytes(b"\x00\x01binary\x00\x00junk" * 20)
    check("binary junk is not mistaken for UTF-16 (must start with '<')", not info.encoding.startswith("utf-16"), info.encoding)

    # ---- 3. an undeclared, pure-ASCII page stays pure ASCII -----------------
    ascii_page = (b"<html><head><title>Tom &amp; Jerry &mdash; notes</title></head><body>"
                  b"<p>A&nbsp;B &copy; 2024 &mdash; all rights</p><img src='x.png'></body></html>")
    tree, out, again, rep = _roundtrip("ascii", ascii_page)
    check("undeclared ASCII: output has no byte above 0x7F", out.isascii(), repr(out))
    check("undeclared ASCII: entities come back as references", b"&#160;" in out and b"&#169;" in out and b"&#8212;" in out)
    check("undeclared ASCII: the inserted em dash / é are references too", b'alt="Chart &#8212; caf&#233;"' in out, repr(out))
    check("undeclared ASCII: the text reads exactly the same after the fix",
          _texts(again) == _texts(tree) and (again.root.metadata.properties or {}).get("title") == "Tom & Jerry — notes",
          repr(_texts(again)))
    check("undeclared ASCII: no charset declaration was invented", b"charset" not in out.lower())
    # A DECLARED UTF-8 page is not forced to ASCII.
    tree, out, again, rep = _roundtrip("declared", b"<html><head><meta charset='utf-8'><title>T</title></head><body><p>A&nbsp;B</p><img src='x.png'></body></html>")
    check("declared UTF-8: inserted text is plain UTF-8 (no needless references)", ALT.encode("utf-8") in out, repr(out))

    # ---- 4. bytes invalid in the declared encoding survive ------------------
    mixed = (b"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Menu</title></head><body>"
             b"<p>Caf\xe9 menu \xa9 2024</p><img src='a.png'></body></html>")
    tree, out, again, rep = _roundtrip("mixed", mixed)
    check("invalid bytes: the analysis shows what a browser shows (U+FFFD)", _texts(tree) == ["Caf� menu � 2024"], repr(_texts(tree)))
    check("invalid bytes: the ORIGINAL bytes are in the output, not EF BF BD",
          b"<p>Caf\xe9 menu \xa9 2024</p>" in out and b"\xef\xbf\xbd" not in out, repr(out))
    check("invalid bytes: the inserted alt is proper UTF-8", ALT.encode("utf-8") in out)
    check("invalid bytes: re-parse reads the same text and the alt", _texts(again) == _texts(tree) and _alts(again) == [ALT])
    # Shift_JIS with a stray invalid byte: kept too.
    sj = ("<html><head><meta charset='shift_jis'><title>日本</title></head><body><p>こんにちは</p>".encode("shift_jis")
          + b"<p>bad \x80 byte</p><img src='j.png'></body></html>")
    tree, out, again, rep = _roundtrip("sjis", sj)
    check("Shift_JIS: a stray invalid byte survives and the rest stays Shift_JIS",
          b"<p>bad \x80 byte</p>" in out and "こんにちは".encode("shift_jis") in out and b"&#8212;" in out, repr(out[-120:]))

    # ---- 5. nothing to check -> refused, never charged ---------------------
    for name, blob in (("blank", b"  \r\n\t "), ("bom_only", b"\xef\xbb\xbf\n"), ("comment_only", b"<!-- todo -->"),
                       ("doctype_only", b"<!DOCTYPE html>")):
        p = TMP / f"{name}.html"
        p.write_bytes(blob)
        try:
            HTMLParser().parse_to_tree(str(p))
            refused = False
        except ValueError:
            refused = True
        check(f"{name}: analysis refuses a page with no content", refused)

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
    email = "bytes-smoke@example.com"
    r = client.post("/auth/sign-in", json={"email": email, "displayName": "B", "password": "bytespass12"})
    if r.status_code != 200:
        check("HTTP: sign-in", False, r.text[:200])
        return 0
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    with session_scope() as s:
        s.execute(select(UserRow).where(UserRow.email == email)).scalars().first().credits_balance = 50

    def balance() -> int:
        return int(client.get("/credits/balance", headers=headers).json()["balance"])

    blank = b"   \n\n  "
    ar = client.post("/pipeline/analyze", files={"file": ("blank.html", blank, "text/html")}, headers=headers)
    check("HTTP: a whitespace-only page is not given a score", ar.status_code == 422, f"{ar.status_code} {ar.text[:200]}")
    before = balance()
    rr = client.post(
        "/pipeline/remediate",
        files={"file": ("blank.html", blank, "text/html")},
        data={"approved_violations": json.dumps(["x"]), "rejected_violations": "[]"},
        headers=headers,
    )
    check("HTTP: remediating it is refused", rr.status_code == 422, f"{rr.status_code} {rr.text[:200]}")
    check("HTTP: and nothing is charged", balance() == before)

    page = (b"<html><head><title>Board minutes</title></head><body><h1>Minutes</h1>\x00"
            b"<p>The board approved the budget.</p><img src='sig.png'><a href='/x'>click here</a></body></html>")
    ar = client.post("/pipeline/analyze", files={"file": ("minutes.html", page, "text/html")}, headers=headers)
    rules = sorted({v["ruleId"] for v in ar.json().get("violations", [])}) if ar.status_code == 200 else []
    check("HTTP: a page with a NUL reports the image and link after it",
          {"MISSING_ALT_TEXT", "LINK_TEXT_NON_DESCRIPTIVE"} <= set(rules), f"{ar.status_code} {rules}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
