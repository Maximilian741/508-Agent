"""Smoke: SSRF guard for the free URL accessibility scanner.

The /pipeline/analyze-url endpoint fetches an attacker-influenced URL
server-side, so the SSRF guard in app.security.url_fetch is the crown-jewel
control. This pins it WITHOUT any real network egress: it exercises only IP
literals and pre-resolution rejections (bad scheme, credentials, length), which
resolve locally / fail fast before any socket is opened.

Usage:
    python -m app.devtools.smoke_url_scan_ssrf
"""

from __future__ import annotations

import ipaddress
import os
import sys
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_ssrf_')}/s.db")

from app.security.url_fetch import (  # noqa: E402
    SsrfError,
    _addr_blocked,
    fetch_url_html,
    host_is_blocked,
)

# IPs that MUST be refused (internal / non-public).
BLOCKED_IPS = [
    "127.0.0.1", "127.10.20.30",          # loopback
    "10.0.0.1", "172.16.5.5", "192.168.1.1",  # RFC1918 private
    "169.254.169.254",                     # link-local (cloud metadata!)
    "100.64.0.1",                          # CGNAT
    "0.0.0.0",                             # unspecified
    "::1",                                 # IPv6 loopback
    "fc00::1", "fd12:3456::1",             # IPv6 unique-local
    "fe80::1",                             # IPv6 link-local
    "::ffff:127.0.0.1",                    # IPv4-mapped loopback
    "::ffff:10.0.0.1",                     # IPv4-mapped private
    "224.0.0.1",                           # multicast
]

# IPs that should be ALLOWED (genuinely public).
ALLOWED_IPS = [
    "8.8.8.8", "1.1.1.1", "93.184.216.34",  # public v4
    "2606:4700:4700::1111",                  # public v6 (Cloudflare)
    "::ffff:8.8.8.8",                        # IPv4-mapped public -> unwrap -> public
]

# URLs rejected as SSRF BEFORE any network call (scheme/credentials/host/length).
BLOCKED_URLS = [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "gopher://evil/x",
    "http://127.0.0.1/",
    "http://localhost/",
    "https://localhost:8443/admin",
    "http://10.0.0.1/",
    "http://192.168.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://0.0.0.0/",
    "http://user:pass@8.8.8.8/",   # embedded credentials
    "http://",                      # no host
    "x" * 3000,                     # over length cap
    "",                             # empty
]


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    # --- IP classification ---
    for ip in BLOCKED_IPS:
        check(f"blocked IP: {ip}", _addr_blocked(ipaddress.ip_address(ip)))
    for ip in ALLOWED_IPS:
        check(f"allowed IP: {ip}", not _addr_blocked(ipaddress.ip_address(ip)))

    # --- host resolution gate (IP literals resolve locally, no network) ---
    check("host_is_blocked(127.0.0.1) True", host_is_blocked("127.0.0.1"))
    check("host_is_blocked(169.254.169.254) True", host_is_blocked("169.254.169.254"))
    check("host_is_blocked(8.8.8.8) False", not host_is_blocked("8.8.8.8"))

    # --- fetch_url_html rejects disallowed targets before any egress ---
    for url in BLOCKED_URLS:
        try:
            fetch_url_html(url)
            check(f"reject: {url[:48]!r}", False, "did not raise")
        except SsrfError:
            check(f"reject (SsrfError): {url[:48]!r}", True)
        except Exception as exc:  # any other exception = wrong/leaky handling
            check(f"reject: {url[:48]!r}", False, f"raised {type(exc).__name__}: {exc}")

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
