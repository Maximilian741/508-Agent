from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO, Optional

from app.storage.base import StoredObject, sanitize_storage_key, utc_now


class S3Storage:
    def __init__(self, *, bucket: str, region: str, prefix: str = "", endpoint_url: str = "", force_path_style: bool = True) -> None:
        try:
            import boto3
        except Exception as exc:  # pragma: no cover - import guard
            raise RuntimeError("boto3 is required for STORAGE_PROVIDER=s3") from exc

        self._boto3 = boto3
        self.bucket = bucket
        self.region = region
        self.prefix = prefix.strip("/")
        self.endpoint_url = endpoint_url or None
        config = None
        if force_path_style:
            from botocore.config import Config

            config = Config(s3={"addressing_style": "path"})
        self.client = boto3.client("s3", region_name=region, endpoint_url=self.endpoint_url, config=config)

    def _full_key(self, key: str) -> str:
        clean = sanitize_storage_key(key)
        if self.prefix:
            return f"{self.prefix}/{clean}"
        return clean

    def _sha256_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(8192)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def save_bytes(self, key: str, data: bytes, content_type: Optional[str] = None) -> StoredObject:
        full_key = self._full_key(key)
        extra = {"ContentType": content_type} if content_type else {}
        self.client.put_object(Bucket=self.bucket, Key=full_key, Body=data, **extra)
        return StoredObject(
            key=sanitize_storage_key(key),
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            content_type=content_type,
            created_at=utc_now(),
        )

    def save_file(self, key: str, src_path: str, content_type: Optional[str] = None) -> StoredObject:
        src = Path(src_path)
        if not src.exists() or not src.is_file():
            raise FileNotFoundError(f"Source file not found: {src}")
        full_key = self._full_key(key)
        extra = {"ContentType": content_type} if content_type else {}
        self.client.upload_file(str(src), self.bucket, full_key, ExtraArgs=extra or None)
        return StoredObject(
            key=sanitize_storage_key(key),
            size_bytes=src.stat().st_size,
            sha256=self._sha256_file(src),
            content_type=content_type,
            created_at=utc_now(),
        )

    def open_stream(self, key: str) -> BinaryIO:
        full_key = self._full_key(key)
        response = self.client.get_object(Bucket=self.bucket, Key=full_key)
        body = response["Body"]
        return body

    def exists(self, key: str) -> bool:
        full_key = self._full_key(key)
        try:
            self.client.head_object(Bucket=self.bucket, Key=full_key)
            return True
        except Exception:
            return False

    def get_download_url(self, key: str, expires_seconds: int = 3600) -> str:
        full_key = self._full_key(key)
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": full_key},
            ExpiresIn=max(60, int(expires_seconds or 3600)),
        )

    def delete(self, key: str) -> None:
        full_key = self._full_key(key)
        self.client.delete_object(Bucket=self.bucket, Key=full_key)

    def list(self, prefix: str) -> list[str]:
        key_prefix = self._full_key(prefix)
        paginator = self.client.get_paginator("list_objects_v2")
        out: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=key_prefix):
            for item in page.get("Contents", []):
                key = str(item.get("Key") or "")
                if self.prefix and key.startswith(f"{self.prefix}/"):
                    key = key[len(self.prefix) + 1 :]
                out.append(key)
        return sorted(out)
