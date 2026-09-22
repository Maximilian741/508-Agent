"""Smoke: an HTML page's characters survive analysis AND the fixed file.

The bug this pins: a UTF-8 page whose ``<title>`` held a non-ASCII character
BEFORE its ``<meta charset="utf-8">`` was handed to libxml2 as raw bytes;
libxml2 had already committed to latin-1 by the time it reached the meta, so
the analysis read "Newsletter â€" September" and the delivered file was
re-serialised DOUBLE-encoded — every é, — and € on the page turned to
mojibake. Word counts matched, so the content-loss gate passed it, and the
customer was charged. A windows-1252 page with no declaration was emitted as
charset-less UTF-8 (the browser still reads it as windows-1252: mojibake
again), and a Word "Save as Web Page" export lost its only charset
declaration, the ``<meta http-equiv="Content-Type">``.

Pinned here:
  * decoding follows the browser: BOM > <meta charset>/http-equiv anywhere in
    <head> > XML declaration > valid-UTF-8 > windows-1252; latin-1/ascii
    labels mean windows-1252; unusable labels (utf-7, unicode_escape) are
    ignored;
  * the writer re-encodes in the SOURCE's encoding (same BOM, same XML
    declaration, same doctype-or-none, the http-equiv meta kept): untouched
    text is byte-identical, text we insert that the encoding can't hold
    becomes a character reference;
  * a page lxml can't build a document from is never replaced by a blank one;
  * end to end over HTTP: /pipeline/analyze reports the real title and the
    downloaded file carries the real bytes.

Usage:
    python -m app.devtools.smoke_html_charset_roundtrip
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="508_smoke_charset_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/s.db"
os.environ["MATERIALIZED_ROOT"] = str(Path(_TMP) / "materialized")
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)

from app.models.accessibility import ImageNode, ParagraphNode, iter_reading_order  # noqa: E402
from app.parsers.html_parser import HTMLParser, decode_html_bytes  # noqa: E402
from app.writers.html_writer import write_remediated_html  # noqa: E402

TMP = Path(_TMP)
ALT = "Logo — café"  # an em dash + é: not representable in every encoding


def _roundtrip(name: str, data: bytes, alt: str = ALT, lang: str = "fr"):
    """parse -> set alt + language -> write -> (tree, out bytes, reparsed tree, report)."""
    src = TMP / f"{name}.html"
    src.write_bytes(data)
    res = HTMLParser().parse_to_tree(str(src))
    for n in iter_reading_order(res.tree.root):
        if isinstance(n, ImageNode):
            n.alt_text = alt
    res.tree.root.metadata.language = lang
    out = TMP / f"{name}.out.html"
    report = write_remediated_html(src, res.tree, out)
    again = HTMLParser().parse_to_tree(str(out))
    return res.tree, out.read_bytes(), again.tree, report


def _title(tree) -> str:
    return (tree.root.metadata.properties or {}).get("title") or ""


def _paras(tree):
    return [n.content.text for n in iter_reading_order(tree.root) if isinstance(n, ParagraphNode) and n.content.text]


def _alts(tree):
    return [n.alt_text for n in iter_reading_order(tree.root) if isinstance(n, ImageNode)]


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    body = "<p>naïve café — 10 €</p><img src=a.png>"

    # ---- 1. the reported bug: non-ASCII before <meta charset="utf-8"> -------
    page = ("<!DOCTYPE html><html><head><title>Café — Menú</title><meta charset=\"utf-8\"></head>"
            "<body>" + body + "</body></html>").encode("utf-8")
    tree, out, again, rep = _roundtrip("meta_after_title", page)
    check("meta after title: analysis reads the real title", _title(tree) == "Café — Menú", repr(_title(tree)))
    check("meta after title: analysis reads the real body text", _paras(tree) == ["naïve café — 10 €"], repr(_paras(tree)))
    check("meta after title: output has NO double-encoded bytes",
          b"\xc3\x83" not in out and b"\xc3\xa2\xc2" not in out, repr(out[:200]))
    check("meta after title: output title/body are the same UTF-8 bytes as the source",
          "Café — Menú".encode("utf-8") in out and "naïve café — 10 €".encode("utf-8") in out)
    check("meta after title: re-parse of the output reads identical text",
          _title(again) == "Café — Menú" and _paras(again) == _paras(tree) and _alts(again) == [ALT],
          f"{_title(again)!r} {_paras(again)!r} {_alts(again)!r}")
    check("meta after title: the fixes still land", {a.get("action") for a in rep["applied"]} >= {"GENERATE_ALT_TEXT", "SET_DOCUMENT_LANGUAGE"}, str(rep))

    # http-equiv after the title, and a meta more than 1024 bytes in.
    for label, pg in (
        ("http-equiv after title", "<html><head><title>Café — Menú</title><meta http-equiv=\"Content-Type\" content=\"text/html; charset=UTF-8\"></head><body>" + body + "</body></html>"),
        ("meta 1.5 KB into <head>", "<html><head><title>Café — Menú</title><!-- " + "x" * 1500 + " --><meta charset=\"utf-8\"></head><body>" + body + "</body></html>"),
    ):
        tree, out, again, _ = _roundtrip(label.replace(" ", "_").replace("<", "").replace(">", ""), pg.encode("utf-8"))
        check(f"{label}: title and text decoded as UTF-8", _title(tree) == "Café — Menú" and _paras(tree) == ["naïve café — 10 €"], repr(_title(tree)))
        check(f"{label}: output round-trips", _title(again) == "Café — Menú" and b"\xc3\x83" not in out)

    # ---- 2. legacy windows-1252, no declaration ----------------------------
    legacy = ("<html><head><title>Résumé — “Q3”</title></head><body><p>naïve café — 10 €</p><img src=a.png></body></html>").encode("cp1252")
    tree, out, again, _ = _roundtrip("cp1252_undeclared", legacy)
    check("undeclared windows-1252: 0x97/0x93/0x80 read as — “ €", _title(tree) == "Résumé — “Q3”" and _paras(tree) == ["naïve café — 10 €"], repr(_title(tree)))
    check("undeclared windows-1252: output stays windows-1252 (not re-encoded as UTF-8)",
          "Résumé — “Q3”".encode("cp1252") in out and b"\xc3" not in out, repr(out[:160]))
    check("undeclared windows-1252: inserted alt is encoded in windows-1252", ALT.encode("cp1252") in out)
    check("undeclared windows-1252: no charset declaration was added", b"charset" not in out.lower())
    check("undeclared windows-1252: re-parse identical", _title(again) == _title(tree) and _alts(again) == [ALT])

    # ---- 3. declared labels follow the web's mapping -------------------------
    latin1 = "<html><head><meta charset=\"iso-8859-1\"><title>“Quoted”</title></head><body><p>x</p></body></html>".encode("cp1252")
    text, info = decode_html_bytes(latin1)
    check("charset=iso-8859-1 is windows-1252 (0x93 is a curly quote, not a C1 control)",
          info.encoding == "cp1252" and "“Quoted”" in text, f"{info.encoding} {text[:80]!r}")
    for bad in ("utf-7", "unicode_escape", "rot13", "base64", "no-such-charset"):
        _, info = decode_html_bytes(f"<html><head><meta charset=\"{bad}\"><title>Café</title></head></html>".encode("utf-8"))
        check(f"unusable label {bad!r} is ignored (falls back to UTF-8 detection)", info.encoding == "utf-8", info.encoding)
    commented = b"<html><head><!-- <meta charset=\"shift_jis\"> --><title>Caf\xc3\xa9</title></head></html>"
    _, info = decode_html_bytes(commented)
    check("a <meta charset> inside a comment declares nothing", info.encoding == "utf-8", info.encoding)

    # ---- 4. a multibyte legacy page: Shift_JIS -----------------------------
    sjis = ("<html><head><meta charset=\"shift_jis\"><title>日本語のページ</title></head><body><p>こんにちは世界</p><img src=a.png></body></html>").encode("shift_jis")
    tree, out, again, _ = _roundtrip("shift_jis", sjis, lang="ja")
    check("Shift_JIS: decoded correctly", _title(tree) == "日本語のページ" and _paras(tree) == ["こんにちは世界"], repr(_title(tree)))
    check("Shift_JIS: untouched text keeps its Shift_JIS bytes", "こんにちは世界".encode("shift_jis") in out)
    check("Shift_JIS: an inserted character it can't hold becomes a reference", b"&#8212;" in out, repr(out[-160:]))
    check("Shift_JIS: re-parse reads the inserted alt back", _alts(again) == [ALT], repr(_alts(again)))

    # ---- 5. BOMs are honoured and kept -------------------------------------
    bom8 = b"\xef\xbb\xbf" + "<html><head><title>Café</title><meta charset=\"iso-8859-1\"></head><body><p>é</p><img src=a.png></body></html>".encode("utf-8")
    tree, out, again, _ = _roundtrip("bom_utf8", bom8)
    check("UTF-8 BOM beats a contradicting meta", _title(tree) == "Café" and _paras(tree) == ["é"], repr(_title(tree)))
    check("UTF-8 BOM is kept on the output", out.startswith(b"\xef\xbb\xbf") and _title(again) == "Café")
    bom16 = "﻿<html><head><title>Café</title></head><body><p>é</p><img src=a.png></body></html>".encode("utf-16-le")
    tree, out, again, _ = _roundtrip("bom_utf16", bom16)
    check("UTF-16LE (BOM) decoded", _title(tree) == "Café", repr(_title(tree)))
    check("UTF-16LE output keeps its BOM and encoding", out.startswith(b"\xff\xfe") and _title(again) == "Café" and _alts(again) == [ALT])

    # ---- 6. XHTML with an XML declaration ----------------------------------
    xhtml = ("<?xml version=\"1.0\" encoding=\"iso-8859-1\"?>\n<!DOCTYPE html PUBLIC \"-//W3C//DTD XHTML 1.0 Strict//EN\" "
             "\"http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd\">\n<html xmlns=\"http://www.w3.org/1999/xhtml\"><head><title>Déjà vu</title></head>"
             "<body><p>Garçon</p><img src=\"x.png\" /></body></html>").encode("latin-1")
    tree, out, again, rep = _roundtrip("xhtml", xhtml)
    check("XHTML XML declaration: parsed (not replaced by an empty document)", _title(tree) == "Déjà vu" and _paras(tree) == ["Garçon"], repr(_title(tree)))
    check("XHTML XML declaration: kept verbatim at the top, no '??>'",
          out.startswith(b"<?xml version=\"1.0\" encoding=\"iso-8859-1\"?>\n") and b"??>" not in out, repr(out[:80]))
    check("XHTML: its own doctype kept", b"XHTML 1.0 Strict" in out)
    check("XHTML: re-parse identical", _title(again) == "Déjà vu" and _alts(again) == [ALT])

    # ---- 7. no invented doctype; top-level comments kept; http-equiv kept ---
    plain = b"<!-- saved from url=(0014)about:internet -->\n<html><head><title>T</title></head><body><p>x</p><img src=a.png></body></html>"
    _, out, _, _ = _roundtrip("no_doctype", plain)
    check("a page without a doctype does not gain one", b"<!DOCTYPE" not in out.upper(), repr(out[:120]))
    check("a comment before <html> survives", b"saved from url" in out)
    word = ("<html><head><meta http-equiv=Content-Type content=\"text/html; charset=windows-1252\"><title>Board minutes – draft</title></head>"
            "<body><p class=MsoNormal>Nuñez<o:p></o:p></p><img src=a.png></body></html>").encode("cp1252")
    tree, out, again, _ = _roundtrip("word_export", word)
    check("Word export: its http-equiv charset meta is kept (it is the page's only declaration)",
          b"http-equiv" in out.lower() and b"windows-1252" in out, repr(out[:200]))
    check("Word export: text stays windows-1252", "Board minutes – draft".encode("cp1252") in out and _title(again) == "Board minutes – draft")

    # ---- 8. never ship a blank page for something lxml can't build ----------
    junk = b"<!-- only a comment -->"
    src = TMP / "comment_only.html"
    src.write_bytes(junk)
    try:
        HTMLParser().parse_to_tree(str(src))
        refused = False
    except ValueError as exc:
        refused = "no page content" in str(exc)
    check("unbuildable page: analysis refuses it (no near-clean score for nothing)", refused)
    # The writer must hold the line on its own too: hand it a tree from a real
    # page but the junk bytes as the source.
    real = TMP / "real_for_junk.html"
    real.write_bytes(b"<html><head><title>x</title></head><body><p>x</p></body></html>")
    res = HTMLParser().parse_to_tree(str(real))
    res.tree.root.metadata.language = "en"
    out_path = TMP / "comment_only.out.html"
    rep = write_remediated_html(src, res.tree, out_path)
    check("unbuildable page: writer applies nothing and says so",
          not rep["applied"] and any("failed_to_open" in s.get("reason", "") for s in rep["skipped"]), str(rep))
    check("unbuildable page: output is the untouched source", out_path.read_bytes() == junk)

    # ---- 9. end to end over HTTP -------------------------------------------
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
    email = "charset-smoke@example.com"
    r = client.post("/auth/sign-in", json={"email": email, "displayName": "C", "password": "charsetpass1"})
    if r.status_code != 200:
        check("HTTP: sign-in", False, r.text[:200])
        return 0
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    with session_scope() as s:
        s.execute(select(UserRow).where(UserRow.email == email)).scalars().first().credits_balance = 50

    page = ("<!DOCTYPE html><html><head><title>Newsletter — September</title><meta charset=\"utf-8\"></head>"
            "<body><h1>Newsletter — September</h1><p>This month the café opens a new terrace — tickets cost 12 €.</p>"
            "<h3>Events</h3></body></html>").encode("utf-8")
    ar = client.post("/pipeline/analyze", files={"file": ("news.html", page, "text/html")}, headers=headers)
    check("HTTP: analyze 200", ar.status_code == 200, ar.text[:200])
    if ar.status_code != 200:
        return 0
    data = ar.json()
    check("HTTP: analyze reports the real title (not 'Newsletter â€\" September')",
          data["summary"].get("title") == "Newsletter — September", repr(data["summary"].get("title")))
    ids = [v["id"] for v in data["violations"]]
    rr = client.post(
        "/pipeline/remediate",
        files={"file": ("news.html", page, "text/html")},
        data={"approved_violations": json.dumps(ids), "rejected_violations": "[]"},
        headers=headers,
    )
    check("HTTP: remediate 200", rr.status_code == 200, rr.text[:300])
    if rr.status_code != 200:
        return 0
    dl = client.get(rr.json()["downloadUrl"], headers=headers)
    check("HTTP: download 200", dl.status_code == 200)
    body = dl.content
    check("HTTP: downloaded file carries the page's real UTF-8 bytes, no double encoding",
          "Newsletter — September".encode("utf-8") in body and "12 €".encode("utf-8") in body and b"\xc3\x83" not in body and b"\xc3\xa2\xc2" not in body,
          repr(body[:200]))
    check("HTTP: the approved fix (heading level) is in the file", b"<h2>Events</h2>" in body, repr(body[-160:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
