"""The isolation world: two Members and an Admin, with everything Bob can own.

:func:`build_world` runs over a users-mode app (``root`` the Admin, ``alice`` and ``bob``
Members, from :mod:`tests.support.users`) whose lifespan ran. As Bob it creates:

- a Mirror of the Origin's RSS fixture, whose first Refresh job stays queued;
- a Local feed with a Request expanded and archived by the FakeEngine (one archived item);
- a staged Upload and an API key;
- a failed Refresh run on his Mirror whose error text carries his Source URL.

Alice has her own default Local feed only; so has root. :class:`World` exposes every id,
title and the Source URL, so a test can assert that none of Bob's things reach Alice.

The fixtures ``world``, ``alice_client``, ``bob_client`` and ``root_client`` in
``tests/integration/api/conftest.py`` (and ``world`` in the storage conftest) build it.
Groups use it; nobody edits it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx

from copycast.adapters.api.app import create_app
from copycast.adapters.db.uow import uow_of
from copycast.app import Container, build_container
from copycast.domain.enums import JobTrigger, RefreshRunStatus
from copycast.settings import Settings, get_settings
from copycast.worker.runner import Runner
from tests.support.factories import EPOCH, listing, listing_item
from tests.support.fake_engine import FakeEngine
from tests.support.origin import Origin
from tests.support.paths import api
from tests.support.users import USERS, login, users_auth

WORLD_USERS: tuple[str, ...] = ("root", "alice", "bob")
"""Every User of the world, in configuration order (``root`` is the only Admin)."""

BOB_RSS_PATH = "rss/itunes_podcast20.xml"
"""The Origin fixture Bob mirrors."""

BOB_REQUEST_URL = "https://www.youtube.com/watch?v=bobsvideo01"
"""The URL Bob pushes into his Local feed (the FakeEngine lists one item for it)."""

BOB_ITEM_TITLE = "Bob's private video"
"""The title of the item Bob's Request archives (unlike any other title in the world)."""
BOB_BUCKET_NAME = "Bob's later"
BOB_UPLOAD_NAME = "bob-part-1.mp3"
BOB_KEY_NAME = "bob's agent"

ClientFactory = Callable[[], httpx.AsyncClient]


@dataclass(frozen=True, slots=True)
class World:
    """What :func:`build_world` made: every id and name of Bob's things, and the defaults."""

    source_url: str
    """Bob's Mirror's Source URL (also in his failed Refresh's error)."""
    mirror_id: str
    mirror_title: str
    bucket_id: str
    bucket_title: str
    item_id: str
    """The archived item of Bob's Local feed."""
    item_title: str
    request_id: str
    job_id: str
    """The queued first Refresh of Bob's Mirror."""
    upload_id: str
    key_id: str
    key_secret: str
    """Bob's API key (scope ``full``), usable as ``Authorization: Bearer``."""
    refresh_error: str
    """The error text of Bob's failed Refresh run."""
    defaults: dict[str, str] = field(default_factory=dict[str, str])
    """Each User's default Local feed id."""

    @property
    def bob_ids(self) -> tuple[str, ...]:
        """Every id of Bob's that another Member must never see."""
        return (
            self.mirror_id,
            self.bucket_id,
            self.item_id,
            self.request_id,
            self.job_id,
            self.upload_id,
            self.key_id,
            self.defaults["bob"],
        )

    @property
    def bob_texts(self) -> tuple[str, ...]:
        """Every title and URL of Bob's that another Member must never see."""
        return (self.source_url, self.mirror_title, self.bucket_title, self.item_title)

    def leaks(self, body: str) -> list[str]:
        """The ids and texts of Bob's found in ``body`` (empty when nothing leaks)."""
        return [needle for needle in (*self.bob_ids, *self.bob_texts) if needle in body]


def _container(app: Any) -> Container:
    container: Container = app.state.container
    return container


def _ok(response: httpx.Response, *statuses: int) -> dict[str, Any]:
    assert response.status_code in statuses, response.text
    body: dict[str, Any] = response.json()
    return body


async def build_world(app: Any, client_factory: ClientFactory, *, origin: Origin) -> World:
    """Fill the users-mode ``app`` with Bob's things (see the module docstring).

    ``client_factory`` makes a fresh client of ``app`` (Bob signs in on his own, so the
    caller's clients keep their cookies); ``origin`` serves ``tests/fixtures``.
    """
    container = _container(app)
    engine = container.engine
    assert isinstance(engine, FakeEngine), "the world needs the FakeEngine"
    # The users rows first, so repository-level tests may rely on them (the lifespan's
    # sync_users wrote them already; this is idempotent).
    async with uow_of(container.uow_factory()) as uow:
        await uow.users.ensure(WORLD_USERS)

    async with client_factory() as bob:
        await login(bob, "bob")

        bucket = _ok(await bob.post(api("/buckets"), json={"name": BOB_BUCKET_NAME}), 201)
        engine.script_listing(
            BOB_REQUEST_URL,
            listing(
                1,
                service="YouTube",
                title="Bob's channel",
                items=[
                    listing_item(
                        1,
                        key="youtube:bobsvideo01",
                        title=BOB_ITEM_TITLE,
                        source_url=BOB_REQUEST_URL,
                        published_at=EPOCH,
                    )
                ],
            ),
        )
        request = _ok(
            await bob.post(api(f"/buckets/{bucket['id']}/requests"), json={"url": BOB_REQUEST_URL}),
            202,
        )
        runner = Runner(
            container,
            worker_id="world-worker",
            concurrency=1,
            listen=False,
            claim_tick=0.05,
            monitor_tick=0.05,
        )
        await runner.run_until_idle()
        items = _ok(
            await bob.get(
                api(f"/feeds/{bucket['id']}/items"), params={"state": "archived", "limit": 10}
            ),
            200,
        )["items"]
        assert len(items) == 1, items
        item = items[0]

        source_url = origin.url_for(BOB_RSS_PATH)
        mirror = _ok(
            await bob.post(
                api("/mirrors"), json={"source_url": source_url, "backfill": {"mode": "new"}}
            ),
            201,
        )
        jobs = _ok(await bob.get(api("/jobs"), params={"feed_id": mirror["id"], "limit": 10}), 200)[
            "jobs"
        ]
        assert jobs, "the Mirror's first Refresh is queued"
        job = jobs[0]

        upload = _ok(await bob.post(api("/uploads"), json={"filename": BOB_UPLOAD_NAME}), 201)
        key = _ok(await bob.post(api("/keys"), json={"name": BOB_KEY_NAME, "scope": "full"}), 201)

        defaults = {
            name: feed["id"]
            for name in WORLD_USERS
            for feed in await _feeds_of(container, name)
            if feed.get("is_default")
        }

    refresh_error = f"HTTP 503 from {source_url}"
    async with uow_of(container.uow_factory()) as uow:
        run = await uow.telemetry.start_refresh_run(mirror["id"], trigger=JobTrigger.scheduled)
        await uow.telemetry.finish_refresh_run(run.id, RefreshRunStatus.failed, error=refresh_error)
        await uow.feeds.set_refresh_outcome(mirror["id"], success_at=None, error=refresh_error)

    return World(
        source_url=source_url,
        mirror_id=mirror["id"],
        mirror_title=mirror["title"],
        bucket_id=bucket["id"],
        bucket_title=bucket["name"],
        item_id=item["id"],
        item_title=item["title"],
        request_id=request["id"],
        job_id=job["id"],
        upload_id=upload["id"],
        key_id=key["key"]["id"],
        key_secret=key["secret"],
        refresh_error=refresh_error,
        defaults=defaults,
    )


async def _feeds_of(container: Container, owner: str) -> list[dict[str, Any]]:
    """``owner``'s Feeds as the services list them for SYSTEM, filtered on the owner."""
    page = await container.services.list_feeds()
    return [feed.model_dump(mode="json") for feed in page.feeds if feed.owner == owner]


@asynccontextmanager
async def signed_in(
    client_factory: ClientFactory, username: str
) -> AsyncIterator[httpx.AsyncClient]:
    """A fresh client of the app, signed in as ``username``."""
    async with client_factory() as client:
        await login(client, username)
        yield client


@asynccontextmanager
async def world_clients(
    client_factory: ClientFactory,
) -> AsyncIterator[dict[str, httpx.AsyncClient]]:
    """One signed-in client per User of the world, keyed by username."""
    async with AsyncExitStack() as stack:
        clients = {
            name: await stack.enter_async_context(signed_in(client_factory, name))
            for name in WORLD_USERS
        }
        yield clients


def users_mode(settings: Settings) -> Settings:
    """``settings`` (this test's database and Object store) in the world's users mode."""
    return get_settings(
        base_url=settings.base_url,
        storage=settings.storage.model_dump(),
        database_url=settings.database_url,
        refresh=settings.refresh.model_dump(),
        security=settings.security.model_dump(),
        auth=users_auth(users=WORLD_USERS),
    )


@asynccontextmanager
async def running_world(
    settings: Settings, engine: FakeEngine, origin: Origin
) -> AsyncIterator[tuple[Any, World]]:
    """A users-mode app over ``settings``' database whose lifespan runs, and its world.

    For tests below the API (repositories) that still want Bob's rows.
    """
    container = build_container(users_mode(settings), engine=engine)
    try:
        application = create_app(container.settings, container)
        async with application.router.lifespan_context(application):

            def factory() -> httpx.AsyncClient:
                return httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=application), base_url=settings.base_url
                )

            yield application, await build_world(application, factory, origin=origin)
    finally:
        await container.aclose()


def new_upload_id() -> str:
    """An upload id nobody created (for 404 checks)."""
    return uuid.uuid4().hex


__all__ = [
    "BOB_BUCKET_NAME",
    "BOB_ITEM_TITLE",
    "BOB_KEY_NAME",
    "BOB_REQUEST_URL",
    "BOB_RSS_PATH",
    "BOB_UPLOAD_NAME",
    "USERS",
    "WORLD_USERS",
    "ClientFactory",
    "World",
    "build_world",
    "new_upload_id",
    "running_world",
    "signed_in",
    "users_mode",
    "world_clients",
]
