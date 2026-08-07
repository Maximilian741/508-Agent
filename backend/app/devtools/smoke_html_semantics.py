"""Smoke: three new HTML detections + two new auto-fixes.

  IFRAME_TITLE_MISSING        (4.1.2 / 2.4.1)  detect-only
  INPUT_AUTOCOMPLETE_MISSING  (1.3.5, AA)      AUTO-FIX, persists
  POSITIVE_TABINDEX           (2.4.3)          AUTO-FIX, persists

Pins detection precision (no false positives), the full
detect -> execute -> write -> re-parse-clears round trip, and the honesty
invariant: the count the parser CLAIMS is exactly the count the writer WRITES,
because both walk the same iterator.

Usage:
    python -m app.devtools.smoke_html_semantics
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_sem_')}/s.db")

from pathlib import Path  # noqa: E402

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.api.pipeline import _action_persists  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.parsers.html_parser import (  # noqa: E402
    _parse_document,
    count_untitled_iframes,
    iter_autocomplete_candidates,
    iter_positive_tabindex,
)
from app.services.remediation_planner import RemediationPolicy, plan_remediations  # noqa: E402
from app.services.remediators.registry import execute_plans  # noqa: E402
from app.writers.html_writer import write_remediated_html  # noqa: E402

DIRTY = """<!DOCTYPE html><html lang="en"><head><title>Contact us</title></head><body>
  <h1>Contact</h1>
  <iframe src="https://maps.example/embed"></iframe>
  <form>
    <label for="em">Email</label><input id="em" name="email_address" type="text">
    <label for="ph">Phone</label><input id="ph" name="phoneNumber" type="text">
    <label for="zp">Zip</label><input id="zp" name="zipcode" type="text">
    <label for="pw">Password</label><input id="pw" name="password" type="password">
    <label for="q">Question</label><input id="q" name="favourite_colour" type="text">
  </form>
  <a href="/a" tabindex="3">Skip ahead</a>
  <a href="/b" tabindex="0">Fine</a>
  <a href="/c" tabindex="-1">Also fine</a>
</body></html>"""

# Everything already correct — nothing may be flagged.
CLEAN = """<!DOCTYPE html><html lang="en"><head><title>Clean</title></head><body>
  <h1>Clean</h1>
  <iframe src="https://maps.example/embed" title="Map of our office"></iframe>
  <form>
    <label for="em">Email</label><input id="em" name="email" type="email" autocomplete="email">
    <label for="op">Opt out</label><input id="op" name="optout" type="checkbox">
  </form>
  <a href="/b" tabindex="0">Fine</a>
</body></html>"""


def _flag_count(tree, code):
    n = 0

    def walk(node):
        nonlocal n
        n += sum(1 for f in node.accessibility_flags if f.code.value == code)
        for ch in node.children:
            walk(ch)

    walk(tree.root)
    return n


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="htmlsem_"))
    policy = RemediationPolicy(allow_auto_actions=True, allow_ai_actions=False,
                               require_human_review_for_all=False)

    # ---- honesty matrix ----
    check("honesty: SET_INPUT_AUTOCOMPLETE persists for html",
          _action_persists("SET_INPUT_AUTOCOMPLETE", "html"))
    check("honesty: FIX_POSITIVE_TABINDEX persists for html",
          _action_persists("FIX_POSITIVE_TABINDEX", "html"))
    for fmt in ("docx", "pptx", "pdf"):
        check(f"honesty: autocomplete NOT credited for {fmt}",
              not _action_persists("SET_INPUT_AUTOCOMPLETE", fmt))
    check("honesty: IFRAME_TITLE_MISSING is never a credited fix (manual-only)",
          not any(_action_persists("IFRAME_TITLE_MISSING", f) for f in ("html", "docx", "pptx", "pdf")))

    # ---- detection precision on the raw DOM ----
    doc = _parse_document(DIRTY.encode())
    tokens = {(c.get("name") or ""): t for c, t in iter_autocomplete_candidates(doc)}
    check("autocomplete: email field -> 'email'", tokens.get("email_address") == "email", str(tokens))
    check("autocomplete: phone field -> 'tel'", tokens.get("phoneNumber") == "tel", str(tokens))
    check("autocomplete: zip field -> 'postal-code'", tokens.get("zipcode") == "postal-code", str(tokens))
    check("autocomplete: PASSWORD field is never guessed (security)", "password" not in tokens, str(tokens))
    check("autocomplete: unknown-purpose field is left alone (no wrong token)",
          "favourite_colour" not in tokens, str(tokens))

    # --- COLLISION CORPUS (regression lock) -------------------------------
    # An adversarial review proved substring matching wrote personal-data tokens
    # onto unrelated fields — the browser would then silently prefill the user's
    # real name/phone/address into the wrong box, which is strictly WORSE than
    # the missing attribute. Every identifier below must yield NO token.
    from app.parsers.html_parser import _autocomplete_token_for

    class _Fake:
        tag = "input"

        def __init__(self, name, itype="text"):
            self._a = {"name": name, "type": itype}

        def get(self, k, d=None):
            return self._a.get(k, d)

    COLLISIONS = [
        "email_subject", "emailBody", "email_template_name",   # 'email'
        "phone_model", "telephone_provider", "headphone_model",
        "smartphone_brand", "microphone_input",                 # 'phone'
        "mobile_carrier", "automobile_make",                    # 'mobile'
        "zip_file", "unzip_path", "zipper_size",                # 'zip'
        "countryside_tour", "country_music_artist",             # 'country'
        "accompanying_guest", "company_size",                   # 'company'
        "model_name", "hotel_name", "label_name", "channel_name",
        "school_name", "tool_name",                             # 'lname'
        "pdf_name", "conf_name", "ref_name",                    # 'fname'
        "birthday_message",                                     # 'birthday'
    ]
    bad = {c: _autocomplete_token_for(_Fake(c)) for c in COLLISIONS}
    offenders = {k: v for k, v in bad.items() if v}
    check("autocomplete: NO collision produces a wrong token (26 identifiers)",
          not offenders, f"WRONG TOKENS: {offenders}")

    # Fields holding a THIRD PARTY's data are out of WCAG 1.3.5 scope.
    third = {c: _autocomplete_token_for(_Fake(c)) for c in
             ("recipient_email", "friend_email", "emergency_phone", "guest_name", "referral_email")}
    check("autocomplete: third-party fields are never autofilled with the user's data",
          not any(third.values()), str(third))

    # Types that can only produce a wrong token are excluded outright.
    check("autocomplete: type=url is never given a name/org token",
          _autocomplete_token_for(_Fake("company_website", "url")) is None)
    check("autocomplete: type=number is never given a name token",
          _autocomplete_token_for(_Fake("name", "number")) is None)

    # ...while the genuine positives still work (recall didn't collapse).
    POSITIVES = {
        "email_address": "email", "phoneNumber": "tel", "zipcode": "postal-code",
        "first_name": "given-name", "last_name": "family-name", "full_name": "name",
        "postal_code": "postal-code", "street_address": "street-address",
        "user_email": "email", "billing_zip": "postal-code", "company_name": "organization",
    }
    got = {k: _autocomplete_token_for(_Fake(k)) for k in POSITIVES}
    check("autocomplete: real purpose fields still detected (recall intact)",
          got == POSITIVES, f"got {got}")
    check("autocomplete: full_name -> 'name' (not shadowed by 'lname')",
          got.get("full_name") == "name", str(got.get("full_name")))
    check("tabindex: only the POSITIVE one is selected",
          [e.get("tabindex") for e in iter_positive_tabindex(doc)] == ["3"])
    check("iframe: untitled frame counted", count_untitled_iframes(doc) == 1)

    # ---- WCAG 2.5.3 Label in Name -----------------------------------------
    # A speech user says the words they SEE. An aria-label that REPLACES the
    # visible text makes the control unusable by voice; one that EXTENDS it is
    # correct and must never be flagged.
    from app.parsers.html_parser import count_label_in_name_mismatches

    def _mismatches(body: str) -> int:
        html = (
            '<!DOCTYPE html><html lang="en"><head><title>t</title></head>'
            f"<body>{body}</body></html>"
        )
        return count_label_in_name_mismatches(_parse_document(html.encode()))

    check("2.5.3: aria-label REPLACING visible text is flagged",
          _mismatches('<button aria-label="Submit form">Send order</button>') == 1)
    check("2.5.3: aria-label EXTENDING visible text is NOT flagged",
          _mismatches('<a href="/p" aria-label="Read more about pensions">Read more</a>') == 0)
    check("2.5.3: exact match is not flagged",
          _mismatches('<button aria-label="Search">Search</button>') == 0)
    check("2.5.3: case and punctuation differences are not a mismatch",
          _mismatches('<button aria-label="search now!">Search Now</button>') == 0)
    check("2.5.3: a control with no aria-label is never flagged",
          _mismatches("<button>Send order</button>") == 0)
    check("2.5.3: icon-only control is left to LINK_NAME_MISSING, not double-flagged",
          _mismatches('<a href="/x" aria-label="Home"><span class="icon"></span></a>') == 0)
    check("2.5.3: single-character labels are not treated as spoken commands",
          _mismatches('<button aria-label="Close dialog">x</button>') == 0)
    check("2.5.3: counts each offending control",
          _mismatches('<button aria-label="A">Send order</button>'
                      '<button aria-label="B">Cancel request</button>') == 2)

    # ---- flags fire ----
    src = tmp / "dirty.html"
    src.write_text(DIRTY, encoding="utf-8")
    res = parse_to_tree(str(src))
    run_analyzers(res.tree)
    for code in ("IFRAME_TITLE_MISSING", "INPUT_AUTOCOMPLETE_MISSING", "POSITIVE_TABINDEX"):
        check(f"flagged: {code}", _flag_count(res.tree, code) == 1, str(_flag_count(res.tree, code)))

    claimed_ac = (res.tree.root.metadata.properties or {}).get("inputs_missing_autocomplete")
    claimed_ti = (res.tree.root.metadata.properties or {}).get("positive_tabindex_count")
    check("parser claims 3 autocomplete fixes", claimed_ac == 3, str(claimed_ac))
    check("parser claims 1 tabindex fix", claimed_ti == 1, str(claimed_ti))

    # ---- execute + write ----
    execs = execute_plans(res.tree, plan_remediations(res.tree, policy))
    done = {e.action_code.value for e in execs if e.status.value == "success"}
    check("SET_INPUT_AUTOCOMPLETE executed", "SET_INPUT_AUTOCOMPLETE" in done, str(done))
    check("FIX_POSITIVE_TABINDEX executed", "FIX_POSITIVE_TABINDEX" in done, str(done))

    out = tmp / "fixed.html"
    result = write_remediated_html(src, res.tree, out)
    applied = result["applied"]
    n_ac = sum(1 for a in applied if a.get("action") == "SET_INPUT_AUTOCOMPLETE")
    n_ti = sum(1 for a in applied if a.get("action") == "FIX_POSITIVE_TABINDEX")
    # THE honesty invariant: claimed == written.
    check("writer wrote exactly the claimed number of autocomplete tokens",
          n_ac == claimed_ac, f"wrote {n_ac}, claimed {claimed_ac}")
    check("writer wrote exactly the claimed number of tabindex resets",
          n_ti == claimed_ti, f"wrote {n_ti}, claimed {claimed_ti}")

    text = out.read_text(encoding="utf-8")
    check("output declares autocomplete=\"email\"", 'autocomplete="email"' in text)
    check("output declares autocomplete=\"tel\"", 'autocomplete="tel"' in text)
    check("output declares autocomplete=\"postal-code\"", 'autocomplete="postal-code"' in text)
    check("output never autocompletes the password field",
          'type="password"' in text and 'autocomplete="password"' not in text)
    check("positive tabindex is gone from the output", 'tabindex="3"' not in text, text[:0])
    check("tabindex was reset to 0 (still focusable)", text.count('tabindex="0"') >= 2)

    # ---- round-trip: re-parsing the OUTPUT clears both auto-fixed flags ----
    res2 = parse_to_tree(str(out))
    run_analyzers(res2.tree)
    check("re-parse: INPUT_AUTOCOMPLETE_MISSING cleared",
          _flag_count(res2.tree, "INPUT_AUTOCOMPLETE_MISSING") == 0)
    check("re-parse: POSITIVE_TABINDEX cleared", _flag_count(res2.tree, "POSITIVE_TABINDEX") == 0)
    check("re-parse: IFRAME_TITLE_MISSING still flagged (we never fake a frame title)",
          _flag_count(res2.tree, "IFRAME_TITLE_MISSING") == 1)

    # ---- no false positives on a clean page ----
    csrc = tmp / "clean.html"
    csrc.write_text(CLEAN, encoding="utf-8")
    rc = parse_to_tree(str(csrc))
    run_analyzers(rc.tree)
    for code in ("IFRAME_TITLE_MISSING", "INPUT_AUTOCOMPLETE_MISSING", "POSITIVE_TABINDEX"):
        check(f"clean page: no {code}", _flag_count(rc.tree, code) == 0)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
