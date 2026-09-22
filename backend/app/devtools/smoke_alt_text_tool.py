"""Smoke for the standalone AI alt-text tool (POST /tools/alt-text).

Proves the HTTP contract: auth required, real-image validation, provider
disclosure, and that it costs 0 credits. With no vision AI (the offline
provider — this smoke's environment) the answer is an EMPTY altText and a plain
message, never a placeholder to copy (it used to be "Uploaded image shown in
image."); smoke_semantic_refusals covers the answer from a vision provider.

Usage:
    python -m app.devtools.smoke_alt_text_tool
"""

from __future__ import annotations

import io
import os
import sys
import tempfile

_SMOKE_DB_DIR = tempfile.mkdtemp(prefix="508_smoke_alttool_")
os.environ["DATABASE_URL"] = f"sqlite:///{_SMOKE_DB_DIR}/smoke.db"

from fastapi.testclient import TestClient  # noqa: E402


def _png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (12, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    from app.main import app

    client = TestClient(app)

    signin = client.post(
        "/auth/sign-in",
        json={"email": "alttool@example.com", "displayName": "Alt", "password": "alttoolpass1"},
    )
    assert signin.status_code == 200, signin.text
    headers = {"Authorization": f"Bearer {signin.json()['token']}"}
    client.post("/auth/grant-starter", headers=headers)

    def balance() -> int:
        r = client.get("/credits/balance", headers=headers)
        return int(r.json().get("balance", -1)) if r.status_code == 200 else -1

    before = balance()

    # 1) Unauthenticated -> 401.
    anon = client.post("/tools/alt-text", files={"file": ("x.png", _png_bytes(), "image/png")})
    assert anon.status_code == 401, f"expected 401 without auth, got {anon.status_code}"

    # 2) Non-image payload -> 400.
    bad = client.post(
        "/tools/alt-text",
        files={"file": ("notes.txt", b"this is plainly not an image", "text/plain")},
        headers=headers,
    )
    assert bad.status_code == 400, f"expected 400 for non-image, got {bad.status_code} {bad.text}"

    # 3) Real PNG -> 200 with disclosure fields; no vision AI -> no alt text.
    ok = client.post(
        "/tools/alt-text",
        files={"file": ("photo.png", _png_bytes(), "image/png")},
        headers=headers,
    )
    assert ok.status_code == 200, f"alt-text failed: {ok.status_code} {ok.text}"
    data = ok.json()
    assert isinstance(data.get("altText"), str), data
    assert isinstance(data.get("provider"), str) and data["provider"], data
    assert isinstance(data.get("aiConfigured"), bool), data
    assert isinstance(data.get("confidence"), (int, float)), data
    if not data["aiConfigured"]:
        assert data["altText"] == "", f"no vision AI must mean no alt text to copy: {data}"
        assert data.get("message"), f"an empty answer must explain itself: {data}"
    else:
        assert data["altText"].strip(), data
    print(f"[smoke] alt-text tool returned: provider={data['provider']!r} aiConfigured={data['aiConfigured']} text={data['altText']!r}")

    # 4) Free — balance unchanged across the successful call.
    after = balance()
    assert after == before, f"alt-text tool must be free; balance {before} -> {after}"
    print(f"[smoke] tool is free (balance unchanged at {after})")

    print("ok alt-text tool smoke passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAIL alt-text tool smoke: {exc}")
        sys.exit(1)
    except Exception as exc:  # pragma: no cover
        print(f"FAIL alt-text tool smoke: {exc.__class__.__name__}: {exc}")
        sys.exit(1)
