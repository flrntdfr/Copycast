"""``AccessLogMiddleware`` on a bare Starlette app: one ``http.request`` line per request."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, StreamingResponse
from starlette.routing import Route
from starlette.types import Message, Receive, Scope, Send

from copycast.adapters.api.middleware import (
    ACCESS_LOG_EVENT,
    AccessLogMiddleware,
    RequestContextMiddleware,
    client_ip_of,
)

EventDict = dict[str, Any]


@pytest.fixture
def captured() -> Iterator[list[EventDict]]:
    with structlog.testing.capture_logs() as events:
        yield events


def requests_in(events: list[EventDict]) -> list[EventDict]:
    return [event for event in events if event["event"] == ACCESS_LOG_EVENT]


def _app(
    media_file: Path, captured: list[EventDict], events_seen_by_background: list[int]
) -> Starlette:
    async def ok(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    async def media(request: Request) -> FileResponse:
        def note() -> None:
            events_seen_by_background.append(len(requests_in(captured)))

        return FileResponse(media_file, media_type="audio/mpeg", background=BackgroundTask(note))

    async def stream(request: Request) -> StreamingResponse:
        async def chunks() -> Any:
            yield b"abc"
            yield b"de"

        return StreamingResponse(chunks(), media_type="text/plain")

    async def boom(request: Request) -> PlainTextResponse:
        raise RuntimeError("kaboom")

    app = Starlette(
        routes=[
            Route("/api/x", ok),
            Route("/healthz/live", ok),
            Route("/feeds/abc/media/item.mp3", media),
            Route("/stream", stream),
            Route("/boom", boom),
        ]
    )
    # Same order as create_app: the request-id layer inside, the request log outermost.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(AccessLogMiddleware)
    return app


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    path = tmp_path / "item.mp3"
    path.write_bytes(b"hello")
    return path


@pytest.fixture
def events_seen_by_background() -> list[int]:
    return []


@pytest.fixture
async def client(
    media_file: Path, captured: list[EventDict], events_seen_by_background: list[int]
) -> Any:
    app = _app(media_file, captured, events_seen_by_background)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        yield http


async def test_plain_response_logs_one_line(
    client: httpx.AsyncClient, captured: list[EventDict]
) -> None:
    response = await client.get(
        "/api/x?q=1&page=2",
        headers={
            "User-Agent": "Overcast/2026",
            "X-Request-ID": "req-1",
            "Authorization": "Basic Y29weWNhc3Q6aHVudGVyMg==",
        },
    )
    assert response.status_code == 200 and response.text == "ok"

    (event,) = requests_in(captured)
    assert event["log_level"] == "info"
    assert event["method"] == "GET"
    assert event["path"] == "/api/x"
    assert event["query"] == "q=1&page=2"
    assert event["status"] == 200
    assert event["bytes"] == 2
    assert event["content_length"] == 2
    assert event["client_ip"] == "127.0.0.1"  # ASGITransport's default peer
    assert event["forwarded_for"] is None
    assert event["user_agent"] == "Overcast/2026"
    assert event["range"] is None
    assert event["request_id"] == "req-1"  # from the X-Request-ID response header
    assert event["completed"] is True
    assert isinstance(event["duration_ms"], float) and event["duration_ms"] >= 0
    assert round(event["duration_ms"], 1) == event["duration_ms"]
    # Never the credentials, never a username.
    serialized = repr(event).lower()
    assert "authorization" not in serialized and "y29wewnhc3q" not in serialized
    assert "copycast:" not in serialized and "hunter2" not in serialized


async def test_query_is_none_when_empty(
    client: httpx.AsyncClient, captured: list[EventDict]
) -> None:
    assert (await client.get("/api/x")).status_code == 200
    (event,) = requests_in(captured)
    assert event["query"] is None
    assert event["user_agent"].startswith("python-httpx")


async def test_file_range_logs_after_the_body_and_before_the_background_task(
    client: httpx.AsyncClient, captured: list[EventDict], events_seen_by_background: list[int]
) -> None:
    response = await client.get("/feeds/abc/media/item.mp3", headers={"Range": "bytes=0-1"})
    assert response.status_code == 206
    assert response.content == b"he"
    assert response.headers["content-range"] == "bytes 0-1/5"

    (event,) = requests_in(captured)
    assert event["status"] == 206
    assert event["bytes"] == 2
    assert event["content_length"] == 2
    assert event["range"] == "bytes=0-1"
    assert event["completed"] is True
    # The line is written when the final body chunk goes out; the background task ran later.
    assert events_seen_by_background == [1]


async def test_head_request_counts_no_body_bytes(
    client: httpx.AsyncClient, captured: list[EventDict]
) -> None:
    response = await client.head("/feeds/abc/media/item.mp3")
    assert response.status_code == 200 and response.content == b""
    (event,) = requests_in(captured)
    assert event["method"] == "HEAD"
    assert event["bytes"] == 0 and event["content_length"] == 5
    assert event["completed"] is True


async def test_streaming_response_sums_every_chunk_into_one_line(
    client: httpx.AsyncClient, captured: list[EventDict]
) -> None:
    response = await client.get("/stream")
    assert response.status_code == 200 and response.content == b"abcde"
    (event,) = requests_in(captured)
    assert event["bytes"] == 5
    assert event["content_length"] is None  # chunked: no Content-Length header
    assert event["completed"] is True


async def test_health_paths_are_skipped(
    client: httpx.AsyncClient, captured: list[EventDict]
) -> None:
    assert (await client.get("/healthz/live")).status_code == 200
    assert requests_in(captured) == []


async def test_forwarded_for_first_hop_wins(
    client: httpx.AsyncClient, captured: list[EventDict]
) -> None:
    forwarded = "203.0.113.9, 10.0.0.1, 10.0.0.2"
    assert (await client.get("/api/x", headers={"X-Forwarded-For": forwarded})).status_code == 200
    (event,) = requests_in(captured)
    assert event["client_ip"] == "203.0.113.9"
    assert event["forwarded_for"] == forwarded


async def test_unhandled_exception_logs_500_not_completed(
    client: httpx.AsyncClient, captured: list[EventDict]
) -> None:
    # ServerErrorMiddleware answers on the raw send, outside this layer, and re-raises.
    response = await client.get("/boom")
    assert response.status_code == 500

    (event,) = requests_in(captured)
    assert event["path"] == "/boom"
    assert event["status"] == 500
    assert event["bytes"] == 0
    assert event["content_length"] is None
    assert event["completed"] is False


async def test_response_that_never_finishes_logs_once_not_completed(
    captured: list[EventDict],
) -> None:
    async def half_a_response(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"part", "more_body": True})
        # The client went away: the app returns without its final chunk.

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/feeds/abc.xml",
        "query_string": b"",
        "headers": [],
        "client": None,
    }
    await AccessLogMiddleware(half_a_response)(scope, receive, send)

    (event,) = requests_in(captured)
    assert event["status"] == 200
    assert event["bytes"] == 4
    assert event["client_ip"] is None  # no peer in the scope
    assert event["completed"] is False
    assert len(sent) == 2


async def test_non_http_scopes_pass_through(captured: list[EventDict]) -> None:
    seen: list[str] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        pass

    await AccessLogMiddleware(inner)({"type": "lifespan"}, receive, send)
    assert seen == ["lifespan"] and requests_in(captured) == []


@pytest.mark.parametrize(
    ("forwarded_for", "client", "expected"),
    [
        (None, ("10.1.2.3", 5000), "10.1.2.3"),
        (None, None, None),
        ("", ("10.1.2.3", 5000), "10.1.2.3"),
        ("198.51.100.7", None, "198.51.100.7"),
        (" 198.51.100.7 ,10.0.0.1", ("10.1.2.3", 5000), "198.51.100.7"),
    ],
)
def test_client_ip_of(
    forwarded_for: str | None, client: tuple[str, int] | None, expected: str | None
) -> None:
    from starlette.datastructures import Headers

    headers = Headers({"x-forwarded-for": forwarded_for} if forwarded_for is not None else {})
    assert client_ip_of({"type": "http", "client": client}, headers) == expected
