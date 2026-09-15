from __future__ import annotations

import logging
import os
import secrets as _secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

_log = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except Exception:
        return default


def _env_list(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name)
    if raw is None:
        return default
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items or default


@dataclass(frozen=True)
class Settings:
    environment: str
    app_version: str
    database_url: str
    storage_provider: str
    storage_local_root: Path
    aws_region: str
    s3_bucket: str
    s3_prefix: str
    s3_endpoint_url: str
    s3_force_path_style: bool
    presign_expires_seconds: int
    enable_dev_storage_endpoint: bool
    materialized_root: Path
    cors_allow_origins: List[str]
    max_upload_mb: int
    # OCR for scanned PDFs: requires a host Tesseract install (see
    # docs/LAUNCH.md). Off by default; the action degrades to manual review.
    ocr_enabled: bool
    require_strict_cors: bool
    # Only trust client-supplied forwarded-IP headers (CF-Connecting-IP /
    # X-Forwarded-For) when actually behind a trusted proxy, otherwise an
    # attacker can spoof them to evade per-IP rate limiting.
    trust_proxy_headers: bool
    app_secret: str
    session_ttl_seconds: int
    pipeline_artifact_ttl_seconds: int
    cloudflare_access_team_domain: str
    cloudflare_access_aud: str
    admin_emails: List[str]
    # Cap the AI spend per single remediation job, in USD. When the cap
    # is hit mid-job the SemanticInferenceClient falls back to heuristics
    # for the remaining calls so a runaway image-heavy document cannot
    # blow your margin. Set to 0 to disable the cap.
    max_ai_cost_per_job_usd: float

    @property
    def max_upload_bytes(self) -> int:
        return max(1, int(self.max_upload_mb)) * 1024 * 1024

    def is_admin(self, email: Optional[str]) -> bool:
        """Is ``email`` listed in ``ADMIN_EMAILS``? List membership ONLY.

        Never sufficient on its own — a user can type any unclaimed address.
        Authorize with ``app.api.deps.is_admin_user``, which also requires
        server-side promotion and a verified address.
        """
        if not email:
            return False
        normalized = email.strip().lower()
        if not normalized:
            return False
        return any(normalized == admin.strip().lower() for admin in self.admin_emails)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    backend_root = Path(__file__).resolve().parents[1]
    default_db_url = "sqlite:///./.runtime/508_agent.db"
    default_storage_root = backend_root / ".runtime" / "storage"
    default_materialized_root = backend_root / ".runtime" / "materialized"

    environment = (os.getenv("ENVIRONMENT") or os.getenv("APP_ENV") or "development").strip().lower() or "development"

    raw_secret = (os.getenv("APP_SECRET") or "").strip()
    is_dev = environment == "development"
    if not raw_secret:
        if not is_dev:
            # Any non-development deployment (production, staging, prod, ...)
            # MUST pin a stable secret. A per-process random secret would give
            # each uvicorn worker a different key, so session JWTs and signed
            # download URLs would randomly fail to verify across workers/restarts.
            raise RuntimeError(
                "APP_SECRET is required outside development "
                f"(ENVIRONMENT={environment!r}). Generate one with: "
                "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        raw_secret = _secrets.token_urlsafe(48)
        _log.warning(
            "[config] APP_SECRET not set — generated a per-process random secret for dev mode. "
            "Signed URLs and sessions will become invalid on backend restart."
        )
    elif not is_dev and len(raw_secret) < 16:
        raise RuntimeError(
            "APP_SECRET is too short; use at least 32 random characters in non-development environments."
        )

    settings = Settings(
        environment=environment,
        app_version=os.getenv("APP_VERSION", "0.1.0").strip() or "0.1.0",
        database_url=os.getenv("DATABASE_URL", default_db_url).strip() or default_db_url,
        storage_provider=os.getenv("STORAGE_PROVIDER", "local").strip().lower() or "local",
        storage_local_root=Path(os.getenv("STORAGE_LOCAL_ROOT", str(default_storage_root))).resolve(),
        aws_region=os.getenv("AWS_REGION", "us-east-1").strip() or "us-east-1",
        s3_bucket=os.getenv("S3_BUCKET", "").strip(),
        s3_prefix=os.getenv("S3_PREFIX", "").strip(),
        s3_endpoint_url=os.getenv("S3_ENDPOINT_URL", "").strip(),
        s3_force_path_style=_env_bool("S3_FORCE_PATH_STYLE", True),
        presign_expires_seconds=max(60, _env_int("PRESIGN_EXPIRES_SECONDS", 3600)),
        enable_dev_storage_endpoint=_env_bool("ENABLE_DEV_STORAGE_ENDPOINT", False),
        materialized_root=Path(os.getenv("MATERIALIZED_ROOT", str(default_materialized_root))).resolve(),
        cors_allow_origins=_env_list(
            "CORS_ALLOW_ORIGINS",
            [
                "http://localhost:8081",
                "http://127.0.0.1:8081",
                "http://localhost:8080",
                "http://127.0.0.1:8080",
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                "http://localhost:19006",
                "http://127.0.0.1:19006",
            ],
        ),
        max_upload_mb=_env_int("MAX_UPLOAD_MB", 25),
        ocr_enabled=_env_bool("OCR_ENABLED", False),
        require_strict_cors=_env_bool("REQUIRE_STRICT_CORS", True),
        trust_proxy_headers=_env_bool("TRUST_PROXY_HEADERS", True),
        app_secret=raw_secret,
        session_ttl_seconds=max(300, _env_int("SESSION_TTL_SECONDS", 7 * 24 * 3600)),
        pipeline_artifact_ttl_seconds=max(60, _env_int("PIPELINE_ARTIFACT_TTL_SECONDS", 86400)),
        cloudflare_access_team_domain=os.getenv("CLOUDFLARE_ACCESS_TEAM_DOMAIN", "").strip(),
        cloudflare_access_aud=os.getenv("CLOUDFLARE_ACCESS_AUD", "").strip(),
        admin_emails=_env_list("ADMIN_EMAILS", []),
        max_ai_cost_per_job_usd=_env_float("MAX_AI_COST_PER_JOB_USD", 0.50),
    )

    if settings.storage_provider not in {"local", "s3"}:
        raise RuntimeError("STORAGE_PROVIDER must be 'local' or 's3'.")
    if settings.storage_provider == "s3" and not settings.s3_bucket:
        raise RuntimeError("S3_BUCKET is required when STORAGE_PROVIDER=s3.")
    if settings.environment == "production" and settings.require_strict_cors:
        if "*" in settings.cors_allow_origins:
            raise RuntimeError("CORS_ALLOW_ORIGINS cannot include '*' in production.")
    return settings
  