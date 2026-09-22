"""Smoke: protected PDFs are neither stripped nor mislabelled.

Before:
  * an owner-password PDF (opens without a password, restricts permissions)
    was remediated, charged, and returned UNENCRYPTED — its restrictions
    silently removed, nothing said;
  * a PDF that needs a password to OPEN was reported as "Failed to parse
    document. Ensure it is a valid, uncorrupted PDF ..." — the user was told
    their file was broken.

Now:
  1. owner-password PDFs (RC4-128, AES-128, AES-256) come back encrypted with
     the SAME permission flags, the author's ORIGINAL owner password still
     unlocks them, the empty user password still opens them, and the
     structure fix is inside (the text reads back);
  2. a user-password PDF gets a 422 that says it is password-protected, on
     both /pipeline/analyze and /pipeline/remediate, and nothing is charged.

Usage:
    python -m app.devtools.smoke_pdf_encryption
"""

from __future__ import annotations

import io
import sys

from app.devtools import _pdf_fixture_kit as K

_TMP = K.isolated_env("508_smoke_pdfenc_")

from pypdf import PasswordType, PdfReader, PdfWriter  # noqa: E402

PERMS = (1 << 2) | (1 << 4) | (1 << 9)  # print + copy + accessibility; NOT modify


def plain() -> bytes:
    w = PdfWriter()
    f = K.helvetica(w)
    c = K.bt("F1", 20, 72, 720, K.lit("Snow Emergency Routes"))
    for i in range(12):
        c += K.bt("F1", 11, 72, 690 - 14 * i, K.lit("Parking is banned on posted snow routes until the emergency is lifted."))
    K.add_page(w, c, {"F1": f})
    return K.to_bytes(w)


def encrypt(data: bytes, *, user: str, owner: str, algorithm: str) -> bytes:
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    w.encrypt(user_password=user, owner_password=owner, permissions_flag=PERMS, algorithm=algorithm)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def main() -> int:
    check = K.Checker()
    pipe = K.Pipeline("pdfenc@example.com")
    base = plain()

    for alg in ("RC4-128", "AES-128", "AES-256"):
        data = encrypt(base, user="", owner="owner-secret", algorithm=alg)
        src = PdfReader(io.BytesIO(data))
        check(f"[{alg}] fixture is encrypted, opens with no password", src.is_encrypted and src.decrypt("") != PasswordType.NOT_DECRYPTED)
        p_src = src._encryption.P  # noqa: SLF001
        r = pipe.remediate(f"enc_{alg}.pdf", data)
        check(f"[{alg}] remediate succeeds", r.status_code == 200, r.text[:300])
        if r.status_code != 200:
            continue
        body = r.json()
        out_bytes = pipe.download(body)
        o1 = PdfReader(io.BytesIO(out_bytes))
        check(f"[{alg}] output is STILL encrypted", o1.is_encrypted)
        opened = o1.decrypt("")
        check(f"[{alg}] output opens with the empty user password", opened == PasswordType.USER_PASSWORD, str(opened))
        check(f"[{alg}] same permission flags", o1._encryption.P == p_src, f"{o1._encryption.P} vs {p_src}")  # noqa: SLF001
        o2 = PdfReader(io.BytesIO(out_bytes))
        check(f"[{alg}] the author's ORIGINAL owner password still works",
              o2.decrypt("owner-secret") == PasswordType.OWNER_PASSWORD)
        check(f"[{alg}] the fix is inside: tagged, text intact",
              "/StructTreeRoot" in o1.trailer["/Root"] and "Snow Emergency Routes" in (o1.pages[0].extract_text() or ""))
        notices = " ".join(n.get("notice", "") for n in (body.get("writer") or {}).get("notices", []))
        check(f"[{alg}] the writer says the protection was kept", "pdf_protection_preserved" in notices, notices)

    locked = encrypt(base, user="open-sesame", owner="owner-secret", algorithm="AES-128")
    a = pipe.analyze("locked.pdf", locked)
    check("user-password PDF: analyze says password-protected (422)",
          a.status_code == 422 and "password-protected" in a.json().get("detail", ""), f"{a.status_code} {a.text[:200]}")
    check("...not 'invalid, uncorrupted'", "uncorrupted" not in a.text)
    b0 = pipe.balance()
    rr = pipe.client.post(
        "/pipeline/remediate",
        files={"file": ("locked.pdf", locked, "application/pdf")},
        data={"approved_violations": "[]", "rejected_violations": "[]"},
        headers=pipe.headers,
    )
    check("remediate says password-protected too (422)",
          rr.status_code == 422 and "password-protected" in rr.json().get("detail", ""), f"{rr.status_code} {rr.text[:200]}")
    check("...and charges nothing", pipe.balance() == b0, f"{b0} -> {pipe.balance()}")
    return check.done()


if __name__ == "__main__":
    sys.exit(main())
