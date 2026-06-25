"""SSRF-hardened fetch of a public web page's HTML (for the free URL scanner).

The free "scan a URL" feature fetches an attacker-influenced URL server-side, so
it is a textbook SSRF sink. This module is the single guarded entry point.

Controls:
  * scheme allow-list (http/https only) — no file://, ftp://, gopher://, etc.
  * embedded credentials (user:pass@host) are rejected.
  * the host is resolved ONCE; EVERY resolved address must be a globally-routable
    public IP (loopback, private, link-local incl. 169.254.169.254, CGNAT,
    reserved, multicast, IPv4-mapped/compatible/NAT64/6to4 forms, and special-use
    anycast are all refused).
  * **the TCP connection is PINNED to the validated IP** (custom http.client
    connections) — we never let the stack re-resolve the hostname, which closes
    the DNS-rebinding TOCTOU (validate-then-reconnect) entirely. TLS SNI + cert
    validation still target the original hostname.
  * redirects are followed MANUALLY, re-validating + re-pinning every hop.
  * size-capped (5 MB), total-wall-clock-capped, HTML content-type required.
  * caller-facing errors are GENERIC (no upstream status code / content-type) so
    the endpoint can't be used as a blind-SSRF fingerprinting oracle; the
    specific reason is available to server logs via the exception chain.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from typing import Tuple
from urllib.parse import urljoin, urlparse

# Tunables — deliberately strict for a free, unauthenticated-ish scan.
MAX_BYTES = 5 * 1024 * 1024  # 5 MB of HTML is plenty; abort past this
TIMEOUT_SECONDS = 10.0       # per-socket connect/read timeout
TOTAL_BUDGET_SECONDS = 12.0  # hard wall-clock budget across ALL redirect hops
MAX_REDIRECTS = 4
MAX_URL_LENGTH = 2048
_ALLOWED_SCHEMES = {"http", "https"}
# Only genuine HTML markup types (""/missing kept for misconfigured HTML servers;
# text/plain dropped — a .txt is not a web page and only widens the scrape surface).
_HTML_CONTENT_TYPES = {"text/html", "application/xhtml+xml", ""}
_USER_AGENT = "508Agent-AccessibilityScanner/1.0 (+https://508agent.app)"
_REDIRECT_CODES = {301, 302, 303, 307, 308}

# Publicly-routable but special-use IPv4 anycast prefixes that ``is_global``
# leaves True — denied as defense-in-depth (not internal reach, but not a real
# scan target either).
_EXTRA_DENY_V4 = [
    ipaddress.ip_network(c)
    for c in (
        "192.88.99.0/24",   # 6to4 relay anycast (deprecated)
        "192.31.196.0/24",  # AS112-v4
        "192.52.193.0/24",  # AMT
        "192.175.48.0/24",  # AS112 direct delegation
        "192.0.0.0/24",     # IETF protocol assignments
    )
]


class SsrfError(ValueError):
    """The target is not permitted (bad scheme, non-public address, credentials)."""


class UrlFetchError(RuntimeError):
    """The target is permitted but could not be fetched as HTML (generic to caller)."""


def _addr_blocked(ip: "ipaddress._BaseAddress") -> bool:
    """True if an IP is anything other than a normal, globally-routable public IP."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # unwrap ::ffff:127.0.0.1 etc.
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or not ip.is_global
    ):
        return True
    if ip.version == 4 and any(ip in net for net in _EXTRA_DENY_V4):
        return True
    return False


def host_is_blocked(host: str) -> bool:
    """Resolve ``host`` and return True if ANY resolved address is non-public.

    Raises :class:`UrlFetchError` if the host cannot be resolved at all.
    """
    try:
        return _resolve_validated_ip(host) is None  # never None, but keep type-clean
    except SsrfError:
        return True


def _resolve_validated_ip(host: str) -> str:
    """Resolve ``host`` once; refuse if ANY address is non-public; return one
    validated public IP to PIN the connection to (closes DNS rebinding)."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UrlFetchError("Could not resolve that host.") from exc
    if not infos:
        raise UrlFetchError("Could not resolve that host.")
    chosen: str = ""
    for info in infos:
        addr = info[4][0].split("%", 1)[0]  # strip any IPv6 %zone
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            raise SsrfError("Refusing to scan that address.")
        if _addr_blocked(ip):
            raise SsrfError("Refusing to scan a private, loopback, or link-local address.")
        if not chosen:
            chosen = addr
    return chosen


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that dials a pre-validated IP (no re-resolution)."""

    def __init__(self, host: str, pinned_ip: str, **kw):
        super().__init__(host, **kw)
        self._pinned_ip = pinned_ip

    def connect(self):  # noqa: D401
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        if getattr(self, "_tunnel_host", None):
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection that dials a pre-validated IP while keeping SNI/cert on the host."""

    def __init__(self, host: str, pinned_ip: str, **kw):
        super().__init__(host, **kw)
        self._pinned_ip = pinned_ip

    def connect(self):  # noqa: D401
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        if getattr(self, "_tunnel_host", None):
            self.sock = sock
            self._tunnel()
            sock = self.sock
        # SNI + certificate hostname matching still target the real hostname.
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _validate_url(url: str) -> Tuple[str, str, int, str]:
    """Validate length/scheme/credentials/host; return (scheme, host, port, request_path)."""
    if len(url) > MAX_URL_LENGTH:
        raise SsrfError("That URL is too long.")
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise SsrfError("Only http:// and https:// pages can be scanned.")
    if parsed.username or parsed.password:
        raise SsrfError("URLs with embedded credentials are not allowed.")
    host = parsed.hostname
    if not host:
        raise SsrfError("That URL has no host.")
    port = parsed.port or (443 if scheme == "https" else 80)
    req_path = parsed.path or "/"
    if parsed.query:
        req_path += "?" + parsed.query
    return scheme, host, port, req_path


def fetch_url_html(raw_url: str) -> Tuple[bytes, str]:
    """Fetch ``raw_url`` (a public web page) and return ``(html_bytes, final_url)``.

    Raises :class:`SsrfError` for a disallowed target and :class:`UrlFetchError`
    (generic message) for any fetch failure.
    """
    url = (raw_url or "").strip()
    if not url:
        raise SsrfError("Please enter a URL.")
    if "://" not in url:
        url = "https://" + url  # be forgiving: default to https

    ssl_ctx = ssl.create_default_context()
    deadline = time.monotonic() + TOTAL_BUDGET_SECONDS
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        scheme, host, port, req_path = _validate_url(current)
        pinned_ip = _resolve_validated_ip(host)  # raises SsrfError on a non-public address
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise UrlFetchError("That URL took too long to fetch.")
        timeout = min(TIMEOUT_SECONDS, remaining)

        conn = (
            _PinnedHTTPSConnection(host, pinned_ip, port=port, timeout=timeout, context=ssl_ctx)
            if scheme == "https"
            else _PinnedHTTPConnection(host, pinned_ip, port=port, timeout=timeout)
        )
        try:
            conn.request(
                "GET",
                req_path,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,*/*",
                    "Accept-Encoding": "identity",  # no compression -> no decompress bomb
                },
            )
            resp = conn.getresponse()
            status = resp.status
            if status in _REDIRECT_CODES:
                location = resp.getheader("Location")
                if not location:
                    raise UrlFetchError("That page could not be fetched.")
                current = urljoin(current, location)
                continue  # re-validate + re-pin the next hop
            if status >= 400:
                # Generic — never leak the upstream status code (blind-SSRF oracle).
                raise UrlFetchError("That page could not be fetched.")
            ctype = (resp.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            if ctype not in _HTML_CONTENT_TYPES:
                raise UrlFetchError("That URL is not an HTML page.")
            data = resp.read(MAX_BYTES + 1)
        except (ssl.SSLError, socket.timeout, TimeoutError, OSError, http.client.HTTPException) as exc:
            raise UrlFetchError("Could not reach that URL.") from exc
        finally:
            conn.close()

        if len(data) > MAX_BYTES:
            raise UrlFetchError("That page is too large to scan.")
        if not data.strip():
            raise UrlFetchError("That page returned no content.")
        return data, current

    raise UrlFetchError("That URL has too many redirects.")
