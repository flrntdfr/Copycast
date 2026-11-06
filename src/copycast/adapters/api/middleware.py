"""Pure ASGI middleware: request ids, ``Cache-Control: no-store`` on JSON, the request log,
and the body cap of the unauthenticated ``/auth`` routes.

All of them wrap ``send`` rather than using ``BaseHTTPMiddleware`` so streaming
responses (SSE, media) and background tasks keep their exact semantics.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from copycast.adapters.api.problems import PROBLEM_MEDIA_TYPE, build_problem
from copycast.logging import bind_context, get_logger, redact_text, unbind_context

REQUEST_ID_HEADER = "x-request-id"
REQUEST_ID_MAX_LEN = 64
NO_STORE_TYPES = ("application/json", "application/problem+json")

log = get_logger(__name__)


def ensure_request_id(scope: Scope) -> str:
    """The request's id: the client's ``X-Request-ID`` (cut to 64 chars), else one minted here.

    Kept on ``scope["state"]`` so every layer that asks gets the same id, whichever
    asked first: the request log (outermost) can then name it even when the request
    never produced a response through the stack (an unhandled exception).
    """
    state: dict[str, Any] = scope.setdefault("state", {})
    found = state.get("request_id")
    if isinstance(found, str) and found:
        return found
    incoming = Headers(scope=scope).get(REQUEST_ID_HEADER, "").strip()
    request_id = incoming[:REQUEST_ID_MAX_LEN] if incoming else uuid.uuid4().hex[:16]
    state["request_id"] = request_id
    return request_id


class RequestContextMiddleware:
    """Echo (or mint) ``X-Request-ID`` and bind it to the structlog context."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = ensure_request_id(scope)
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


AUTH_BODY_PREFIX = "/auth/"
AUTH_BODY_LIMIT = 16 * 1024
"""Bytes a ``/auth`` request body may have: a login is a username (256 characters), a
password (1024) and a code, JSON-escaped; anything longer is no sign-in."""
TOO_LARGE_SLUG = "payload-too-large"


class AuthBodyLimitMiddleware:
    """Refuse an ``/auth`` request body over :data:`AUTH_BODY_LIMIT` with 413, unread.

    ``POST /auth/login`` is the one route that parses a body before any credential, and
    neither uvicorn nor FastAPI caps one: a client could make the api buffer and
    ``json.loads`` gigabytes. A declared ``Content-Length`` over the cap is refused at
    once; otherwise the body (chunked or not) is read here up to the cap, refused as soon
    as it goes past it, and handed to the route from this buffer.
    """

    def __init__(
        self, app: ASGIApp, *, prefix: str = AUTH_BODY_PREFIX, limit: int = AUTH_BODY_LIMIT
    ) -> None:
        self.app = app
        self.prefix = prefix
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        if scope["type"] != "http" or not path.startswith(self.prefix):
            await self.app(scope, receive, send)
            return
        declared = _int_or_none(Headers(scope=scope).get("content-length"))
        if declared is not None and declared > self.limit:
            await self._refuse(scope, receive, send, path)
            return
        chunks: list[bytes] = []
        size = 0
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body: bytes = message.get("body", b"")
            size += len(body)
            if size > self.limit:
                await self._refuse(scope, receive, send, path)
                return
            chunks.append(body)
            more = bool(message.get("more_body", False))
        buffered: Message | None = {
            "type": "http.request",
            "body": b"".join(chunks),
            "more_body": False,
        }

        async def replay() -> Message:
            nonlocal buffered
            if buffered is not None:
                message, buffered = buffered, None
                return message
            return await receive()

        await self.app(scope, replay, send)

    async def _refuse(self, scope: Scope, receive: Receive, send: Send, path: str) -> None:
        log.warning("http.body_too_large", path=redact_text(path), limit=self.limit)
        problem = build_problem(
            None,
            status=413,
            slug=TOO_LARGE_SLUG,
            detail=f"a request body here is limited to {self.limit} bytes",
        )
        body = problem.model_dump(mode="json", exclude_none=True)
        body["instance"] = path
        response = JSONResponse(
            body,
            status_code=413,
            media_type=PROBLEM_MEDIA_TYPE,
            headers={"Cache-Control": "no-store", "Connection": "close"},
        )
        await response(scope, receive, send)


ACCESS_LOG_EVENT = "http.request"
ACCESS_LOG_SKIP_PREFIX = "/healthz/"
"""Compose and Kubernetes probe ``/healthz/live`` every few seconds: never worth a line."""
FORWARDED_FOR_HEADER = "x-forwarded-for"


def client_ip_of(scope: Scope) -> str | None:
    """The client's address as uvicorn resolved it: the socket peer, or the hop a trusted
    proxy named in ``X-Forwarded-For`` (``COPYCAST_FORWARDED_ALLOW_IPS``).

    The header itself is never believed here: any client can send one, so only the raw
    ``forwarded_for`` field of the log line carries what it claimed.
    """
    client = scope.get("client")
    return str(client[0]) if client else None


LOGGED_VALUE_MAX = 2048
"""The longest path, query or header value an access log line carries, in characters; the
rest is cut (a request line can be tens of kilobytes, nobody reads that in a log)."""
TRUNCATED_MARK = "…"


def capped(value: str | None) -> str | None:
    """``value`` cut to :data:`LOGGED_VALUE_MAX` characters, marked when it was cut."""
    if value is None or len(value) <= LOGGED_VALUE_MAX:
        return value
    return value[:LOGGED_VALUE_MAX] + TRUNCATED_MARK


def logged_path(path: str) -> str:
    """The path as logged: a media key (``/feeds/{id}/k/{key}/``) or an upload token
    (``/uploads/{id}/{token}``) is a credential (:func:`copycast.logging.redact_text`),
    redacted before the path is cut to :data:`LOGGED_VALUE_MAX` (a cut must never leave
    half a secret the rule no longer recognises)."""
    return capped(redact_text(path)) or ""


def _redacted_query(scope: Scope) -> str | None:
    raw: bytes = scope.get("query_string") or b""
    return capped(redact_text(raw.decode("utf-8", "replace"))) or None


def _logged_header(headers: Headers, name: str) -> str | None:
    value = headers.get(name)
    return None if value is None else capped(redact_text(value))


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
    or a username; the path and query go through the redaction of ``copycast.logging``.
    ``client_ip`` is the address uvicorn resolved (trusted proxies only), ``forwarded_for``
    the raw header as the client sent it. An unhandled exception never produces a response
    through this layer (``ServerErrorMiddleware`` answers 500 on the raw socket and
    re-raises), so it is logged as ``status=500, bytes=0, completed=False`` with the
    request id minted for it (the one line an operator most wants to match with the
    traceback); a request that ends without its final body (client gone, task cancelled)
    is logged with ``completed=False`` too.
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
        request_id: str | None = ensure_request_id(scope)
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
                path=logged_path(scope["path"]),
                query=_redacted_query(scope),
                status=500 if status is None else status,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                bytes=body_bytes,
                content_length=content_length,
                client_ip=client_ip_of(scope),
                forwarded_for=_logged_header(request_headers, FORWARDED_FOR_HEADER),
                user_agent=_logged_header(request_headers, "user-agent"),
                range=_logged_header(request_headers, "range"),
                request_id=request_id,
                completed=completed,
            )

        async def send_logged(message: Message) -> None:
            nonlocal status, content_length, request_id, body_bytes
            message_type = message["type"]
            if message_type == "http.response.start":
                status = int(message["status"])
                response_headers = Headers(raw=message.get("headers", []))
                request_id = response_headers.get(REQUEST_ID_HEADER) or request_id
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
    "AUTH_BODY_LIMIT",
    "AUTH_BODY_PREFIX",
    "LOGGED_VALUE_MAX",
    "NO_STORE_TYPES",
    "REQUEST_ID_HEADER",
    "REQUEST_ID_MAX_LEN",
    "ROBOTS_TAG",
    "TOO_LARGE_SLUG",
    "AccessLogMiddleware",
    "AuthBodyLimitMiddleware",
    "NoRobotsMiddleware",
    "NoStoreJsonMiddleware",
    "RequestContextMiddleware",
    "capped",
    "client_ip_of",
    "ensure_request_id",
    "logged_path",
]
