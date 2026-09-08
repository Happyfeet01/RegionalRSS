from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


class FetchError(RuntimeError):
    """Raised when a configured source cannot be downloaded safely."""


@dataclass(frozen=True)
class FetchResult:
    status: int
    body: str | None
    etag: str | None
    last_modified: str | None
    final_url: str


def validate_public_url(url: str, *, allow_private_hosts: bool = False) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise FetchError("Only absolute HTTP(S) source URLs are allowed")
    if parsed.username or parsed.password:
        raise FetchError("Credentials in source URLs are not allowed")
    if allow_private_hosts:
        return

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".local"):
        raise FetchError("Local source hosts are blocked")

    try:
        addresses = socket.getaddrinfo(
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise FetchError(f"Unable to resolve source host: {hostname}") from exc

    for address in addresses:
        raw_ip = address[4][0].split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError as exc:
            raise FetchError(f"Invalid address returned for source host: {raw_ip}") from exc
        if not ip.is_global:
            raise FetchError(f"Non-public source address is blocked: {raw_ip}")


class PublicOnlyRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allow_private_hosts: bool) -> None:
        super().__init__()
        self.allow_private_hosts = allow_private_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        validate_public_url(newurl, allow_private_hosts=self.allow_private_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_html(
    url: str,
    *,
    user_agent: str,
    timeout_seconds: int = 12,
    max_response_bytes: int = 5 * 1024 * 1024,
    etag: str | None = None,
    last_modified: str | None = None,
    allow_private_hosts: bool = False,
    accepted_content_types: set[str] | None = None,
    accept_header: str | None = None,
) -> FetchResult:
    validate_public_url(url, allow_private_hosts=allow_private_hosts)

    headers = {
        "User-Agent": user_agent,
        "Accept": accept_header or "text/html,application/xhtml+xml;q=0.9",
        "Accept-Language": "de,en;q=0.7",
        "Connection": "close",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    request = Request(url, headers=headers, method="GET")
    opener = build_opener(PublicOnlyRedirectHandler(allow_private_hosts))
    try:
        response = opener.open(request, timeout=timeout_seconds)
    except HTTPError as exc:
        if exc.code == 304:
            return FetchResult(
                status=304,
                body=None,
                etag=exc.headers.get("ETag") or etag,
                last_modified=exc.headers.get("Last-Modified") or last_modified,
                final_url=url,
            )
        raise FetchError(f"Source returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"Unable to fetch source: {exc}") from exc

    with response:
        final_url = response.geturl()
        validate_public_url(final_url, allow_private_hosts=allow_private_hosts)
        content_type = response.headers.get_content_type()
        allowed_types = accepted_content_types or {"text/html", "application/xhtml+xml"}
        if content_type not in allowed_types:
            raise FetchError(f"Unexpected source content type: {content_type}")
        raw = response.read(max_response_bytes + 1)
        if len(raw) > max_response_bytes:
            raise FetchError("Source response exceeded the configured size limit")
        charset = response.headers.get_content_charset() or "utf-8"
        try:
            body = raw.decode(charset, errors="replace")
        except LookupError:
            body = raw.decode("utf-8", errors="replace")
        return FetchResult(
            status=getattr(response, "status", 200),
            body=body,
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            final_url=final_url,
        )
