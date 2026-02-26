from __future__ import annotations

import os
import importlib
from unittest.mock import patch
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.storage.base import encode_storage_key, parse_artifact_ref, sanitize_storage_key
from app.storage.local import LocalStorage
from app.storage.materialize import materialize_to_path
from app.storage.s3 import S3Storage


class StorageFoundationsTests(unittest.TestCase):
    def setUp(self) -> None:
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_sanitize_storage_key_blocks_traversal(self) -> None:
        with self.assertRaises(ValueError):
            sanitize_storage_key("../secrets.txt")
        with self.assertRaises(ValueError):
            sanitize_storage_key("/../../etc/passwd")

    def test_local_storage_save_and_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = LocalStorage(Path(tmp))
            payload = b"hello-world"
            meta = storage.save_bytes("documents/doc-1/original/file.pdf", payload, "application/pdf")
            self.assertEqual(meta.size_bytes, len(payload))
            self.assertTrue(storage.exists("documents/doc-1/original/file.pdf"))
            with storage.open_stream("documents/doc-1/original/file.pdf") as stream:
                self.assertEqual(stream.read(), payload)

    def test_artifact_ref_resolution(self) -> None:
        encoded = encode_storage_key("bundles/bundle-1/evidence.zip")
        parsed = parse_artifact_ref(encoded)
        self.assertEqual(parsed.type, "storage_key")
        self.assertEqual(parsed.value, "bundles/bundle-1/evidence.zip")

        legacy = parse_artifact_ref("C:/tmp/file.pdf")
        self.assertEqual(legacy.type, "local_path")
        self.assertEqual(legacy.value, "C:/tmp/file.pdf")

    def test_config_fails_fast_missing_s3_bucket(self) -> None:
        prior_provider = os.environ.get("STORAGE_PROVIDER")
        prior_bucket = os.environ.get("S3_BUCKET")
        try:
            os.environ["STORAGE_PROVIDER"] = "s3"
            if "S3_BUCKET" in os.environ:
                del os.environ["S3_BUCKET"]
            with self.assertRaises(RuntimeError):
                get_settings()
        finally:
            if prior_provider is None:
                os.environ.pop("STORAGE_PROVIDER", None)
            else:
                os.environ["STORAGE_PROVIDER"] = prior_provider
            if prior_bucket is None:
                os.environ.pop("S3_BUCKET", None)
            else:
                os.environ["S3_BUCKET"] = prior_bucket

    def test_s3_presigned_url_shape(self) -> None:
        try:
            import boto3  # noqa: F401
        except Exception:
            self.skipTest("boto3 not installed")

        class FakeClient:
            def generate_presigned_url(self, _op, Params, ExpiresIn):
                return f"http://example.local/{Params['Bucket']}/{Params['Key']}?exp={ExpiresIn}"

        with patch("boto3.client", return_value=FakeClient()):
            storage = S3Storage(bucket="bkt", region="us-east-1", prefix="pref", endpoint_url="http://localhost:9000")
            url = storage.get_download_url("bundles/id/evidence.zip", expires_seconds=1800)
            self.assertIn("bkt", url)
            self.assertIn("pref/bundles/id/evidence.zip", url)
            self.assertIn("exp=1800", url)

    def test_materialize_streaming_writes_chunks(self) -> None:
        class ChunkStream:
            def __init__(self):
                self._chunks = [b"ab", b"cd", b"ef", b""]

            def read(self, _size=-1):
                return self._chunks.pop(0)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeStorage:
            def open_stream(self, _key):
                return ChunkStream()

        with tempfile.TemporaryDirectory() as tmp:
            out = materialize_to_path(
                storage=FakeStorage(),
                artifact_ref="storage_key:documents/d1/original/file.pdf",
                base_dir=Path(tmp),
                scope="doc1",
                filename_hint="x.pdf",
            )
            self.assertTrue(out.exists())
            self.assertEqual(out.read_bytes(), b"abcdef")

    def test_storage_endpoint_gated_in_production(self) -> None:
        prior_env = os.environ.get("ENVIRONMENT")
        prior_flag = os.environ.get("ENABLE_DEV_STORAGE_ENDPOINT")
        prior_provider = os.environ.get("STORAGE_PROVIDER")
        try:
            os.environ["ENVIRONMENT"] = "production"
            os.environ["ENABLE_DEV_STORAGE_ENDPOINT"] = "false"
            os.environ["STORAGE_PROVIDER"] = "local"
            get_settings.cache_clear()
            import app.main as main_module
            main_module = importlib.reload(main_module)
            client = TestClient(main_module.app)
            resp = client.get("/storage/documents/a/file.pdf")
            self.assertIn(resp.status_code, {403, 404})
        finally:
            if prior_env is None:
                os.environ.pop("ENVIRONMENT", None)
            else:
                os.environ["ENVIRONMENT"] = prior_env
            if prior_flag is None:
                os.environ.pop("ENABLE_DEV_STORAGE_ENDPOINT", None)
            else:
                os.environ["ENABLE_DEV_STORAGE_ENDPOINT"] = prior_flag
            if prior_provider is None:
                os.environ.pop("STORAGE_PROVIDER", None)
            else:
                os.environ["STORAGE_PROVIDER"] = prior_provider
            get_settings.cache_clear()


if __name__ == "__main__":
    unittest.main()
