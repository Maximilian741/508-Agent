"""Smoke: every /pipeline and /auth error carries a stable code AND a sentence.

Robustness findings (reproduced): machine strings reached customers verbatim
("empty_upload", "file_content_mismatch: declared .docx but magic bytes do not
match", "invalid_ooxml: missing [Content_Types].xml"), and a password-protected
PDF was reported as "Ensure it is a valid, uncorrupted PDF" — telling the user
their file was broken when it was only locked.

Every error body from the /pipeline and /auth routers is now::

    {"detail": <unchanged>, "code": "<stable snake_case>", "message": "<sentence>"}

Pins, end to end through the real app:
  * the upload errors: empty_upload, file_content_mismatch, invalid_ooxml,
    unsupported_type (naming the extension), too_large (naming the limit);
  * the document errors: password_protected (analyze AND remediate, the latter
    uncharged), invalid_pdf for a broken PDF, and an owner-password-only PDF
    (opens without a password) is NOT misreported as password-protected;
  * money + identity: insufficient_credits, authentication_required,
    invalid_credentials, password_required, approved_violations_invalid,
    FastAPI validation -> invalid_request, bad download links;
  * ``detail`` is unchanged where callers already read it (back-compat);
  * every sentence in the catalogue reads like a sentence: capitalised, ends
    with a full stop, and contains no snake_case token or config key;
  * routers that did not opt in are byte-for-byte unchanged.

Run: python -m app.devtools.smoke_error_codes
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="508_smoke_errcodes_"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'err.db').as_posix()}"
os.environ["STORAGE_LOCAL_ROOT"] = str(_TMP / "storage")
os.environ["MATERIALIZED_ROOT"] = str(_TMP / "materialized")
os.environ["MAX_UPLOAD_MB"] = "1"
for _key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SEMANTIC_PROVIDER", "SMTP_HOST"):
    os.environ.pop(_key, None)

from fastapi.testclient import TestClient  # noqa: E402

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_SNAKE = re.compile(r"\b[a-z]+_[a-z_]+\b")


def _pdf(*, user_pw: str | None = None, owner_pw: str | None = None) -> bytes:
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=300, height=300)
    w.add_metadata({"/Title": "Locked"})
    if user_pw is not None or owner_pw is not None:
        w.encrypt(user_password=user_pw or "", owner_password=owner_pw or user_pw or "", algorithm="AES-128")
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _not_ooxml_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "not a word document")
    return buf.getvalue()


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, detail: object = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, "" if cond else f"  [{str(detail)[:400]}]")
        if not cond:
            failures += 1

    from app.api.errors import MESSAGES, describe
    from app.db.models import UserRow
    from app.db.session_sqlalchemy import session_scope
    from app.main import app

    c = TestClient(app)
    r = c.post("/auth/sign-in", json={"email": "codes@example.com", "password": "codespass1"})
    assert r.status_code == 200, r.text
    uid = r.json()["user"]["id"]
    auth = {"Authorization": f"Bearer {r.json()['token']}"}

    def is_sentence(text) -> bool:
        return (
            isinstance(text, str) and len(text) > 8 and text[0].isupper() and text.rstrip().endswith((".", "!"))
            and not _SNAKE.search(text) and "detail" not in text.lower()
        )

    def coded(label: str, resp, status: int, code: str, *, detail=None, contains: str = "") -> dict:
        body = {}
        try:
            body = resp.json()
        except Exception:
            pass
        ok = resp.status_code == status and body.get("code") == code and is_sentence(body.get("message"))
        if detail is not None:
            ok = ok and body.get("detail") == detail
        if contains:
            ok = ok and contains.lower() in (body.get("message") or "").lower()
        check(f"{label} -> {status} {code}", ok, (resp.status_code, body))
        return body

    def analyze(name: str, data: bytes, mime: str = "application/octet-stream", headers=None):
        return c.post("/pipeline/analyze", files={"file": (name, data, mime)}, headers=auth if headers is None else headers)

    # --- uploads -------------------------------------------------------------------
    # detail became the sentence itself when uploads learned to name what is
    # wrong with a file; the code is what a program branches on.
    coded("empty upload", analyze("empty.pdf", b""), 400, "empty_upload", contains="empty")
    coded("PDF bytes named .docx", analyze("really.docx", b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF", DOCX), 400, "file_content_mismatch")
    coded("a zip that isn't a Word file", analyze("zip.docx", _not_ooxml_zip(), DOCX), 400, "invalid_ooxml")
    coded("unsupported type", analyze("setup.exe", b"MZ\x90\x00" * 10), 400, "unsupported_type", contains=".exe")
    coded("over the upload limit", analyze("big.html", b"<p>" + b"a" * (1536 * 1024) + b"</p>", "text/html"), 413, "too_large", contains="1 MB")

    # --- documents -------------------------------------------------------------------
    locked = _pdf(user_pw="open-sesame")
    body = coded("user-password PDF (analyze)", analyze("locked.pdf", locked, "application/pdf"), 422, "password_protected", contains="password")
    check("...not blamed on the file being corrupt", "corrupt" not in json.dumps(body).lower(), body)
    with session_scope() as s:
        s.get(UserRow, uid).credits_balance = 20
    rr = c.post(
        "/pipeline/remediate",
        files={"file": ("locked.pdf", locked, "application/pdf")},
        data={"approved_violations": "[]", "rejected_violations": "[]"},
        headers=auth,
    )
    coded("user-password PDF (remediate)", rr, 422, "password_protected", contains="not charged")
    with session_scope() as s:
        bal = int(s.get(UserRow, uid).credits_balance or 0)
    check("...and nothing was charged", bal == 20, bal)
    owner_only = _pdf(owner_pw="owner-secret")
    r = analyze("owner-only.pdf", owner_only, "application/pdf")
    check("an owner-password-only PDF opens without a password -> 200, not 'password_protected'", r.status_code == 200, r.text[:200])
    coded(
        "a broken PDF",
        analyze("broken.pdf", b"%PDF-1.7\n" + b"\x00garbage" * 200, "application/pdf"),
        422,
        "invalid_pdf",
        detail="Failed to parse document. Ensure it is a valid, uncorrupted PDF, DOCX, PPTX, or HTML file.",
    )

    # --- money + identity ------------------------------------------------------------
    with session_scope() as s:
        s.get(UserRow, uid).credits_balance = 0
    page = b"<!doctype html><html><head></head><body><img src='a.png'></body></html>"
    rr = c.post(
        "/pipeline/remediate",
        files={"file": ("p.html", page, "text/html")},
        data={"approved_violations": "[]", "rejected_violations": "[]"},
        headers=auth,
    )
    coded("remediate with 0 credits", rr, 402, "insufficient_credits", detail="Insufficient credits")
    rr = c.post(
        "/pipeline/remediate",
        files={"file": ("p.html", page, "text/html")},
        data={"approved_violations": "not json", "rejected_violations": "[]"},
        headers=auth,
    )
    coded("approved_violations that isn't JSON", rr, 400, "approved_violations_invalid")
    coded("no session on a signed-in route", c.get("/pipeline/jobs"), 401, "authentication_required", detail="authentication_required")
    coded(
        "wrong password",
        c.post("/auth/sign-in", json={"email": "codes@example.com", "password": "wrong-password"}),
        401,
        "invalid_credentials",
        detail="invalid_credentials",
    )
    coded("new account, short password", c.post("/auth/sign-in", json={"email": "short@example.com", "password": "abc"}), 400, "password_required")
    body = coded("sign-in body missing its email", c.post("/auth/sign-in", json={"password": "whatever1"}), 422, "invalid_request")
    check("...FastAPI's field errors are still in detail", isinstance(body.get("detail"), list) and body["detail"], body)
    coded("a download link with no signature", c.get("/pipeline/files/abc123/x.pdf"), 403, "missing_signature")
    coded("a download link with a forged signature", c.get("/pipeline/files/abc123/x.pdf?exp=9999999999&sig=deadbeef"), 403, "bad_signature")

    # --- the catalogue itself ----------------------------------------------------------
    bad = {k: v for k, v in MESSAGES.items() if not is_sentence(v)}
    check("every catalogue message is a plain sentence (no snake_case, no config keys)", not bad, bad)

    class _E:
        def __init__(self, status, detail):
            self.status_code, self.detail, self.headers = status, detail, None

    samples = [
        (_E(400, "invalid_ooxml: missing [Content_Types].xml"), "invalid_ooxml"),
        (_E(413, "upload_rejected: decompressed size exceeds limit"), "too_large"),
        (_E(422, "We stopped before writing this file: the fixed version came out with less text"), "content_loss_refused"),
        (_E(422, "Could not remediate this file — it may be encrypted or corrupted. You were not charged."), "unreadable_document"),
        (_E(410, "url_revoked"), "url_revoked"),
        (_E(403, "verify_email_first"), "verify_email_first"),
        (_E(429, "too_many_attempts"), "too_many_attempts"),
        (_E(500, "invalid max_upload_bytes"), "internal_error"),
    ]
    for exc, want in samples:
        code, message = describe(exc)
        check(f"describe({exc.detail[:32]!r}) -> {want} + a sentence", code == want and is_sentence(message), (code, message))
    code, message = describe(_E(400, "Refusing to scan a private, loopback, or link-local address."), "/pipeline/analyze-url")
    check("the URL scan keeps its own specific sentence", code == "url_not_allowed" and message.startswith("Refusing to scan"), (code, message))

    # --- scope: routers that did not opt in are unchanged -------------------------------
    r = c.get("/credits/balance")
    check("a non-opted-in router's error body is unchanged ({'detail': ...} only)", r.status_code == 401 and set(r.json()) == {"detail"}, r.text)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
