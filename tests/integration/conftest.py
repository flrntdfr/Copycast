"""Fixtures every integration suite shares: users mode and the isolation world.

``users_settings``, ``users_container``, ``users_app`` and ``users_client`` are the root
``settings``, container, app and client in users mode (``root`` the Admin, ``alice`` and
``bob`` Members, from ``tests.support.users``); ``users_client_factory`` makes more
clients of that app, each with its own cookie jar. ``world`` fills ``users_app`` with
Bob's things (``tests.support.world``), and ``alice_client``, ``bob_client`` and
``root_client`` are signed-in clients of it. :func:`running` starts an app for any other
settings and :func:`settings_with` derives such settings from this test's.

They live here, not in ``api/conftest.py``, so the MCP, worker and storage suites get
them too (``storage/conftest.py`` overrides ``world`` with a database-only one).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from copycast.adapters.api.app import create_app
from copycast.app import Container, build_container
from copycast.settings import Settings, get_settings
from tests.support.fake_engine import FakeEngine
from tests.support.origin import Origin
from tests.support.users import users_auth
from tests.support.world import ClientFactory, World, build_world, signed_in


def settings_with(settings: Settings, **changes: Any) -> Settings:
    """``settings`` (this test's database and Object store) with other values, validated."""
    values: dict[str, Any] = {
        "base_url": settings.base_url,
        "storage": settings.storage.model_dump(),
        "database_url": settings.database_url,
        "refresh": settings.refresh.model_dump(),
        "security": settings.security.model_dump(),
    }
    values.update(changes)
    return get_settings(**values)


@asynccontextmanager
async def running(
    settings: Settings, engine: FakeEngine | None = None, *, base_url: str | None = None
) -> AsyncIterator[tuple[Container, httpx.AsyncClient]]:
    """A container and a client of an app whose lifespan runs, for any settings."""
    container = build_container(settings, engine=engine or FakeEngine())
    try:
        application = create_app(settings, container)
        async with application.router.lifespan_context(application):
            transport = httpx.ASGITransport(app=application)
            async with httpx.AsyncClient(
                transport=transport, base_url=base_url or settings.base_url
            ) as http:
                yield container, http
    finally:
        await container.aclose()


@pytest.fixture
def users_settings(settings: Settings, db: str) -> Settings:
    """The root ``settings`` in users mode: root (Admin), alice and bob (Members)."""
    return settings_with(settings, auth=users_auth())


@pytest.fixture
async def users_container(users_settings: Settings, engine: FakeEngine) -> AsyncIterator[Container]:
    container = build_container(users_settings, engine=engine)
    try:
        yield container
    finally:
        await container.aclose()


@pytest.fixture
async def users_app(users_settings: Settings, users_container: Container) -> AsyncIterator[Any]:
    application = create_app(users_settings, users_container)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def users_client(users_app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=users_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest.fixture
def users_client_factory(users_app: Any) -> ClientFactory:
    """Fresh clients of ``users_app``, each with its own cookie jar."""

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=users_app), base_url="http://testserver"
        )

    return factory


@pytest.fixture
async def world(users_app: Any, users_client_factory: ClientFactory, origin: Origin) -> World:
    """Bob's things in ``users_app`` (``tests.support.world``)."""
    return await build_world(users_app, users_client_factory, origin=origin)


@pytest.fixture
async def alice_client(
    world: World, users_client_factory: ClientFactory
) -> AsyncIterator[httpx.AsyncClient]:
    async with signed_in(users_client_factory, "alice") as client:
        yield client


@pytest.fixture
async def bob_client(
    world: World, users_client_factory: ClientFactory
) -> AsyncIterator[httpx.AsyncClient]:
    async with signed_in(users_client_factory, "bob") as client:
        yield client


@pytest.fixture
async def root_client(
    world: World, users_client_factory: ClientFactory
) -> AsyncIterator[httpx.AsyncClient]:
    async with signed_in(users_client_factory, "root") as client:
        yield client


__all__ = ["running", "settings_with"]
