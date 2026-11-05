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
    PODCAST_POLICY,
    SPA_SCRIPT_HASHES,
    UNKNOWN_HOST_SLUG,
    CrossSiteGuardMiddleware,
    SecurityHeadersMiddleware,
    base_origin,
    docs_policy,
    host_name,
    normalized_origin,
    spa_policy,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
BASE_URL = "https://copycast.example"
UNSAFE = ("POST", "PUT", "PATCH", "DELETE")


def _app(
    *,
    base_url: str = BASE_URL,
    reached: list[str] | None = None,
    allowed_hosts: tuple[str, ...] = ("internal",),
) -> Starlette:
    seen = reached if reached is not None else []

    async def handler(request: Request) -> Response:
        seen.append(request.method)
        if request.url.path.endswith(".mp3"):
            return Response(b"ID3", media_type="audio/mpeg")
        if request.url.path.endswith(".mp4"):
            return Response(b"", media_type="video/mp4")
        if request.url.path.endswith(".html"):
            return Response(b"<script>alert(1)</script>", media_type="text/html")
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
    app.add_middleware(CrossSiteGuardMiddleware, base_url=base_url, allowed_hosts=allowed_hosts)
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


def assert_refused(response: httpx.Response, slug: str = CROSS_SITE_SLUG) -> None:
    assert response.status_code == 403, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["type"] == f"urn:copycast:problem:{slug}"
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


# --- the host check (DNS rebinding) --------------------------------------------------------


REBOUND = ("evil.example:8080", "copycast.example.evil.example", "internal.evil.example")


@pytest.mark.parametrize("host", REBOUND)
@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
@pytest.mark.parametrize(
    "browser",
    [
        {"Sec-Fetch-Site": "same-origin"},
        {"Sec-Fetch-Site": "none"},
        {"Origin": "http://{host}"},
        {"Sec-Fetch-Site": "same-origin", "Origin": "http://{host}"},
    ],
)
async def test_a_browser_request_to_a_rebound_name_is_refused(
    client: httpx.AsyncClient, reached: list[str], host: str, method: str, browser: dict[str, str]
) -> None:
    """After a rebinding the attacker's page is same-origin with itself: Sec-Fetch-Site and
    Origin both look right, only Host is foreign."""
    headers = {name: value.format(host=host) for name, value in browser.items()}
    for path in ("/api/feeds", "/mcp", "/mcp/", "/feeds/x.xml", "/"):
        response = await client.request(method, path, headers={**headers, "Host": host})
        assert_refused(response, UNKNOWN_HOST_SLUG)
    assert reached == []


@pytest.mark.parametrize(
    "host",
    [
        "copycast.example",  # base_url's
        "Copycast.Example.",
        "copycast.example:8443",
        "internal:8080",  # COPYCAST_ALLOWED_HOSTS
        "localhost:5173",  # the Vite dev server
        "app.localhost",
        "127.0.0.1:8080",
        "[::1]:8080",
        "100.101.102.103",  # a tailnet address typed in the address bar
        "10.42.0.17:8080",  # a Kubernetes probe
    ],
)
async def test_a_browser_request_to_a_name_of_this_copycast_passes(
    client: httpx.AsyncClient, reached: list[str], host: str
) -> None:
    headers = {"Sec-Fetch-Site": "same-origin", "Host": host}
    assert (await client.post("/api/buckets", headers=headers)).status_code == 200
    assert (await client.get("/api/feeds", headers=headers)).status_code == 200
    assert reached == ["POST", "GET"]


@pytest.mark.parametrize("host", ["", "user@copycast.example", "copycast.example/x", "a b"])
async def test_a_browser_request_without_a_usable_host_is_refused(
    client: httpx.AsyncClient, host: str
) -> None:
    response = await client.get("/", headers={"Sec-Fetch-Site": "none", "Host": host})
    assert_refused(response, UNKNOWN_HOST_SLUG)


async def test_non_browser_clients_are_not_host_checked(
    client: httpx.AsyncClient, reached: list[str]
) -> None:
    # curl or an MCP client on a Docker network name: no Fetch Metadata, no Origin.
    assert (await client.post("/mcp", headers={"Host": "copycast-api:8080"})).status_code == 200
    assert reached == ["POST"]


async def test_a_rebound_origin_is_not_the_requests_own() -> None:
    """Without the host check the Origin would equal the request's own scheme://Host."""
    seen: list[str] = []
    transport = httpx.ASGITransport(app=_app(reached=seen, allowed_hosts=()))
    async with httpx.AsyncClient(transport=transport, base_url="http://evil.example") as http:
        response = await http.post("/api/buckets", headers={"Origin": "http://evil.example"})
    assert_refused(response, UNKNOWN_HOST_SLUG)
    assert seen == []


async def test_a_star_turns_the_host_check_off() -> None:
    seen: list[str] = []
    transport = httpx.ASGITransport(app=_app(reached=seen, allowed_hosts=("*",)))
    async with httpx.AsyncClient(transport=transport, base_url="http://any.example") as http:
        response = await http.post("/api/buckets", headers={"Sec-Fetch-Site": "same-origin"})
    assert response.status_code == 200
    assert seen == ["POST"]


async def test_a_host_refusal_is_logged(client: httpx.AsyncClient) -> None:
    with structlog.testing.capture_logs() as events:
        await client.get("/", headers={"Sec-Fetch-Site": "none", "Host": "evil.example"})
    (event,) = [e for e in events if e["event"] == "http.cross_site_refused"]
    assert "evil.example" in event["reason"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Copycast.Example:8080", "copycast.example"),
        ("copycast.example.", "copycast.example"),
        ("[::1]:8080", "::1"),
        ("127.0.0.1", "127.0.0.1"),
        ("", None),
        ("host:99999", None),
        ("user@host", None),
        ("host/path", None),
    ],
)
def test_host_name(value: str, expected: str | None) -> None:
    assert host_name(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://public.example/copycast", "https://public.example"),
        ("https://Public.Example:443/a/b/", "https://public.example"),
        ("http://copycast.tailnet:8080", "http://copycast.tailnet:8080"),
        ("not a url", None),
    ],
)
def test_base_origin_ignores_the_path(value: str, expected: str | None) -> None:
    assert base_origin(value) == expected


async def test_a_base_url_with_a_path_keeps_its_origin_hsts_and_policy() -> None:
    seen: list[str] = []
    base_url = "https://public.example/copycast"
    transport = httpx.ASGITransport(app=_app(base_url=base_url, reached=seen))
    async with httpx.AsyncClient(transport=transport, base_url="http://internal:8080") as http:
        page = await http.get("/")
        write = await http.post("/api/buckets", headers={"Origin": "https://public.example"})
        rebound = await http.post(
            "/api/buckets",
            headers={"Sec-Fetch-Site": "same-origin", "Host": "public.example"},
        )
    assert page.headers["strict-transport-security"] == HSTS_VALUE
    assert page.headers["content-security-policy"] == spa_policy("https://public.example")
    assert write.status_code == 200 and rebound.status_code == 200
    assert seen == ["GET", "POST", "POST"]


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
        "/feeds/abc/k/key/media/0123.mp4",
        "/feeds/abc/k/key/assets/x.vtt",
        "/feeds/abc/k/key/assets/x.html",
        "/feeds/copycast-artwork.png",
        "/art/TOKEN.jpg",
    ],
)
async def test_feeds_media_and_artwork_get_no_page_policy(
    client: httpx.AsyncClient, path: str
) -> None:
    headers = (await client.get(path)).headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    for absent in ("x-frame-options", "cross-origin-opener-policy", "strict-transport-security"):
        assert absent not in headers, (path, absent)
    assert headers.get("content-security-policy") in (None, PODCAST_POLICY)


@pytest.mark.parametrize(
    "path",
    [
        "/feeds/abc.xml",
        "/feeds/abc/k/key/assets/x.html",  # a mirrored HTML transcript
        "/feeds/abc/k/key/assets/x.vtt",
        "/feeds/copycast-artwork.png",
        "/art/TOKEN.jpg",
    ],
)
async def test_podcast_documents_are_sandboxed(client: httpx.AsyncClient, path: str) -> None:
    """A mirrored HTML transcript opened in a browser would otherwise run its scripts on
    Copycast's origin, where Sec-Fetch-Site: same-origin passes the guard."""
    assert (await client.get(path)).headers["content-security-policy"] == PODCAST_POLICY


@pytest.mark.parametrize("path", ["/feeds/abc/media/0123.mp3", "/feeds/abc/k/key/media/0123.mp4"])
async def test_audio_and_video_are_not_sandboxed(client: httpx.AsyncClient, path: str) -> None:
    assert "content-security-policy" not in (await client.get(path)).headers


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
