"""Smoke test for HS256-signed session JWTs.

Exercises three layers:

1. ``mint_session`` / ``verify_session`` round-trip in isolation.
2. Tampering and expiry rejection.
3. End-to-end /auth/sign-in -> /auth/me using ``Authorization: Bearer``.

Usage:
    python -m app.devtools.smoke_jwt_session
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

# Pin a known APP_SECRET + dedicated sqlite db before importing anything that
# reads settings.  Must happen before app.config / db imports.
os.environ["APP_SECRET"] = "smoke-test-secret-do-not-use-in-prod"
_TMP_DIR = tempfile.mkdtemp(prefix="508_jwt_smoke_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DIR}/smoke.db"

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.auth import router as auth_router  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session_sqlalchemy import ENGINE  # noqa: E402
from app.db import models as _models  # noqa: F401, E402
from app.security.sessions import mint_session, verify_session  # noqa: E402


def _bootstrap() -> TestClient:
    Base.metadata.create_all(bind=ENGINE)
    app = FastAPI(title="jwt-smoke")
    app.include_router(auth_router)
    return TestClient(app)


def main() -> int:
    print("[1] mint + verify round-trip")
    user_id = "abc123-test-user"
    token = mint_session(user_id, ttl_seconds=120)
    assert isinstance(token, str) and token.count(".") == 2, token
    claims = verify_session(token)
    assert claims is not None, "verify_session returned None for fresh token"
    assert claims["sub"] == user_id, claims
    assert "iat" in claims and "exp" in claims, claims
    print("    sub=", claims["sub"], "iat=", claims["iat"], "exp=", claims["exp"])

    print("[2] tampered token rejected")
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    assert verify_session(tampered) is None, "tampered token must not verify"

    print("[3] garbage tokens rejected")
    assert verify_session("") is None
    assert verify_session("not.a.jwt") is None
    assert verify_session("a.b.c") is None

    print("[4] expired token rejected")
    expired = mint_session("expired-user", ttl_seconds=60)
    # mint_session clamps to >=60s; force expiry by hand-encoding a short-lived
    # one via the same code path with a tiny ttl injected at the JWT layer.
    import jwt  # noqa: WPS433
    from app.config import get_settings  # noqa: WPS433

    past = int(time.time()) - 10
    really_expired = jwt.encode(
        {"sub": "expired-user", "iat": past - 60, "exp": past},
        get_settings().app_secret,
        algorithm="HS256",
    )
    if isinstance(really_expired, bytes):
        really_expired = really_expired.decode("utf-8")
    assert verify_session(really_expired) is None, "expired token must not verify"

    print("[5] sign-in -> /auth/me with Bearer header")
    client = _bootstrap()
    email = "jwt-smoke@example.com"
    r = client.post(
        "/auth/sign-in",
        json={"email": email, "displayName": "JWT Smoke", "password": "jwtpass1234"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    api_token = body["token"]
    api_user = body["user"]
    print("    token_prefix=", api_token[:24], "user_id=", api_user["id"][:8])
    assert api_token.count(".") == 2, "expected JWT shape"
    # The JWT must NOT just be the user id.
    assert api_token != api_user["id"], "token should not be the raw uuid"

    # Verify the returned token resolves to the same user id.
    api_claims = verify_session(api_token)
    assert api_claims is not None, "API-issued token failed to verify"
    assert api_claims["sub"] == api_user["id"], (api_claims, api_user)

    print("[6] /auth/me Authorization: Bearer ...")
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {api_token}"})
    assert r.status_code == 200, r.text
    me = r.json()
    print("    email=", me["email"], "id_prefix=", me["id"][:8])
    assert me["email"] == email
    assert me["id"] == api_user["id"]

    print("[7] /auth/me with X-Account-Id only (no Bearer) -> 401 (header is NOT an auth source)")
    r = client.get("/auth/me", headers={"X-Account-Id": api_user["id"]})
    assert r.status_code == 401, r.text

    print("[8] /auth/me with bogus Bearer -> 401")
    r = client.get("/auth/me", headers={"Authorization": "Bearer not.a.real.jwt"})
    assert r.status_code == 401, r.text

    print("[9] /auth/me with no auth -> 401")
    r = client.get("/auth/me")
    assert r.status_code == 401, r.text

    print("ALL ASSERTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
