"""The shared httpx client and a body-capped fetch used by every Source adapter.

Timeouts are 20 s to connect and 120 s to read; redirects are followed;
every body is streamed and cut off at the caller's cap so a hostile Source
cannot exhaust memory. Errors are reported as :class:`SourceError` subclasses
the callers translate (probe -> ``Unsupported``, refresh -> ``last_error``).
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

import httpx

from copycast.version import APP_VERSION

USER_AGENT: Final = f"Copycast/{APP_VERSION} (+https://github.com/flrntdfr/copycast)"
CONNECT_TIMEOUT: Final = 20.0
READ_TIMEOUT: Final = 120.0
MAX_REDIRECTS: Final = 10
CHUNK_SIZE: Final = 64 * 1024

MiB: Final = 1024 * 1024
MAX_FEED_BYTES: Final = 25 * MiB
MAX_HTML_BYTES: Final = 256 * 1024


class SourceError(Exception):
    """Base class of every HTTP-level Source failure."""


class SourceUnreachable(SourceError):
    """Network failure, timeout or a 5xx answer: worth retrying later."""


class SourceRejected(SourceError):
    """A 4xx answer: the Source refused this request."""

    def __init__(self, url: str, status: int) -> None:
        self.url = url
        self.status = status
        super().__init__(f"{url} answered HTTP {status}")


class BodyTooLarge(SourceError):
    """The body exceeded the caller's cap."""

    def __init__(self, url: str, limit: int) -> None:
        self.url = url
        self.limit = limit
        super().__init__(f"{url} exceeds the {limit} byte limit")


class NotAFeed(SourceError):
    """The body is not an RSS document Copycast can mirror."""


@dataclass(frozen=True, slots=True)
class Fetched:
    """One completed response; ``not_modified`` marks a 304 (empty body)."""

    url: str
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict[str, str])
    not_modified: bool = False

    @property
    def content_type(self) -> str | None:
        raw = self.headers.get("content-type")
        if not raw:
            return None
        return raw.split(";", 1)[0].strip().lower() or None

    @property
    def etag(self) -> str | None:
        return self.headers.get("etag")

    @property
    def last_modified(self) -> str | None:
        return self.headers.get("last-modified")


def default_timeout() -> httpx.Timeout:
    return httpx.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=30.0, pool=30.0)


def create_client(*, timeout: httpx.Timeout | None = None) -> httpx.Client:
    """A new client with Copycast's user agent, timeouts and redirect policy."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
        timeout=timeout or default_timeout(),
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
    )


_shared: httpx.Client | None = None
_shared_lock = threading.Lock()


def get_client() -> httpx.Client:
    """The process-wide client (thread-safe; created on first use)."""
    global _shared
    with _shared_lock:
        if _shared is None or _shared.is_closed:
            _shared = create_client()
        return _shared


def close_client() -> None:
    global _shared
    with _shared_lock:
        if _shared is not None:
            _shared.close()
            _shared = None


def fetch(
    url: str,
    *,
    max_bytes: int,
    client: httpx.Client | None = None,
    headers: Mapping[str, str] | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    accept: str | None = None,
    method: str = "GET",
) -> Fetched:
    """GET ``url`` (streamed) with an optional conditional request.

    ``etag``/``last_modified`` become ``If-None-Match``/``If-Modified-Since``;
    a 304 returns ``Fetched(not_modified=True)``. Bodies longer than
    ``max_bytes`` raise :class:`BodyTooLarge`; 4xx raise :class:`SourceRejected`;
    5xx and transport errors raise :class:`SourceUnreachable`.
    """
    request_headers: dict[str, str] = dict(headers or {})
    if accept:
        request_headers["Accept"] = accept
    if etag:
        request_headers["If-None-Match"] = etag
    if last_modified:
        request_headers["If-Modified-Since"] = last_modified
    http = client or get_client()
    try:
        with http.stream(method, url, headers=request_headers) as response:
            final_url = str(response.url)
            response_headers = {k.lower(): v for k, v in response.headers.items()}
            status = response.status_code
            if status == 304:
                return Fetched(final_url, status, b"", response_headers, not_modified=True)
            if status >= 500:
                raise SourceUnreachable(f"{url} answered HTTP {status}")
            if status >= 400:
                raise SourceRejected(url, status)
            if method.upper() == "HEAD":
                return Fetched(final_url, status, b"", response_headers)
            declared = response_headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise BodyTooLarge(url, max_bytes)
            chunks: list[bytes] = []
            received = 0
            for chunk in response.iter_bytes(CHUNK_SIZE):
                received += len(chunk)
                if received > max_bytes:
                    raise BodyTooLarge(url, max_bytes)
                chunks.append(chunk)
            return Fetched(final_url, status, b"".join(chunks), response_headers)
    except httpx.HTTPError as exc:
        raise SourceUnreachable(f"{url}: {_describe(exc)}") from exc


def content_type_of(url: str, *, client: httpx.Client | None = None) -> str | None:
    """The media type ``url`` serves (HEAD, falling back to a GET cut off after one chunk)."""
    try:
        head = fetch(url, max_bytes=0, client=client, method="HEAD")
        if head.content_type:
            return head.content_type
    except SourceError:
        pass
    http = client or get_client()
    try:
        with http.stream("GET", url) as response:
            if response.status_code >= 400:
                return None
            raw = response.headers.get("content-type")
            return raw.split(";", 1)[0].strip().lower() if raw else None
    except httpx.HTTPError:
        return None


def _describe(exc: httpx.HTTPError) -> str:
    text = str(exc).strip()
    return text or type(exc).__name__


__all__ = [
    "MAX_FEED_BYTES",
    "MAX_HTML_BYTES",
    "USER_AGENT",
    "BodyTooLarge",
    "Fetched",
    "NotAFeed",
    "SourceError",
    "SourceRejected",
    "SourceUnreachable",
    "close_client",
    "content_type_of",
    "create_client",
    "fetch",
    "get_client",
]
