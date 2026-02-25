from __future__ import annotations

import hashlib
import mimetypes
import shutil
from pathlib import Path
from typing import BinaryIO, Optional

from app.storage.base import StoredObject, sanitize_storage_key, utc_now


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, key: str) -> Path:
        clean = sanitize_storage_key(key)
        path = (self.root / clean).resolve()
        if self.root not in path.parents and path != self.root:
            raise ValueError("Resolved storage path escaped root")
        return path

    def resolve_local_path(self, key: str) -> Path:
        return self._resolve_path(key)

    def _sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(8192)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def save_bytes(self, key: str, data: bytes, content_type: Optional[str] = None) -> StoredObject:
        path = self._resolve_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return StoredObject(
            key=sanitize_storage_key(key),
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            content_type=content_type or mimetypes.guess_type(path.name)[0],
            created_at=utc_now(),
        )

    def save_file(self, key: str, src_path: str, content_type: Optional[str] = None) -> StoredObject:
        src = Path(src_path)
        if not src.exists() or not src.is_file():
            raise FileNotFoundError(f"Source file not found: {src}")
        dest = self._resolve_path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return StoredObject(
            key=sanitize_storage_key(key),
            size_bytes=dest.stat().st_size,
            sha256=self._sha256(dest),
            content_type=content_type or mimetypes.guess_type(dest.name)[0],
            created_at=utc_now(),
        )

    def open_stream(self, key: str) -> BinaryIO:
        return self._resolve_path(key).open("rb")

    def exists(self, key: str) -> bool:
        return self._resolve_path(key).exists()

    def get_download_url(self, key: str, expires_seconds: int = 3600) -> str:
        clean = sanitize_storage_key(key)
        return f"/storage/{clean}"

    def delete(self, key: str) -> None:
        path = self._resolve_path(key)
        if path.exists():
            path.unlink()

    def list(self, prefix: str) -> list[str]:
        clean_prefix = sanitize_storage_key(prefix) if prefix else ""
        base = self._resolve_path(clean_prefix) if clean_prefix else self.root
        if not base.exists():
            return []
        out: list[str] = []
        for item in base.rglob("*"):
            if item.is_file():
                out.append(item.relative_to(self.root).as_posix())
        return sorted(out)
