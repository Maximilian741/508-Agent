"""Pytest session setup.

Pin a stable ``APP_SECRET`` before any test imports the app, so tests that
reload ``app.main`` under a non-development ``ENVIRONMENT`` (e.g. the storage
endpoint gating test) don't trip the "APP_SECRET is required" guard.
"""

import os

os.environ.setdefault("APP_SECRET", "pytest-stable-secret-not-for-production-use")
