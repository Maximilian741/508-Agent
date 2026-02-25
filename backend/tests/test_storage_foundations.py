from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.config import get_settings
from app.storage.base import encode_storage_key, parse_artifact_ref, sanitize_storage_key
from app.storage.local import LocalStorage


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


if __name__ == "__main__":
    unittest.main()
