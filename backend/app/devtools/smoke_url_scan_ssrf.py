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

    # --- DNS-rebinding is closed: the host is resolved ONCE and the connection
    # is PINNED to that validated IP (no second, attacker-controlled resolution).
    import socket as _S

    import app.security.url_fetch as _uf
    from app.security.url_fetch import UrlFetchError

    state = {"gai": 0, "connected": None}
    real_gai, real_cc = _S.getaddrinfo, _S.create_connection
    try:
        def fake_gai(host, *a, **k):
            state["gai"] += 1
            # public on the first (validation) lookup; would rebind to loopback on any later one
            ip = "93.184.216.34" if state["gai"] == 1 else "127.0.0.1"
            return [(_S.AF_INET, _S.SOCK_STREAM, 6, "", (ip, 0))]

        def fake_cc(addr, *a, **k):
            state["connected"] = addr[0]
            raise OSError("smoke: no real egress")

        _S.getaddrinfo = fake_gai
        _S.create_connection = fake_cc
        try:
            _uf.fetch_url_html("http://rebind.example/")
        except UrlFetchError:
            pass  # the sentinel OSError from fake_cc surfaces as UrlFetchError
        except Exception as exc:
            check("rebind: only the sentinel error escapes", False, repr(exc))
        check("rebind: host resolved exactly once (no connect-time re-resolve)", state["gai"] == 1,
              f"getaddrinfo calls={state['gai']}")
        check("rebind: connected to the VALIDATED public IP, never the rebind target",
              state["connected"] == "93.184.216.34", str(state["connected"]))
    finally:
        _S.getaddrinfo, _S.create_connection = real_gai, real_cc

    # --- scan score is page-QUALITY (as-found), never the remediation 0/'F' ---
    from app.api.pipeline import _build_scan_score

    class _V:
        def __init__(self, sev):
            self.severity = sev

    check("score: clean page = 100/A+", _build_scan_score([]).score == 100.0)
    one_warn = _build_scan_score([_V("warning")])
    check("score: a single warning is NOT 0/'F'", one_warn.score >= 95 and one_warn.grade != "F",
          f"{one_warn.score}/{one_warn.grade}")
    few = _build_scan_score([_V("error"), _V("error"), _V("error")])
    check("score: a few errors -> mid range (not 0, not 100)", 0 < few.score < 100, str(few.score))
    many = _build_scan_score([_V("error")] * 30)
    check("score: a badly-broken page floors at 0", many.score == 0.0, str(many.score))
    check("score: scan never reports auto-fixes", _build_scan_score([_V("error")]).fixedAutomatically == 0)

    # --- site discovery: sitemap parsing is same-ORIGIN-locked (a hostile
    # sitemap must never aim our fetcher at third-party URLs) and bounded.
    from app.security.url_fetch import discover_site_urls, same_origin

    check("origin: same host+scheme+port", same_origin("https://a.com/x", "https://a.com/y"))
    check("origin: different host rejected", not same_origin("https://a.com/x", "https://evil.com/y"))
    check("origin: different scheme rejected", not same_origin("https://a.com/x", "http://a.com/y"))
    check("origin: different port rejected", not same_origin("https://a.com:8443/x", "https://a.com/y"))

    SITEMAP = (
        b'<?xml version="1.0"?><urlset>'
        b"<loc>https://site.example/</loc>"
        b"<loc>https://site.example/about</loc>"
        b"<loc>https://site.example/contact</loc>"
        b"<loc>http://site.example/insecure</loc>"       # scheme mismatch -> dropped
        b"<loc>https://evil.example/steal</loc>"          # cross-origin -> dropped
        b"<loc>https://site.example/about</loc>"          # duplicate -> dropped
        b"</urlset>"
    )
    fetched: list = []

    def fake_fetch(url, allowed, err):
        fetched.append(url)
        return SITEMAP, url

    real_fetch = _uf._fetch
    try:
        _uf._fetch = fake_fetch
        urls = discover_site_urls("https://site.example/", limit=10)
    finally:
        _uf._fetch = real_fetch

    check("sitemap: fetched /sitemap.xml at the site root",
          fetched and fetched[0] == "https://site.example/sitemap.xml", str(fetched[:1]))
    check("sitemap: seed URL is always first", urls[0] == "https://site.example/", str(urls[:1]))
    check("sitemap: cross-origin <loc> DROPPED (no fetch-proxy abuse)",
          not any("evil.example" in u for u in urls), str(urls))
    check("sitemap: scheme-mismatched <loc> dropped", not any(u.startswith("http://") for u in urls), str(urls))
    check("sitemap: same-origin pages discovered",
          "https://site.example/about" in urls and "https://site.example/contact" in urls, str(urls))
    check("sitemap: de-duplicated (seed + about + contact only)", len(urls) == 3, str(urls))

    def fake_fetch_many(url, allowed, err):
        locs = b"".join(f"<loc>https://site.example/p{i}</loc>".encode() for i in range(500))
        return b"<urlset>" + locs + b"</urlset>", url

    try:
        _uf._fetch = fake_fetch_many
        capped = discover_site_urls("https://site.example/", limit=10)
    finally:
        _uf._fetch = real_fetch
    check("sitemap: hard-capped at the requested limit", len(capped) == 10, str(len(capped)))

    def fake_fetch_missing(url, allowed, err):
        raise UrlFetchError("404")

    try:
        _uf._fetch = fake_fetch_missing
        seed_only = discover_site_urls("https://site.example/page", limit=10)
    finally:
        _uf._fetch = real_fetch
    check("sitemap: missing sitemap -> seed-only (not an error)",
          seed_only == ["https://site.example/page"], str(seed_only))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
