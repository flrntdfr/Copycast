"""HTTP Basic parsing, the operator check, the gated paths and the middleware on a bare ASGI app."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from copycast.adapters.api import auth
from copycast.settings import AuthSettings


def basic(username: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("", None),
        ("Bearer abc", None),
        ("Basic", None),
        ("Basic not-base64!", None),
        ("Basic " + base64.b64encode(b"nocolon").decode(), None),
        (basic("u", "p"), auth.BasicCredentials("u", "p")),
        (basic("u", "p:with:colons"), auth.BasicCredentials("u", "p:with:colons")),
        ("basic " + base64.b64encode("ü:pä".encode()).decode(), auth.BasicCredentials("ü", "pä")),
    ],
)
def test_parse_basic(header: str | None, expected: auth.BasicCredentials | None) -> None:
    assert auth.parse_basic(header) == expected


def test_is_operator() -> None:
    settings = AuthSettings(username="op", password="correct-horse")
    assert auth.is_operator(settings, auth.BasicCredentials("op", "correct-horse"))
    assert not auth.is_operator(settings, auth.BasicCredentials("op", "wrong-horse"))
    assert not auth.is_operator(settings, auth.BasicCredentials("other", "correct-horse"))
    assert not auth.is_operator(settings, None)
    assert not auth.is_operator(AuthSettings(), auth.BasicCredentials("copycast", ""))


@pytest.mark.parametrize(
    ("path", "gated"),
    [
        ("/", True),
        ("/index.html", True),
        ("/assets/app-abc.js", True),
        ("/api/feeds", True),
        ("/api/events", True),
        ("/healthz", False),
        ("/healthz/live", False),
        ("/healthz/ready", False),
        ("/healthzz", True),
        ("/healthz-status", True),
        ("/feeds/abc.xml", False),
        ("/feeds/abc/media/0123456789abcdef.m4a", False),
        ("/feedsX", True),
        ("/mcp", False),
        ("/mcp/", False),
        ("/mcp/.well-known/x", False),
        ("/mcpx", True),
    ],
)
def test_is_gated(path: str, gated: bool) -> None:
    assert auth.is_gated(path) is gated


def _app(settings: AuthSettings) -> Starlette:
    async def ok(request: object) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = Starlette(
        routes=[
            Route("/api/x", ok),
            Route("/healthz/live", ok),
            Route("/feeds/abc.xml", ok),
            Route("/mcp", ok),
        ]
    )
    app.add_middleware(auth.OperatorAuthMiddleware, auth=settings)
    return app


async def test_middleware_challenges_gated_paths_only() -> None:
    settings = AuthSettings(username="op", password="correct-horse")
    transport = httpx.ASGITransport(app=_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        missing = await client.get("/api/x")
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == 'Basic realm="Copycast", charset="UTF-8"'
        assert missing.headers["content-type"].startswith("application/problem+json")
        assert missing.headers["cache-control"] == "no-store"
        body = json.loads(missing.content)
        assert body["type"] == "urn:copycast:problem:unauthorized"
        assert body["instance"] == "/api/x" and "username and password" in body["detail"]

        wrong = await client.get("/api/x", headers={"Authorization": basic("op", "nope")})
        assert wrong.status_code == 401 and "wrong" in json.loads(wrong.content)["detail"]

        right = await client.get("/api/x", headers={"Authorization": basic("op", "correct-horse")})
        assert right.status_code == 200 and right.text == "ok"

        # Health, feeds and MCP are left to their own gates.
        for path in ("/healthz/live", "/feeds/abc.xml", "/mcp"):
            assert (await client.get(path)).status_code == 200, path


async def test_middleware_is_transparent_while_auth_is_off() -> None:
    transport = httpx.ASGITransport(app=_app(AuthSettings()))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        assert (await client.get("/api/x")).status_code == 200
