"""``AuthBodyLimitMiddleware``: an ``/auth`` body over the cap is refused unread (413)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from starlette.types import Message, Receive, Scope, Send

from copycast.adapters.api.middleware import AUTH_BODY_LIMIT, AuthBodyLimitMiddleware


class Echo:
    """Answers 200 with the size of the body it read; records whether it ran."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.calls += 1
        size = 0
        more = True
        while more:
            message = await receive()
            size += len(message.get("body", b""))
            more = bool(message.get("more_body", False))
        body = str(size).encode()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body})


def client(app: Echo) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=AuthBodyLimitMiddleware(app))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def chunks(total: int, size: int = 4096) -> AsyncIterator[bytes]:
    sent = 0
    while sent < total:
        step = min(size, total - sent)
        sent += step
        yield b"x" * step


async def test_a_body_within_the_cap_reaches_the_route_whole() -> None:
    app = Echo()
    async with client(app) as http:
        response = await http.post("/auth/login", content=b"x" * AUTH_BODY_LIMIT)
        streamed = await http.post("/auth/login", content=chunks(AUTH_BODY_LIMIT))
    assert response.status_code == 200 and response.text == str(AUTH_BODY_LIMIT)
    assert streamed.status_code == 200 and streamed.text == str(AUTH_BODY_LIMIT)
    assert app.calls == 2


@pytest.mark.parametrize("chunked", [False, True], ids=["content-length", "chunked"])
async def test_a_body_over_the_cap_is_refused_before_the_route(chunked: bool) -> None:
    app = Echo()
    size = 64 * 1024 * 1024
    async with client(app) as http:
        content = chunks(size) if chunked else b"x" * (AUTH_BODY_LIMIT + 1)
        response = await http.post("/auth/login", content=content)
    assert response.status_code == 413
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["type"] == "urn:copycast:problem:payload-too-large"
    assert app.calls == 0


async def test_a_lying_content_length_is_refused_by_the_bytes() -> None:
    """A Content-Length under the cap does not let a longer body through."""
    app = Echo()
    received: list[Message] = [
        {"type": "http.request", "body": b"x" * AUTH_BODY_LIMIT, "more_body": True},
        {"type": "http.request", "body": b"x", "more_body": False},
    ]
    sent: list[Message] = []

    async def receive() -> Message:
        return received.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/auth/login",
        "headers": [(b"content-length", b"10")],
    }
    await AuthBodyLimitMiddleware(app)(scope, receive, send)
    assert sent[0]["status"] == 413
    assert app.calls == 0


async def test_other_paths_are_left_alone() -> None:
    app = Echo()
    async with client(app) as http:
        response = await http.put("/uploads/abc/token", content=b"x" * (AUTH_BODY_LIMIT * 4))
    assert response.status_code == 200 and response.text == str(AUTH_BODY_LIMIT * 4)
