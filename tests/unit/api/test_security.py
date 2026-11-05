"""The cross-site request guard and the security headers on a bare Starlette app."""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from copycast.adapters.api.security import (
    CROSS_SITE_SLUG,
    HSTS_VALUE,
    SPA_SCRIPT_HASHES,
    CrossSiteGuardMiddleware,
    SecurityHeadersMiddleware,
    docs_policy,
    normalized_origin,
    spa_policy,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
BASE_URL = "https://copycast.example"
UNSAFE = ("POST", "PUT", "PATCH", "DELETE")


def _app(*, base_url: str = BASE_URL, reached: list[str] | None = None) -> Starlette:
    seen = reached if reached is not None else []

    async def handler(request: Request) -> Response:
        seen.append(request.method)
        return PlainTextResponse("ok")

    async def own_headers(request: Request) -> Response:
        return PlainTextResponse(
            "ok",
            headers={
                "Content-Security-Policy": "sandbox",
                "Referrer-Policy": "origin",
                "X-Frame-Options": "SAMEORIGIN",
            },
        )

    methods = ["GET", "HEAD", "OPTIONS", *UNSAFE]
    app = Starlette(
        routes=[
            Route("/own", own_headers),
            Route("/{path:path}", handler, methods=methods),
        ]
    )
    app.add_middleware(CrossSiteGuardMiddleware, base_url=base_url)
    app.add_middleware(SecurityHeadersMiddleware, base_url=base_url)
    return app


@pytest.fixture
def reached() -> list[str]:
    return []


@pytest.fixture
async def client(reached: list[str]) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=_app(reached=reached))
    async with httpx.AsyncClient(transport=transport, base_url="http://internal:8080") as http:
        yield http


def assert_refused(response: httpx.Response) -> None:
    assert response.status_code == 403, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["type"] == f"urn:copycast:problem:{CROSS_SITE_SLUG}"
    assert body["status"] == 403


# --- the guard -----------------------------------------------------------------------------


@pytest.mark.parametrize("method", UNSAFE)
@pytest.mark.parametrize("site", ["cross-site", "same-site", "Cross-Site", "", "bogus"])
async def test_unsafe_methods_from_another_site_are_refused(
    client: httpx.AsyncClient, reached: list[str], method: str, site: str
) -> None:
    for path in ("/api/buckets", "/uploads/abc/token", "/mcp", "/feeds/x.xml"):
        response = await client.request(method, path, headers={"Sec-Fetch-Site": site})
        assert_refused(response)
    assert reached == []


@pytest.mark.parametrize("site", ["same-origin", "none", " Same-Origin "])
async def test_same_origin_and_user_initiated_requests_pass(
    client: httpx.AsyncClient, reached: list[str], site: str
) -> None:
    response = await client.post("/api/buckets", headers={"Sec-Fetch-Site": site})
    assert response.status_code == 200
    assert reached == ["POST"]


async def test_fetch_metadata_wins_over_a_matching_origin(
    client: httpx.AsyncClient, reached: list[str]
) -> None:
    headers = {"Sec-Fetch-Site": "cross-site", "Origin": BASE_URL}
    assert_refused(await client.post("/api/buckets", headers=headers))
    headers = {"Sec-Fetch-Site": "same-origin", "Origin": "https://evil.example"}
    assert (await client.post("/api/buckets", headers=headers)).status_code == 200
    assert reached == ["POST"]


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
async def test_safe_methods_are_never_refused(
    client: httpx.AsyncClient, reached: list[str], method: str
) -> None:
    headers = {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"}
    response = await client.request(method, "/api/feeds", headers=headers)
    assert response.status_code == 200
    assert reached == [method]


@pytest.mark.parametrize(
    "origin",
    [
        BASE_URL,
        "HTTPS://Copycast.Example",
        "https://copycast.example:443",
        "http://internal:8080",  # the request's own scheme and Host
    ],
)
async def test_without_fetch_metadata_a_matching_origin_passes(
    client: httpx.AsyncClient, reached: list[str], origin: str
) -> None:
    assert (await client.delete("/api/feeds/x", headers={"Origin": origin})).status_code == 200
    assert reached == ["DELETE"]


@pytest.mark.parametrize(
    "origin",
    [
        "null",  # sandboxed frames, file: pages, some redirects
        "https://evil.example",
        "http://copycast.example",  # the right host over the wrong scheme
        "https://copycast.example:8443",
        "https://copycast.example.evil.example",
        "http://internal:8081",
        "https://user@copycast.example",
        "https://copycast.example/path",
        "not a url",
        "",
    ],
)
async def test_without_fetch_metadata_another_origin_is_refused(
    client: httpx.AsyncClient, reached: list[str], origin: str
) -> None:
    assert_refused(await client.post("/api/buckets", headers={"Origin": origin}))
    assert reached == []


async def test_non_browser_clients_send_neither_header_and_pass(
    client: httpx.AsyncClient, reached: list[str]
) -> None:
    # curl, podcast apps, MCP clients: no Sec-Fetch-Site, no Origin.
    for method in UNSAFE:
        assert (await client.request(method, "/mcp")).status_code == 200
    assert reached == list(UNSAFE)


async def test_a_refusal_is_logged_with_a_redacted_path(client: httpx.AsyncClient) -> None:
    with structlog.testing.capture_logs() as events:
        await client.put(
            "/uploads/0123456789abcdef/AbCdEfGhIjKlMnOpQrStUvWxYz012345",
            headers={"Sec-Fetch-Site": "cross-site"},
        )
    (event,) = [e for e in events if e["event"] == "http.cross_site_refused"]
    assert event["path"] == "/uploads/0123456789abcdef/***"
    assert event["method"] == "PUT" and "cross-site" in event["reason"]


async def test_the_guard_ignores_lifespan_scopes() -> None:
    seen: list[str] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    async def receive() -> Any:
        return {}

    async def send(_: Any) -> None:
        pass

    await CrossSiteGuardMiddleware(inner, base_url=BASE_URL)({"type": "lifespan"}, receive, send)
    await SecurityHeadersMiddleware(inner, base_url=BASE_URL)({"type": "lifespan"}, receive, send)
    assert seen == ["lifespan", "lifespan"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://Copycast.Example:443", "https://copycast.example"),
        ("http://localhost:5173", "http://localhost:5173"),
        ("http://[::1]:8080", "http://[::1]:8080"),
        ("http://example.com:80/", "http://example.com"),
        ("null", None),
        ("ftp://example.com", None),
        ("https://example.com:99999", None),
        ("https://a:b@example.com", None),
        ("https://example.com?q", None),
    ],
)
def test_normalized_origin(value: str, expected: str | None) -> None:
    assert normalized_origin(value) == expected


# --- the headers ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/feeds-page", "/assets/app.js", "/api/feeds", "/mcp"])
async def test_the_ui_and_the_api_get_the_full_set(client: httpx.AsyncClient, path: str) -> None:
    headers = (await client.get(path)).headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["x-frame-options"] == "DENY"
    assert headers["cross-origin-opener-policy"] == "same-origin"
    assert headers["strict-transport-security"] == HSTS_VALUE
    assert headers["content-security-policy"] == spa_policy(BASE_URL)


@pytest.mark.parametrize(
    "path",
    [
        "/feeds/abc.xml",
        "/feeds/abc/media/0123.mp3",
        "/feeds/abc/k/key/assets/x.vtt",
        "/feeds/copycast-artwork.png",
        "/art/TOKEN.jpg",
    ],
)
async def test_feeds_media_and_artwork_get_only_nosniff_and_referrer_policy(
    client: httpx.AsyncClient, path: str
) -> None:
    headers = (await client.get(path)).headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    for absent in (
        "content-security-policy",
        "x-frame-options",
        "cross-origin-opener-policy",
        "strict-transport-security",
    ):
        assert absent not in headers, (path, absent)


async def test_a_refusal_carries_the_headers_too(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/buckets", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert response.headers["content-security-policy"] == spa_policy(BASE_URL)
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_the_api_docs_get_their_own_policy(client: httpx.AsyncClient) -> None:
    for path in ("/api/docs", "/api/docs/oauth2-redirect"):
        headers = (await client.get(path)).headers
        assert headers["content-security-policy"] == docs_policy()
        assert headers["x-frame-options"] == "DENY"


async def test_a_header_a_route_set_is_kept(client: httpx.AsyncClient) -> None:
    headers = (await client.get("/own")).headers
    assert headers["content-security-policy"] == "sandbox"
    assert headers["referrer-policy"] == "origin"
    assert headers["x-frame-options"] == "SAMEORIGIN"
    assert headers["x-content-type-options"] == "nosniff"


async def test_no_hsts_over_plain_http() -> None:
    transport = httpx.ASGITransport(app=_app(base_url="http://copycast.tailnet:8080"))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        headers = (await http.get("/")).headers
    assert "strict-transport-security" not in headers
    assert "http://copycast.tailnet:8080" in headers["content-security-policy"]


def test_the_spa_policy_has_the_required_directives() -> None:
    policy = dict(
        (directive.split(" ", 1)[0], directive.split(" ", 1)[1])
        for directive in spa_policy(BASE_URL).split("; ")
    )
    assert policy["default-src"] == "'self'"
    assert policy["img-src"] == f"'self' {BASE_URL} data: https: http:"
    assert policy["media-src"] == f"'self' {BASE_URL}"
    assert policy["connect-src"] == f"'self' {BASE_URL}"
    assert policy["frame-ancestors"] == "'none'"
    assert policy["base-uri"] == "'none'"
    assert policy["object-src"] == "'none'"
    assert policy["form-action"] == "'self'"
    assert "'unsafe-inline'" not in policy["script-src"]
    assert "'unsafe-eval'" not in spa_policy(BASE_URL)


def test_every_inline_script_of_the_spa_shell_is_hashed() -> None:
    """``web/index.html`` keeps its inline scripts byte for byte through ``vite build``; the
    CSP allows exactly their hashes, so editing one fails here until the hash follows."""
    html = (REPO_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.DOTALL)
    assert inline, "the no-flash theme script is inline"
    digests = {
        "sha256-" + base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
        for body in inline
    }
    assert digests == set(SPA_SCRIPT_HASHES)
