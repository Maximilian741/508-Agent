from __future__ import annotations

import os
import unittest

from app.storage.s3 import S3Storage


MINIO_ENDPOINT = os.getenv("MINIO_TEST_URL", "").strip() or os.getenv("S3_ENDPOINT_URL", "").strip()
MINIO_BUCKET = os.getenv("MINIO_TEST_BUCKET", "").strip() or os.getenv("S3_BUCKET", "").strip()


@unittest.skipUnless(MINIO_ENDPOINT and MINIO_BUCKET, "MINIO_TEST_URL/S3_ENDPOINT_URL and bucket not set")
class MinioStorageIntegrationTests(unittest.TestCase):
    def test_put_exists_and_presign(self) -> None:
        storage = S3Storage(
            bucket=MINIO_BUCKET,
            region=os.getenv("AWS_REGION", "us-east-1"),
            prefix=os.getenv("S3_PREFIX", ""),
            endpoint_url=MINIO_ENDPOINT,
            force_path_style=True,
        )
        key = "integration/minio-test.txt"
        storage.save_bytes(key, b"hello-minio", content_type="text/plain")
        self.assertTrue(storage.exists(key))
        url = storage.get_download_url(key, expires_seconds=600)
        self.assertIn("http", url)
        self.assertTrue(len(url) > 20)


if __name__ == "__main__":
    unittest.main()

