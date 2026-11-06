"""Fixtures for the API suite: the app with its lifespan, an RSS Source, a worker runner.

Every test gets the shared ``client`` (httpx over the FastAPI app whose
lifespan ran: EventHub listening, default Bucket created) and a
:class:`Source` served from a temp directory. Archiving runs through the
worker ``Runner`` with the FakeEngine, so media files really exist on disk.

``users_settings``, ``users_container``, ``users_app`` and ``users_client`` are the
same in users mode (``root`` the Admin, ``alice`` and ``bob`` Members, from
``tests.support.users``); :func:`running` starts an app for any other settings.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from copycast.adapters.api.app import create_app
from copycast.app import Container, build_container
from copycast.settings import Settings, get_settings
from copycast.worker.runner import Runner
from tests.integration.worker.conftest import Source
from tests.support.fake_engine import FakeEngine
from tests.support.origin import FIXTURES_DIR, Origin
from tests.support.paths import api
from tests.support.users import users_auth

pytestmark = pytest.mark.integration


@pytest.fixture
def source(tmp_path: Path) -> Iterator[Source]:
    root = tmp_path / "origin"
    (root / "media").mkdir(parents=True)
    (root / "assets").mkdir(parents=True)
    shutil.copy(FIXTURES_DIR / "media" / "tiny.mp3", root / "media" / "tiny.mp3")
    shutil.copy(FIXTURES_DIR / "media" / "tiny.jpg", root / "media" / "tiny.jpg")
    (root / "assets" / "chapters.json").write_text(
        '{"version": "1.2.0", "chapters": [{"startTime": 0, "title": "Intro"}]}', encoding="utf-8"
    )
    (root / "assets" / "transcript.vtt").write_text(
        "WEBVTT\n\n00:00.000 --> 00:01.000\nHello\n", encoding="utf-8"
    )
    with Origin(root=root) as origin:
        yield Source(origin=origin, root=root)


@pytest.fixture
def runner(container: Container) -> Runner:
    return Runner(
        container,
        worker_id="api-test-worker",
        concurrency=2,
        listen=False,
        claim_tick=0.05,
        monitor_tick=0.05,
    )


async def create_mirror(
    client: httpx.AsyncClient,
    url: str,
    *,
    mode: str = "all",
    latest_n: int | None = None,
    selection: str | None = None,
    follow: bool | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """POST /api/mirrors and return the MirrorRead (asserting 201)."""
    backfill: dict[str, Any] = {"mode": "selection" if selection is not None else mode}
    if latest_n is not None:
        backfill["latest_n"] = latest_n
    if selection is not None:
        backfill["selection"] = selection
    body: dict[str, Any] = {"source_url": url, "backfill": backfill, **overrides}
    if follow is not None:
        body["follow"] = follow
    response = await client.post(api("/mirrors"), json=body)
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


async def items_of(client: httpx.AsyncClient, feed_id: str, **params: Any) -> list[dict[str, Any]]:
    response = await client.get(api(f"/feeds/{feed_id}/items"), params={"limit": 500, **params})
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


async def archived_items(
    client: httpx.AsyncClient, runner: Runner, feed_id: str
) -> list[dict[str, Any]]:
    """Run every queued job, then return the feed's archived items."""
    await runner.run_until_idle()
    return await items_of(client, feed_id, state="archived")


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


__all__ = [
    "Source",
    "archived_items",
    "create_mirror",
    "items_of",
    "running",
    "settings_with",
]
