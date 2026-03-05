from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import BinaryIO, Optional, Protocol


LOCAL_PATH_PREFIX = "local_path:"
STORAGE_KEY_PREFIX = "storage_key:"


@dataclass(frozen=True)
class StoredObject:
    key: str
    size_bytes: int
    sha256: str
    content_type: Optional[str]
    created_at: str


@dataclass(frozen=True)
class ArtifactRef:
    type: str
    value: str


class StorageProvider(Protocol):
    def save_bytes(self, key: str, data: bytes, content_type: Optional[str] = None) -> StoredObject:
        ...

    def save_file(self, key: str, src_path: str, content_type: Optional[str] = None) -> StoredObject:
        ...

    def open_stream(self, key: str) -> BinaryIO:
        ...

    def exists(self, key: str) -> bool:
        ...

    def get_download_url(self, key: str, expires_seconds: int = 3600) -> str:
        ...

    def delete(self, key: str) -> None:
        ...

    def list(self, prefix: str) -> list[str]:
        ...


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sanitize_storage_key(key: str) -> str:
    normalized = str(PurePosixPath(key)).strip().lstrip("/")
    if not normalized or normalized in {".", ".."}:
        raise ValueError("Invalid storage key")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise ValueError("Storage key cannot contain path traversal")
    return "/".join(parts)


def encode_local_path(path: str) -> str:
    return f"{LOCAL_PATH_PREFIX}{path}"


def encode_storage_key(key: str) -> str:
    return f"{STORAGE_KEY_PREFIX}{sanitize_storage_key(key)}"


def parse_artifact_ref(value: object) -> ArtifactRef:
    raw = str(value or "").strip()
    if raw.startswith(STORAGE_KEY_PREFIX):
        return ArtifactRef(type="storage_key", value=raw[len(STORAGE_KEY_PREFIX) :])
    if raw.startswith(LOCAL_PATH_PREFIX):
        return ArtifactRef(type="local_path", value=raw[len(LOCAL_PATH_PREFIX) :])
    return ArtifactRef(type="local_path", value=raw)
