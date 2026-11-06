"""HTTP Basic parsing, the operator check, the Principal middleware and the session cookie."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from copycast.adapters.api import auth, principal, session_cookie
from copycast.adapters.api.problems import challenges
from copycast.application.services.auth import IssuedSession
from copycast.application.users import UserDirectory
from copycast.domain.enums import KeyScope, PrincipalVia
from copycast.domain.users import Principal
from copycast.settings import AuthSettings, Settings, get_settings
from tests.support.users import users_auth


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
    # Users mode has no operator pair at all.
    users = AuthSettings(**users_auth())
    assert not auth.is_operator(users, auth.BasicCredentials("copycast", "correct-horse"))


@pytest.mark.parametrize(
    ("path", "public"),
    [
        ("/art/0123456789abcdef-abcdefghijklmnopqrstuvwxyz.jpg", True),
        ("/feeds/copycast-artwork.png", True),
        # The old artwork path is computable from a Source URL: it takes the feed's pair now.
        ("/feeds/abc/assets/feed.artwork.jpg", False),
        ("/feeds/abc/assets/0123456789abcdef.artwork.jpg", False),
        ("/feeds/abc/k/key/assets/feed.artwork.jpg", False),
        ("/art", False),
        ("/artwork/x", False),
    ],
)
def test_is_public_artwork(path: str, public: bool) -> None:
    assert auth.is_public_artwork(path) is public


@pytest.mark.parametrize(
    ("path", "gated"),
    [
        ("/api", True),
        ("/api/feeds", True),
        ("/api/events", True),
        ("/apix", False),
        ("/", False),
        ("/login", False),
        ("/auth/login", False),
        ("/auth/logout", False),
        ("/healthz", False),
        ("/feeds/abc.xml", False),
        ("/mcp", False),
        ("/uploads/abc/token", False),
        ("/art/x.jpg", False),
    ],
)
def test_requires_principal(path: str, gated: bool) -> None:
    assert principal.requires_principal(path) is gated


@pytest.mark.parametrize(
    ("header", "expected"),
    [(None, None), ("Basic abc", None), ("Bearer", None), ("bearer  cck_x ", "cck_x")],
)
def test_bearer_token(header: str | None, expected: str | None) -> None:
    headers = Headers({"authorization": header} if header is not None else {})
    assert principal.bearer_token(headers) == expected


@dataclass
class FakeKey:
    id: UUID
    owner: str
    scope: KeyScope


@dataclass
class FakeServices:
    sessions: dict[str, Principal] = field(default_factory=dict[str, Principal])
    keys: dict[str, FakeKey] = field(default_factory=dict[str, FakeKey])
    session_calls: list[str] = field(default_factory=list[str])

    async def authenticate_session(self, token: str) -> Principal | None:
        self.session_calls.append(token)
        return self.sessions.get(token)

    async def authenticate_api_key(self, secret: str) -> FakeKey | None:
        return self.keys.get(secret)


@dataclass
class FakeContainer:
    settings: Settings
    services: FakeServices = field(default_factory=FakeServices)

    @property
    def users(self) -> UserDirectory:
        return UserDirectory(self.settings)


def _app(container: FakeContainer) -> Starlette:
    async def who(request: Request) -> JSONResponse:
        found = principal.principal_of(request.scope)
        return JSONResponse(
            None if found is None else {"username": found.username, "via": found.via.value}
        )

    app = Starlette(routes=[Route("/api/x", who), Route("/feeds/abc.xml", who), Route("/", who)])
    app.add_middleware(principal.PrincipalMiddleware, container=container)  # type: ignore[arg-type]
    return app


def _client(container: FakeContainer) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=_app(container))
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_open_mode_is_the_implicit_admin_everywhere() -> None:
    container = FakeContainer(get_settings(base_url="http://t"))
    async with _client(container) as client:
        for path in ("/api/x", "/feeds/abc.xml", "/"):
            body = (await client.get(path)).json()
            assert body == {"username": "copycast", "via": "open"}, path
    assert container.services.session_calls == []  # no database access in open mode


async def test_api_without_a_principal_is_401_without_a_challenge() -> None:
    container = FakeContainer(get_settings(base_url="http://t", auth=users_auth()))
    async with _client(container) as client:
        missing = await client.get("/api/x")
        assert missing.status_code == 401
        assert "www-authenticate" not in missing.headers
        assert missing.headers["cache-control"] == "no-store"
        assert missing.headers["content-type"].startswith("application/problem+json")
        body = json.loads(missing.content)
        assert body["type"] == "urn:copycast:problem:unauthorized"
        assert body["instance"] == "/api/x"
        # Elsewhere the middleware gates nothing: the route sees no Principal.
        assert (await client.get("/feeds/abc.xml")).json() is None
        assert (await client.get("/")).json() is None


async def test_a_session_cookie_resolves_on_any_path() -> None:
    container = FakeContainer(get_settings(base_url="http://t", auth=users_auth()))
    alice = Principal("alice", False, PrincipalVia.session)
    container.services.sessions["good"] = alice
    async with _client(container) as client:
        for path in ("/api/x", "/feeds/abc.xml"):
            response = await client.get(path, headers={"Cookie": "copycast_session=good"})
            assert response.json() == {"username": "alice", "via": "session"}, path
        # The https cookie name is not the http one.
        wrong_name = await client.get("/api/x", headers={"Cookie": "__Host-copycast_session=good"})
        assert wrong_name.status_code == 401


async def test_a_bearer_key_resolves_on_api_only_and_after_a_dead_cookie() -> None:
    container = FakeContainer(get_settings(base_url="http://t", auth=users_auth()))
    secret = "cck_" + "a" * 40
    key = FakeKey(uuid4(), "bob", KeyScope.read)
    container.services.keys[secret] = key
    removed = "cck_" + "b" * 40
    container.services.keys[removed] = FakeKey(uuid4(), "gone", KeyScope.full)
    bearer = {"Authorization": f"Bearer {secret}"}
    async with _client(container) as client:
        on_api = await client.get("/api/x", headers=bearer)
        assert on_api.json() == {"username": "bob", "via": "api_key"}
        # A cookie naming no session falls through to the key.
        stale = await client.get("/api/x", headers={**bearer, "Cookie": "copycast_session=stale"})
        assert stale.json() == {"username": "bob", "via": "api_key"}
        # Keys never open anything outside /api (feeds take their own pair).
        assert (await client.get("/feeds/abc.xml", headers=bearer)).json() is None
        # A key whose owner is no longer configured, or no key at all, is refused.
        gone = await client.get("/api/x", headers={"Authorization": f"Bearer {removed}"})
        assert gone.status_code == 401
        junk = await client.get("/api/x", headers={"Authorization": "Bearer nope"})
        assert junk.status_code == 401


def _issued(days: int = 30) -> IssuedSession:
    return IssuedSession(token="tok", expires_at=datetime.now(UTC) + timedelta(days=days))


def test_the_cookie_on_http() -> None:
    settings = get_settings(base_url="http://copycast.lan:8080", auth=users_auth())
    response = Response()
    session_cookie.set_session_cookie(response, _issued(), settings)
    header = response.headers["set-cookie"]
    assert header.startswith("copycast_session=tok;")
    assert "HttpOnly" in header and "Path=/" in header and "SameSite=lax" in header
    assert "Max-Age=259" in header  # 30 days, give or take the second it took
    assert "Secure" not in header
    assert (
        session_cookie.read_session_token(
            Headers({"cookie": "a=b; copycast_session=tok"}), settings
        )
        == "tok"
    )
    assert session_cookie.read_session_token(Headers({}), settings) is None
    empty = Headers({"cookie": "copycast_session="})
    assert session_cookie.read_session_token(empty, settings) is None


def test_the_cookie_on_https() -> None:
    settings = get_settings(base_url="https://copycast.example", auth=users_auth(session_days=7))
    assert session_cookie.cookie_name(settings) == "__Host-copycast_session"
    response = Response()
    session_cookie.set_session_cookie(response, _issued(7), settings)
    header = response.headers["set-cookie"]
    assert header.startswith("__Host-copycast_session=tok;")
    assert "Secure" in header and "HttpOnly" in header and "Path=/" in header
    assert "Domain" not in header  # __Host- forbids it
    assert "Max-Age=604" in header
    cleared = Response()
    session_cookie.clear_session_cookie(cleared, settings)
    assert "__Host-copycast_session=" in cleared.headers["set-cookie"]
    assert "Max-Age=0" in cleared.headers["set-cookie"]
    assert "Secure" in cleared.headers["set-cookie"]


def test_only_feeds_carry_the_basic_challenge() -> None:
    assert challenges("/feeds/abc.xml")
    assert not challenges("/api/feeds")
    assert not challenges("/auth/login")
    assert not challenges(None)
    feed = auth.unauthorized_response("/feeds/abc.xml", "x")
    assert feed.headers["www-authenticate"].startswith("Basic")
    api = auth.unauthorized_response("/api/feeds", "x")
    assert "www-authenticate" not in api.headers
