"""Smoke: launch-hardening guards (security fixes for public launch).

Pins three pre-launch fixes so they can't regress:
1. Rate limiting now covers /billing and /teams (invite/email-spam + Stripe-cost
   surface), while /billing/webhook stays exempt (Stripe retries on non-2xx).
2. The Cloudflare-Access bypass set keeps the always-public endpoints reachable
   even if CF Access is enabled: /healthz, /readyz, /billing/webhook, and the
   public /billing/certificate verification path.
3. OVERAGE_TEST_MODE (grants credits off a synthetic charge) is IGNORED in
   production, so a leaked/mis-set env var can't hand out free credits.

Usage:
    python -m app.devtools.smoke_launch_hardening
"""

from __future__ import annotations

import os
import sys
import tempfile
import types

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_harden_')}/s.db"


def main() -> int:
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    from app.security.rate_limit import _is_rate_limited_path as limited

    # 1. Rate-limit coverage
    check("/billing/checkout is rate-limited", limited("/billing/checkout"))
    check("/billing/issue-certificate is rate-limited", limited("/billing/issue-certificate"))
    check("/teams is rate-limited", limited("/teams"))
    check("/teams/invite is rate-limited", limited("/teams/invite"))
    check("/billing/webhook is EXEMPT (Stripe retries)", not limited("/billing/webhook"))
    check("/auth/sign-in still limited", limited("/auth/sign-in"))
    check("/documents/upload still limited", limited("/documents/upload"))
    check("/pipeline/analyze still limited", limited("/pipeline/analyze"))
    check("unrelated path not limited", not limited("/jobs/123"))

    # 2. CF Access bypass set (always-public endpoints)
    from app.security import _AUTH_BYPASS_PATHS, _AUTH_BYPASS_PREFIXES

    def bypassed(path: str) -> bool:
        return path in _AUTH_BYPASS_PATHS or path.startswith(_AUTH_BYPASS_PREFIXES)

    check("healthz bypasses CF Access", bypassed("/healthz"))
    check("readyz bypasses CF Access", bypassed("/readyz"))
    check("stripe webhook bypasses CF Access", bypassed("/billing/webhook"))
    check("public cert verify bypasses CF Access", bypassed("/billing/certificate/abc123"))
    check("an authenticated route does NOT bypass", not bypassed("/pipeline/analyze"))

    # 3. OVERAGE_TEST_MODE ignored in production
    import app.config as _config
    from app.api.stripe_billing import _charge_overage

    _real = _config.get_settings
    os.environ["OVERAGE_TEST_MODE"] = "succeed"
    try:
        _config.get_settings = lambda: types.SimpleNamespace(environment="development")
        dev = _charge_overage("cus_x", "user_x")
        check("dev: OVERAGE_TEST_MODE=succeed yields a synthetic charge", isinstance(dev, str) and dev.startswith("pi_test_"))

        _config.get_settings = lambda: types.SimpleNamespace(environment="production")
        prod = _charge_overage("cus_x", "user_x")
        check("prod: OVERAGE_TEST_MODE ignored (no free credits)", prod is None)
    finally:
        _config.get_settings = _real
        os.environ.pop("OVERAGE_TEST_MODE", None)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
