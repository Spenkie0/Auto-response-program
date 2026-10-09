from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import ipaddress
from pathlib import Path
import socket
import threading
from contextlib import AbstractContextManager
from urllib.parse import urlsplit
import urllib.error
import urllib.request

from .models import DownloadIngestionError


@dataclass(frozen=True)
class DownloadFetchResult:
    """Describe a bounded web download and its provenance metadata."""

    source_url: str
    final_url: str
    path: Path | None
    content_type: str
    content_disposition: str | None
    size: int
    sha256: str
    payload: bytes | None = None
    redirect_chain: tuple[str, ...] = ()
    url_extension: str | None = None
    filename: str | None = None


class _NullContext(AbstractContextManager):
    def __enter__(self):
        return None
    def __exit__(self, exc_type, exc, tb):
        return False


class _DNSPinningContext(AbstractContextManager):
    """Pin validated DNS answers for the complete urllib request/redirect chain."""

    def __init__(self, *, allow_private_ips: bool, allowed_ip_ranges: tuple[ipaddress._BaseNetwork, ...]) -> None:
        self.allow_private_ips = allow_private_ips
        self.allowed_ip_ranges = allowed_ip_ranges
        self.pins: dict[str, list[tuple]] = {}
        self._original = socket.getaddrinfo
        self._lock = threading.RLock()

    def __enter__(self):
        original = self._original
        pins = self.pins
        policy = self

        def pinned_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            key = str(host).rstrip(".").lower() if host is not None else ""
            with policy._lock:
                pinned = pins.get(key)
            if pinned is None:
                return original(host, port, family, type, proto, flags)
            result = []
            for family_v, type_v, proto_v, canonname_v, sockaddr_v in pinned:
                addr = sockaddr_v[0]
                if family and family_v != family:
                    continue
                if type and type_v != type:
                    continue
                if proto and proto_v != proto:
                    continue
                if isinstance(sockaddr_v, tuple):
                    sockaddr = (addr, port, *sockaddr_v[2:]) if len(sockaddr_v) > 2 else (addr, port)
                else:
                    sockaddr = (addr, port)
                result.append((family_v, type_v, proto_v, canonname_v, sockaddr))
            if result:
                return result
            return original(host, port, family, type, proto, flags)

        socket.getaddrinfo = pinned_getaddrinfo  # type: ignore[assignment]
        return self

    def __exit__(self, exc_type, exc, tb):
        socket.getaddrinfo = self._original  # type: ignore[assignment]
        return False

    def validate_and_pin(self, host: str) -> None:
        key = host.rstrip(".").lower()
        if key in self.pins:
            return
        try:
            infos = self._original(host, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise DownloadIngestionError("DOWNLOAD_DNS_FAILED", f"Could not resolve download host {host!r}: {exc}") from exc
        normalized = []
        for info in infos:
            try:
                address = ipaddress.ip_address(info[4][0])
            except (ValueError, IndexError, TypeError):
                continue
            if not self.allow_private_ips and not any(address in network for network in self.allowed_ip_ranges):
                if address.is_loopback or address.is_private or address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified:
                    continue
            normalized.append(info)
        if not normalized:
            raise DownloadIngestionError("DOWNLOAD_DNS_EMPTY", f"Download host {host!r} has no address allowed by policy.")
        self.pins[host.rstrip(".").lower()] = normalized


class _DownloadRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow redirects only when each new destination satisfies the same URL/IP policy."""

    def __init__(
        self,
        page_origin: str | None,
        allowed_hosts: set[str],
        max_redirects: int,
        *,
        allow_private_ips: bool,
        allowed_ip_ranges: tuple[ipaddress._BaseNetwork, ...],
        dns_pins: _DNSPinningContext | None = None,
        allowed_schemes: set[str] | None = None,
    ) -> None:
        super().__init__()
        self.page_origin = page_origin
        self.allowed_hosts = allowed_hosts
        self.max_redirects = max_redirects
        self.allow_private_ips = allow_private_ips
        self.allowed_ip_ranges = allowed_ip_ranges
        self.dns_pins = dns_pins
        self.allowed_schemes = {str(item).lower() for item in (allowed_schemes or {"http", "https"})}
        self.count = 0
        self.chain: list[str] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        if self.count > self.max_redirects:
            raise DownloadIngestionError("DOWNLOAD_TOO_MANY_REDIRECTS", f"Download exceeded the configured redirect limit of {self.max_redirects}.")
        destination = urlsplit(newurl)
        if destination.scheme.lower() not in self.allowed_schemes:
            raise DownloadIngestionError("DOWNLOAD_REDIRECT_UNSUPPORTED_SCHEME", f"Redirected to unsupported scheme: {destination.scheme!r}")
        host = (destination.hostname or "").lower().rstrip(".")
        origin = f"{destination.scheme.lower()}://{destination.netloc.lower()}"
        if self.page_origin and origin != self.page_origin and host not in self.allowed_hosts:
            raise DownloadIngestionError("DOWNLOAD_REDIRECT_NOT_TRUSTED", f"Download redirected to a host that is not trusted: {newurl}")
        if self.dns_pins is not None:
            self.dns_pins.validate_and_pin(host)
        else:
            _validate_host_resolution(
                host,
                allow_private_ips=self.allow_private_ips,
                allowed_ip_ranges=self.allowed_ip_ranges,
            )
        self.chain.append(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _origin(url: str) -> str:
    """Return a normalized scheme/host/port origin string."""
    parsed = urlsplit(url)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _parse_ip_ranges(values: list[str] | tuple[str, ...] | None) -> tuple[ipaddress._BaseNetwork, ...]:
    ranges: list[ipaddress._BaseNetwork] = []
    for value in values or ():
        try:
            ranges.append(ipaddress.ip_network(str(value).strip(), strict=False))
        except ValueError as exc:
            raise DownloadIngestionError("DOWNLOAD_CONFIG_INVALID_IP_RANGE", f"Invalid configured download IP range: {value!r}") from exc
    return tuple(ranges)


def _validate_host_resolution(
    host: str,
    *,
    allow_private_ips: bool,
    allowed_ip_ranges: tuple[ipaddress._BaseNetwork, ...] = (),
) -> tuple[str, ...]:
    """Resolve a download hostname and reject loopback/private/link-local targets by policy."""
    if not host:
        raise DownloadIngestionError("DOWNLOAD_HOST_MISSING", "Download URL does not contain a hostname.")
    try:
        direct = ipaddress.ip_address(host)
        addresses = {direct}
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise DownloadIngestionError("DOWNLOAD_DNS_FAILED", f"Could not resolve download host {host!r}: {exc}") from exc
        addresses = {ipaddress.ip_address(info[4][0]) for info in infos if info[4]}
    if not addresses:
        raise DownloadIngestionError("DOWNLOAD_DNS_EMPTY", f"Download host {host!r} did not resolve to an address.")
    if not allow_private_ips:
        for address in addresses:
            allowed_by_range = any(address in network for network in allowed_ip_ranges)
            if allowed_by_range:
                continue
            if address.is_loopback or address.is_private or address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified:
                raise DownloadIngestionError(
                    "DOWNLOAD_PRIVATE_IP_BLOCKED",
                    f"Download host {host!r} resolves to a non-public address {address}; private destinations are blocked by policy.",
                )
    return tuple(sorted(str(address) for address in addresses))


def is_trusted_download_url(
    url: str,
    page_url: str,
    *,
    same_origin_only: bool = True,
    allowed_hosts: set[str] | None = None,
    allow_private_ips: bool = False,
    allowed_ip_ranges: list[str] | tuple[str, ...] | None = None,
    allowed_schemes: set[str] | None = None,
) -> bool:
    """Return whether a candidate URL satisfies scheme/origin and DNS destination policy."""
    allowed = {host.strip().lower().rstrip(".") for host in (allowed_hosts or set()) if host.strip()}
    destination = urlsplit(url)
    if destination.scheme.lower() not in {str(item).lower() for item in (allowed_schemes or {"http", "https"})}:
        return False
    host = (destination.hostname or "").lower().rstrip(".")
    if not host:
        return False
    if host not in allowed and same_origin_only and page_url and _origin(url) != _origin(page_url):
        return False
    try:
        ranges = _parse_ip_ranges(list(allowed_ip_ranges or []))
        _validate_host_resolution(host, allow_private_ips=allow_private_ips, allowed_ip_ranges=ranges)
    except DownloadIngestionError:
        return False
    return True


def _build_download_opener(
    page_url: str,
    allowed_hosts: set[str],
    max_redirects: int,
    *,
    allow_private_ips: bool,
    allowed_ip_ranges: tuple[ipaddress._BaseNetwork, ...],
    dns_pins: _DNSPinningContext | None = None,
    allowed_schemes: set[str] | None = None,
) -> tuple[urllib.request.OpenerDirector, _DownloadRedirectHandler]:
    """Build the HTTP opener and retain its redirect chain for provenance."""
    allowed_origin = _origin(page_url) if page_url else None
    handler = _DownloadRedirectHandler(
        allowed_origin,
        allowed_hosts,
        max_redirects,
        allow_private_ips=allow_private_ips,
        allowed_ip_ranges=allowed_ip_ranges,
        dns_pins=dns_pins,
        allowed_schemes=allowed_schemes,
    )
    return urllib.request.build_opener(handler), handler


def _open_download(
    url: str,
    *,
    page_url: str,
    max_size_mb: int,
    timeout: int,
    same_origin_only: bool,
    allowed_hosts: set[str],
    max_redirects: int,
    user_agent: str,
    allow_private_ips: bool,
    allowed_ip_ranges: tuple[ipaddress._BaseNetwork, ...],
    max_header_bytes: int,
    allowed_schemes: set[str],
    dns_pin: bool,
    max_url_length: int = 4096,
) -> tuple[object, str, int, _DownloadRedirectHandler]:
    """Open and trust-check a download response before bytes are consumed."""
    parsed = urlsplit(url)
    if len(url) > max_url_length:
        raise DownloadIngestionError("DOWNLOAD_URL_TOO_LONG", f"Download URL exceeds the configured {max_url_length}-character limit.")
    if parsed.scheme.lower() not in allowed_schemes:
        raise DownloadIngestionError("DOWNLOAD_UNSUPPORTED_SCHEME", f"Unsupported download URL scheme: {parsed.scheme!r}")
    if not is_trusted_download_url(
        url,
        page_url,
        same_origin_only=same_origin_only,
        allowed_hosts=allowed_hosts,
        allow_private_ips=allow_private_ips,
        allowed_ip_ranges=[str(item) for item in allowed_ip_ranges],
        allowed_schemes=allowed_schemes,
    ):
        raise DownloadIngestionError("DOWNLOAD_NOT_TRUSTED", f"Candidate download is outside the configured origin/host/IP policy: {url}")

    max_bytes = int(max_size_mb) * 1024 * 1024
    pin_context = _DNSPinningContext(allow_private_ips=allow_private_ips, allowed_ip_ranges=allowed_ip_ranges) if dns_pin else None
    context_manager = pin_context if pin_context is not None else _NullContext()
    with context_manager as pins:
        if pins is not None:
            pins.validate_and_pin(parsed.hostname or "")
        opener, handler = _build_download_opener(
            page_url,
            allowed_hosts,
            max_redirects,
            allow_private_ips=allow_private_ips,
            allowed_ip_ranges=allowed_ip_ranges,
            dns_pins=pins,
        )
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": user_agent,
                "Accept": "application/octet-stream,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.oasis.opendocument.text,text/plain,text/csv,image/*,*/*;q=0.5",
                "Referer": page_url or url,
            },
            method="GET",
        )
        try:
            response = opener.open(request, timeout=timeout)
        except DownloadIngestionError:
            raise
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            raise DownloadIngestionError("DOWNLOAD_FAILED", f"Could not download {url}: {exc}") from exc

    final_url = response.geturl()
    if not is_trusted_download_url(
        final_url,
        page_url,
        same_origin_only=same_origin_only,
        allowed_hosts=allowed_hosts,
        allow_private_ips=allow_private_ips,
        allowed_ip_ranges=[str(item) for item in allowed_ip_ranges],
        allowed_schemes=allowed_schemes,
    ):
        response.close()
        raise DownloadIngestionError("DOWNLOAD_FINAL_NOT_TRUSTED", f"Final download URL is not trusted: {final_url}")

    header_size = sum(len(str(key)) + len(str(value)) for key, value in response.headers.items())
    if header_size > max_header_bytes:
        response.close()
        raise DownloadIngestionError("DOWNLOAD_HEADERS_TOO_LARGE", f"Response headers exceed the configured {max_header_bytes} byte limit.")
    declared_length = response.headers.get("Content-Length")
    if declared_length and declared_length.isdigit() and int(declared_length) > max_bytes:
        response.close()
        raise DownloadIngestionError("DOWNLOAD_TOO_LARGE", f"Server reports a download larger than the {max_size_mb} MB limit.")
    return response, final_url, max_bytes, handler


def _consume_response(response, max_bytes: int) -> tuple[bytes, int, str]:
    """Read a bounded response into memory, calculating SHA-256 as bytes arrive."""
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise DownloadIngestionError("DOWNLOAD_TOO_LARGE", f"Download exceeded the configured {max_bytes} byte limit.")
            digest.update(chunk)
            chunks.append(chunk)
    finally:
        response.close()
    return b"".join(chunks), total, digest.hexdigest()


def _content_disposition_filename(header: str | None) -> str | None:
    """Extract a conservative filename token from Content-Disposition for metadata only."""
    if not header:
        return None
    lower = header.lower()
    marker = "filename="
    index = lower.find(marker)
    if index < 0:
        return None
    value = header[index + len(marker):].strip().strip('"\'')
    return Path(value).name or None


def download_url_bytes(
    url: str,
    *,
    page_url: str = "",
    max_size_mb: int = 25,
    timeout: int = 30,
    same_origin_only: bool = True,
    allowed_hosts: set[str] | None = None,
    max_redirects: int = 5,
    user_agent: str = "Scrapper-Ollama/2.0",
    allow_private_ips: bool = False,
    allowed_ip_ranges: list[str] | tuple[str, ...] | None = None,
    max_header_bytes: int = 65536,
    allowed_schemes: set[str] | None = None,
    dns_pin: bool = True,
    max_url_length: int = 4096,
) -> DownloadFetchResult:
    """Fetch a candidate URL entirely in memory for verified Docker transfer."""
    allowed_host_set = {host.strip().lower().rstrip(".") for host in (allowed_hosts or set()) if host.strip()}
    ranges = _parse_ip_ranges(list(allowed_ip_ranges or []))
    response, final_url, max_bytes, handler = _open_download(
        url,
        page_url=page_url,
        max_size_mb=max_size_mb,
        timeout=timeout,
        same_origin_only=same_origin_only,
        allowed_hosts=allowed_host_set,
        max_redirects=max_redirects,
        user_agent=user_agent,
        allow_private_ips=allow_private_ips,
        allowed_ip_ranges=ranges,
        max_header_bytes=max_header_bytes,
        allowed_schemes={str(item).lower() for item in (allowed_schemes or {"http", "https"})},
        dns_pin=dns_pin,
        max_url_length=max_url_length,
    )
    content_type = response.headers.get_content_type() or "application/octet-stream"
    content_disposition = response.headers.get("Content-Disposition")
    filename = _content_disposition_filename(content_disposition)
    try:
        payload, total, digest = _consume_response(response, max_bytes)
    except DownloadIngestionError:
        raise
    except (OSError, urllib.error.URLError) as exc:
        raise DownloadIngestionError("DOWNLOAD_FAILED", f"Could not read {url}: {exc}") from exc

    if content_type.lower() in {"text/html", "application/xhtml+xml"}:
        raise DownloadIngestionError("DOWNLOAD_RETURNED_HTML", "The candidate URL returned an HTML page instead of a downloadable file.")

    return DownloadFetchResult(
        source_url=url,
        final_url=final_url,
        path=None,
        content_type=content_type,
        content_disposition=content_disposition,
        size=total,
        sha256=digest,
        payload=payload,
        redirect_chain=tuple([url, *handler.chain, final_url]),
        url_extension=Path(urlsplit(final_url).path).suffix.lower() or None,
        filename=filename,
    )
