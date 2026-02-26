from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List


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
    require_strict_cors: bool

    @property
    def max_upload_bytes(self) -> int:
        return max(1, int(self.max_upload_mb)) * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    backend_root = Path(__file__).resolve().parents[1]
    default_db_url = "sqlite:///./.runtime/508_agent.db"
    default_storage_root = backend_root / ".runtime" / "storage"
    default_materialized_root = backend_root / ".runtime" / "materialized"

    environment = (os.getenv("ENVIRONMENT") or os.getenv("APP_ENV") or "development").strip().lower() or "development"
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
        require_strict_cors=_env_bool("REQUIRE_STRICT_CORS", True),
    )

    if settings.storage_provider not in {"local", "s3"}:
        raise RuntimeError("STORAGE_PROVIDER must be 'local' or 's3'.")
    if settings.storage_provider == "s3" and not settings.s3_bucket:
        raise RuntimeError("S3_BUCKET is required when STORAGE_PROVIDER=s3.")
    if settings.environment == "production" and settings.require_strict_cors:
        if "*" in settings.cors_allow_origins:
            raise RuntimeError("CORS_ALLOW_ORIGINS cannot include '*' in production.")
    return settings
