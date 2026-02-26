from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.config import get_settings
from app.storage.base import ArtifactRef, encode_local_path, encode_storage_key, parse_artifact_ref
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage


@lru_cache(maxsize=1)
def get_storage() -> Any:
    settings = get_settings()
    if settings.storage_provider == "s3":
        return S3Storage(
            bucket=settings.s3_bucket,
            region=settings.aws_region,
            prefix=settings.s3_prefix,
            endpoint_url=settings.s3_endpoint_url,
            force_path_style=settings.s3_force_path_style,
        )
    return LocalStorage(root=settings.storage_local_root)


__all__ = [
    "get_storage",
    "ArtifactRef",
    "parse_artifact_ref",
    "encode_local_path",
    "encode_storage_key",
]
