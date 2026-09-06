"""Pure ASGI middleware: request ids, ``Cache-Control: no-store`` on JSON.

Both wrap ``send`` rather than using ``BaseHTTPMiddleware`` so streaming
responses (SSE, media) and background tasks keep their exact semantics.
"""

from __future__ import annotations

import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from copycast.logging import bind_context, unbind_context

REQUEST_ID_HEADER = "x-request-id"
NO_STORE_TYPES = ("application/json", "application/problem+json")


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


__all__ = [
    "NO_STORE_TYPES",
    "REQUEST_ID_HEADER",
    "NoStoreJsonMiddleware",
    "RequestContextMiddleware",
]
