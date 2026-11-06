"""Users for the tests: fast argon2 hashes, a login helper and a Principal override.

``USERS`` holds the three Users of users-mode tests (``root`` the Admin, ``alice`` and
``bob`` Members) with their plaintext passwords; :func:`users_auth` turns them into
``auth`` settings. :func:`login` signs a client in through ``POST /auth/login`` (the
cookie stays in the client); :func:`as_principal` installs a Principal on an app
without any login, for tests about what a User may do rather than how they sign in.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from copycast.adapters.api.principal import PrincipalMiddleware
from copycast.application.passwords import PasswordHasher
from copycast.domain.enums import KeyScope, PrincipalVia
from copycast.domain.totp import generate_secret
from copycast.domain.users import Principal

TEST_HASHER = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
"""argon2id with the cheapest parameters: a verify takes about a millisecond."""


@dataclass(frozen=True, slots=True)
class TestUser:
    username: str
    password: str
    admin: bool = False
    totp_secret: str | None = None

    __test__ = False  # not a pytest class


USERS: dict[str, TestUser] = {
    "root": TestUser("root", "root-password-123", admin=True),
    "alice": TestUser("alice", "alice-password-123"),
    "bob": TestUser("bob", "bob-password-123"),
}
TOTP_SECRET = generate_secret()
"""A TOTP secret tests may give a User (``users_auth(totp={"root": TOTP_SECRET})``)."""

_HASHES: dict[str, str] = {}


def password_hash(password: str) -> str:
    """The (memoised) fast argon2id hash of ``password``."""
    if password not in _HASHES:
        _HASHES[password] = TEST_HASHER.hash(password)
    return _HASHES[password]


def user_entry(user: TestUser, **overrides: Any) -> dict[str, Any]:
    """One ``[[auth.users]]`` entry for ``user``."""
    entry: dict[str, Any] = {
        "username": user.username,
        "password_hash": password_hash(user.password),
        "admin": user.admin,
    }
    if user.totp_secret is not None:
        entry["totp_secret"] = user.totp_secret
    entry.update(overrides)
    return entry


def users_auth(
    *,
    users: tuple[str, ...] = ("root", "alice", "bob"),
    totp: dict[str, str] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """``auth`` settings for users mode: ``users`` from :data:`USERS`, with ``totp`` secrets."""
    totp = totp or {}
    entries = [
        user_entry(USERS[name], **({"totp_secret": totp[name]} if name in totp else {}))
        for name in users
    ]
    return {"users": entries, **overrides}


async def login(
    client: httpx.AsyncClient,
    username: str,
    password: str | None = None,
    *,
    totp: str | None = None,
) -> httpx.Response:
    """``POST /auth/login`` as ``username`` (its :data:`USERS` password by default).

    Asserts 204; the session cookie stays in ``client``'s jar.
    """
    body: dict[str, Any] = {
        "username": username,
        "password": password if password is not None else USERS[username].password,
    }
    if totp is not None:
        body["totp"] = totp
    response = await client.post("/auth/login", json=body)
    assert response.status_code == 204, response.text
    return response


async def session_headers(
    client: httpx.AsyncClient,
    username: str,
    password: str | None = None,
    *,
    totp: str | None = None,
) -> dict[str, str]:
    """Sign in, then return the session as a ``Cookie`` header and empty the client's jar.

    For tests that send some requests with the session and others without it.
    """
    response = await login(client, username, password, totp=totp)
    cookie = response.headers["set-cookie"].split(";", 1)[0]
    client.cookies.clear()
    return {"Cookie": cookie}


async def mint_key(client: httpx.AsyncClient, scope: str = "write", name: str = "test") -> str:
    """Mint an API key through ``POST /api/keys`` (the client must be signed in)."""
    response = await client.post("/api/keys", json={"name": name, "scope": scope})
    assert response.status_code == 201, response.text
    secret: str = response.json()["secret"]
    return secret


def principal_middleware(app: Any) -> PrincipalMiddleware:
    """The app's ``PrincipalMiddleware`` instance (building the middleware stack if needed)."""
    if app.middleware_stack is None:
        app.middleware_stack = app.build_middleware_stack()
    layer: Any = app.middleware_stack
    while layer is not None:
        if isinstance(layer, PrincipalMiddleware):
            return layer
        layer = getattr(layer, "app", None)
    raise LookupError("the app has no PrincipalMiddleware")


@contextmanager
def as_principal(
    app: Any,
    username: str,
    *,
    admin: bool | None = None,
    via: PrincipalVia = PrincipalVia.session,
    key_scope: KeyScope | None = None,
) -> Iterator[Principal]:
    """Every request to ``app`` acts as ``username`` until the block ends, without a login.

    The role comes from the app's configuration unless ``admin`` says otherwise.
    """
    middleware = principal_middleware(app)
    directory = middleware.container.users
    configured = directory.principal(username, via=via, key_scope=key_scope)
    principal = Principal(
        username=username,
        admin=admin if admin is not None else bool(configured and configured.admin),
        via=via,
        key_scope=key_scope,
    )

    async def resolve(scope: Any, path: str) -> Principal | None:
        del scope, path
        return principal

    middleware.resolve = resolve  # type: ignore[method-assign]
    try:
        yield principal
    finally:
        del middleware.resolve


__all__ = [
    "TEST_HASHER",
    "TOTP_SECRET",
    "USERS",
    "TestUser",
    "as_principal",
    "login",
    "mint_key",
    "password_hash",
    "principal_middleware",
    "session_headers",
    "user_entry",
    "users_auth",
]
