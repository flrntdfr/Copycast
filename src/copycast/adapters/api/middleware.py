"""Pure ASGI middleware: request ids, ``Cache-Control: no-store`` on JSON, the request log.

All of them wrap ``send`` rather than using ``BaseHTTPMiddleware`` so streaming
responses (SSE, media) and background tasks keep their exact semantics.
"""

from __future__ import annotations

import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from copycast.logging import bind_context, get_logger, unbind_context

REQUEST_ID_HEADER = "x-request-id"
NO_STORE_TYPES = ("application/json", "application/problem+json")

log = get_logger(__name__)


class RequestContextMiddleware:
    """Echo (or mint) ``X-Request-ID`` and bind it to the structlog context."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER, "").strip()
        request_id = incoming[:64] if incoming else uuid.uuid4().hex[:16]
        bind_context(request_id=request_id)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            unbind_context("request_id")


ROBOTS_TAG = "noindex, nofollow, noarchive"


class NoRobotsMiddleware:
    """``X-Robots-Tag`` on every response: a personal archive is nothing to index."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_tagged(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["x-robots-tag"] = ROBOTS_TAG
            await send(message)

        await self.app(scope, receive, send_tagged)


class NoStoreJsonMiddleware:
    """``Cache-Control: no-store`` on every JSON response that did not choose otherwise."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                content_type = headers.get("content-type", "").partition(";")[0].strip().lower()
                if content_type in NO_STORE_TYPES and "cache-control" not in headers:
                    headers["cache-control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_no_store)


ACCESS_LOG_EVENT = "http.request"
ACCESS_LOG_SKIP_PREFIX = "/healthz/"
"""Compose and Kubernetes probe ``/healthz/live`` every few seconds: never worth a line."""
FORWARDED_FOR_HEADER = "x-forwarded-for"


def client_ip_of(scope: Scope, headers: Headers) -> str | None:
    """The first hop of ``X-Forwarded-For`` when a proxy set it, else the socket peer."""
    first_hop = headers.get(FORWARDED_FOR_HEADER, "").partition(",")[0].strip()
    if first_hop:
        return first_hop
    client = scope.get("client")
    return str(client[0]) if client else None


def _query_of(scope: Scope) -> str | None:
    raw: bytes = scope.get("query_string") or b""
    return raw.decode("utf-8", "replace") or None


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


class AccessLogMiddleware:
    """One ``http.request`` line per request, written once the final body chunk went out.

    Outermost of the stack, so ``status``, ``bytes`` and ``X-Request-ID`` are what the
    client received after every other layer (gzip included). Never logs ``Authorization``
    or a username. An unhandled exception never produces a response through this layer
    (``ServerErrorMiddleware`` answers 500 on the raw socket and re-raises), so it is
    logged as ``status=500, bytes=0, completed=False``; a request that ends without its
    final body (client gone, task cancelled) is logged with ``completed=False`` too.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or str(scope.get("path", "")).startswith(ACCESS_LOG_SKIP_PREFIX):
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        request_headers = Headers(scope=scope)
        status: int | None = None
        content_length: int | None = None
        request_id: str | None = None
        body_bytes = 0
        logged = False

        def emit(*, completed: bool) -> None:
            nonlocal logged
            if logged:
                return
            logged = True
            log.info(
                ACCESS_LOG_EVENT,
                method=scope["method"],
                path=scope["path"],
                query=_query_of(scope),
                status=500 if status is None else status,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                bytes=body_bytes,
                content_length=content_length,
                client_ip=client_ip_of(scope, request_headers),
                forwarded_for=request_headers.get(FORWARDED_FOR_HEADER),
                user_agent=request_headers.get("user-agent"),
                range=request_headers.get("range"),
                request_id=request_id,
                completed=completed,
            )

        async def send_logged(message: Message) -> None:
            nonlocal status, content_length, request_id, body_bytes
            message_type = message["type"]
            if message_type == "http.response.start":
                status = int(message["status"])
                response_headers = Headers(raw=message.get("headers", []))
                request_id = response_headers.get(REQUEST_ID_HEADER)
                content_length = _int_or_none(response_headers.get("content-length"))
            await send(message)
            if message_type == "http.response.body":
                body_bytes += len(message.get("body", b""))
                if not message.get("more_body", False):
                    emit(completed=True)

        try:
            await self.app(scope, receive, send_logged)
        finally:
            emit(completed=False)


__all__ = [
    "ACCESS_LOG_EVENT",
    "ACCESS_LOG_SKIP_PREFIX",
    "NO_STORE_TYPES",
    "REQUEST_ID_HEADER",
    "ROBOTS_TAG",
    "AccessLogMiddleware",
    "NoRobotsMiddleware",
    "NoStoreJsonMiddleware",
    "RequestContextMiddleware",
    "client_ip_of",
]
