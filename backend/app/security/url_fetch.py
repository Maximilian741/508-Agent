"""SSRF-hardened fetch of a public web page's HTML (for the free URL scanner).

The free "scan a URL" feature fetches an attacker-influenced URL server-side, so
it is a textbook SSRF sink. This module is the single guarded entry point:

  * scheme allow-list (http/https only) — no file://, ftp://, gopher://, etc.
  * the host must resolve EXCLUSIVELY to globally-routable public IPs — every
    address returned by ``getaddrinfo`` is checked, so a name that resolves to
    both a public and a private IP is rejected. This blocks loopback
    (127.0.0.1, ::1), private ranges (10/8, 172.16/12, 192.168/16, fc00::/7),
    link-local (169.254/16 incl. the 169.254.169.254 cloud-metadata endpoint),
    CGNAT, reserved, multicast, and IPv4-mapped IPv6 forms of all of these.
  * redirects are followed MANUALLY (urllib auto-redirect disabled) so every
    hop's host is re-validated — a public URL can't 30x-bounce to an internal one.
  * embedded credentials (user:pass@host) are rejected.
  * response is size-capped (streamed, aborted past the cap), time-capped, and
    must look like HTML.

Residual risk (documented, accepted for v1): a sub-second DNS-rebinding flip
between our ``getaddrinfo`` validation and urllib's own resolution. Pinning the
socket to the validated IP while preserving TLS SNI/cert validation is not
cleanly expressible with ``urllib``; the tight window plus the per-hop
revalidation make this a low-probability vector for a read-only HTML fetch.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.request
from typing import Tuple
from urllib.parse import urljoin, urlparse

# Tunables — deliberately strict for a free, unauthenticated scan.
MAX_BYTES = 5 * 1024 * 1024  # 5 MB of HTML is plenty; abort past this
TIMEOUT_SECONDS = 10.0
MAX_REDIRECTS = 4
MAX_URL_LENGTH = 2048
_ALLOWED_SCHEMES = {"http", "https"}
_HTML_CONTENT_TYPES = {"text/html", "application/xhtml+xml", "text/plain", ""}
_USER_AGENT = "508Agent-AccessibilityScanner/1.0 (+https://508agent.app)"


class SsrfError(ValueError):
    """The target is not permitted (bad scheme, non-public address, credentials)."""


class UrlFetchError(RuntimeError):
    """The target is permitted but could not be fetched as HTML."""


def _addr_blocked(ip: "ipaddress._BaseAddress") -> bool:
    """True if an IP is anything other than a normal, globally-routable public IP."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # unwrap ::ffff:127.0.0.1 etc.
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or not ip.is_global
    )


def host_is_blocked(host: str) -> bool:
    """Resolve ``host`` and return True if ANY resolved address is non-public.

    Raises :class:`UrlFetchError` if the host cannot be resolved at all.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:  # pragma: no cover - DNS failure
        raise UrlFetchError("Could not resolve that host.") from exc
    if not infos:
        raise UrlFetchError("Could not resolve that host.")
    for info in infos:
        addr = info[4][0]
        # IPv6 scoped addresses can carry a %zone suffix — strip it.
        addr = addr.split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return True  # unparseable -> refuse, conservatively
        if _addr_blocked(ip):
            return True
    return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Disable urllib's automatic redirect following so we validate each hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def _validate_target(url: str) -> str:
    """Validate scheme/credentials/host of a single URL; return it normalized."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise SsrfError("Only http:// and https:// pages can be scanned.")
    if parsed.username or parsed.password:
        raise SsrfError("URLs with embedded credentials are not allowed.")
    host = parsed.hostname
    if not host:
        raise SsrfError("That URL has no host.")
    if host_is_blocked(host):
        raise SsrfError("Refusing to scan a private, loopback, or link-local address.")
    return url


def fetch_url_html(raw_url: str) -> Tuple[bytes, str]:
    """Fetch ``raw_url`` (a public web page) and return ``(html_bytes, final_url)``.

    Raises :class:`SsrfError` for a disallowed target and :class:`UrlFetchError`
    for a fetch failure (unreachable, too big, non-HTML, redirect loop).
    """
    url = (raw_url or "").strip()
    if not url:
        raise SsrfError("Please enter a URL.")
    if len(url) > MAX_URL_LENGTH:
        raise SsrfError("That URL is too long.")
    if "://" not in url:
        url = "https://" + url  # be forgiving: default to https

    opener = urllib.request.build_opener(_NoRedirect)
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        current = _validate_target(current)
        req = urllib.request.Request(
            current, headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*"}
        )
        try:
            resp = opener.open(req, timeout=TIMEOUT_SECONDS)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                location = exc.headers.get("Location") if exc.headers else None
                if not location:
                    raise UrlFetchError("The page redirected without a destination.")
                current = urljoin(current, location)  # resolve relative redirects
                continue
            raise UrlFetchError(f"The page returned HTTP {exc.code}.")
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            raise UrlFetchError("Could not reach that URL (timed out or unreachable).") from exc

        try:
            ctype = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if ctype not in _HTML_CONTENT_TYPES:
                raise UrlFetchError(f"That URL is not an HTML page (Content-Type: {ctype}).")
            data = resp.read(MAX_BYTES + 1)
        finally:
            resp.close()
        if len(data) > MAX_BYTES:
            raise UrlFetchError("That page is too large to scan (over 5 MB).")
        if not data.strip():
            raise UrlFetchError("That page returned no content.")
        return data, current

    raise UrlFetchError("Too many redirects.")
