"""The scoped repositories: a Member sees their own rows only, an Admin and SYSTEM all.

Every read that can return another Owner's row filters on ``scope.visible_owner``; a
row filtered out behaves exactly like a missing one. API keys go by actor (an Admin's
too); ``by_dedup_key`` and ``default_bucket`` go by the Owner they name, never by
visibility, so two Owners of one Source or two default Local feeds never collide.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from copycast.adapters.db.base import utcnow
from copycast.adapters.db.uow import UnitOfWorkFactory
from copycast.application.services.context import Scope
from copycast.domain.enums import JobKind, JobStatus, JobTrigger
from copycast.domain.exceptions import NotFound
from copycast.domain.ids import owned_mirror_id
from tests.integration.storage.conftest import mirror_row
from tests.support.world import World

pytestmark = pytest.mark.integration

ALICE = Scope("alice", "alice")
BOB = Scope("bob", "bob")
ROOT = Scope("root", None)
"""An Admin session: stamps its own name, sees every Owner."""
ROOT_KEY = Scope("root", "root")
"""An Admin's API key: sees the Admin's own Feeds only."""
SEES_ALL = (ROOT, Scope.SYSTEM)


async def test_feeds_are_hidden_from_other_members(
    world: World, uow_factory: UnitOfWorkFactory
) -> None:
    async with uow_factory(ALICE) as uow:
        assert await uow.feeds.get(world.mirror_id) is None
        with pytest.raises(NotFound):
            await uow.feeds.require(world.mirror_id)
        with pytest.raises(NotFound):
            await uow.feeds.get_for_update(world.mirror_id)
        with pytest.raises(NotFound):
            await uow.feeds.try_get_for_update(world.bucket_id)
        assert await uow.feeds.find_bucket(world.bucket_id) is None
        assert await uow.feeds.find_bucket(world.bucket_title) is None
        assert [feed.id for feed in await uow.feeds.list()] == [world.defaults["alice"]]
        assert await uow.feeds.ids() == [world.defaults["alice"]]
        assert await uow.feeds.count() == 1
        totals = await uow.feeds.totals()
        assert (totals.feeds, totals.episodes, totals.storage_bytes) == (1, 0, 0)
        # Unscoped by design: the id-collision check of create_mirror.
        assert await uow.feeds.exists_id(world.mirror_id)
    async with uow_factory(ROOT_KEY) as uow:
        assert await uow.feeds.get(world.mirror_id) is None
        assert await uow.feeds.ids() == [world.defaults["root"]]


@pytest.mark.parametrize("scope", [BOB, *SEES_ALL], ids=["bob", "root", "system"])
async def test_owners_and_admins_see_bobs_feeds(
    world: World, uow_factory: UnitOfWorkFactory, scope: Scope
) -> None:
    async with uow_factory(scope) as uow:
        assert (await uow.feeds.require(world.mirror_id)).owner == "bob"
        assert (await uow.feeds.get_for_update(world.bucket_id)).id == world.bucket_id
        assert (await uow.feeds.try_get_for_update(world.mirror_id)) is not None
        found = await uow.feeds.find_bucket(world.bucket_title)
        assert found is not None and found.id == world.bucket_id
        ids = set(await uow.feeds.ids())
        assert {world.mirror_id, world.bucket_id, world.defaults["bob"]} <= ids
        totals = await uow.feeds.totals()
        assert totals.episodes == 1 and totals.storage_bytes > 0


async def test_find_bucket_prefers_the_actors_own_by_name(
    world: World, uow_factory: UnitOfWorkFactory
) -> None:
    async with uow_factory(ROOT) as uow:
        found = await uow.feeds.find_bucket("Copycast")
        assert found is not None and found.id == world.defaults["root"]
    async with uow_factory(ALICE) as uow:
        found = await uow.feeds.find_bucket("copycast")
        assert found is not None and found.id == world.defaults["alice"]


async def test_dedup_keys_and_defaults_go_by_owner_with_two_owners(
    world: World, uow_factory: UnitOfWorkFactory
) -> None:
    async with uow_factory(BOB) as uow:
        key = (await uow.feeds.require(world.mirror_id)).source_dedup_key
    assert key is not None
    alices = mirror_row(world.source_url, id=owned_mirror_id("alice", key), owner="alice")
    async with uow_factory(ALICE) as uow:
        await uow.feeds.add(alices)
    for scope in (ALICE, ROOT, Scope.SYSTEM):
        async with uow_factory(scope) as uow:
            mine = await uow.feeds.by_dedup_key("alice", key)
            his = await uow.feeds.by_dedup_key("bob", key)
            assert mine is not None and mine.id == alices.id
            assert his is not None and his.id == world.mirror_id
            for name in ("root", "alice", "bob"):
                default = await uow.feeds.default_bucket(name)
                assert default is not None and default.id == world.defaults[name]


async def test_catalog_items_follow_their_feed(
    world: World, uow_factory: UnitOfWorkFactory
) -> None:
    async with uow_factory(ALICE) as uow:
        assert await uow.catalog.get(world.item_id) is None
        with pytest.raises(NotFound):
            await uow.catalog.require(world.item_id)
        with pytest.raises(NotFound):
            await uow.catalog.require(world.item_id, world.bucket_id)
        assert await uow.catalog.get_many([world.item_id]) == []
        assert await uow.catalog.for_feed(world.bucket_id) == []
    for scope in (BOB, *SEES_ALL):
        async with uow_factory(scope) as uow:
            assert (await uow.catalog.require(world.item_id, world.bucket_id)).id == world.item_id
            assert [row.id for row in await uow.catalog.get_many([world.item_id])] == [
                world.item_id
            ]
            assert [row.id for row in await uow.catalog.for_feed(world.bucket_id)] == [
                world.item_id
            ]


async def test_requests_follow_their_local_feed(
    world: World, uow_factory: UnitOfWorkFactory
) -> None:
    request_id = uuid.UUID(world.request_id)
    async with uow_factory(ALICE) as uow:
        assert await uow.requests.get(request_id) is None
        with pytest.raises(NotFound):
            await uow.requests.require(request_id, world.bucket_id)
        assert await uow.requests.for_feed(world.bucket_id) == []
        assert await uow.requests.list_page(world.bucket_id) == ([], 0)
    for scope in (BOB, *SEES_ALL):
        async with uow_factory(scope) as uow:
            assert (await uow.requests.require(request_id, world.bucket_id)).id == request_id
            rows, total = await uow.requests.list_page(world.bucket_id)
            assert total == 1 and [row.id for row in rows] == [request_id]
            assert [row.id for row in await uow.requests.for_feed(world.bucket_id)] == [request_id]


async def test_jobs_are_scoped_to_the_feeds_and_their_requester(
    world: World, uow_factory: UnitOfWorkFactory
) -> None:
    job_id = uuid.UUID(world.job_id)
    now = utcnow()
    async with uow_factory(ALICE) as uow:
        assert await uow.jobs.get(job_id) is None
        with pytest.raises(NotFound):
            await uow.jobs.require(job_id)
        with pytest.raises(NotFound):
            await uow.jobs.cancel(job_id)
        assert await uow.jobs.list_page() == ([], 0)
        assert await uow.jobs.list_page(feed_id=world.mirror_id) == ([], 0)
        assert await uow.jobs.active() == []
        assert await uow.jobs.latest_for_item(world.item_id) is None
        stats = await uow.jobs.queue_stats(now=now, since=now - timedelta(days=1))
        assert (stats.queued, stats.running, stats.failed_recently) == (0, 0, 0)
        # A feed-less job she requests is hers (and stamped with her name).
        own = await uow.jobs.enqueue(JobKind.rebuild, JobTrigger.manual, dedup="rebuild:test")
        assert own is not None and own.requested_by == "alice"
        own_id = own.id
    async with uow_factory(ALICE) as uow:
        assert [job.id for job in (await uow.jobs.list_page())[0]] == [own_id]
    async with uow_factory(BOB) as uow:
        assert await uow.jobs.get(own_id) is None
        assert (await uow.jobs.require(job_id)).feed_id == world.mirror_id
        assert job_id in {job.id for job in await uow.jobs.active()}
        assert (await uow.jobs.latest_for_item(world.item_id)) is not None
        stats = await uow.jobs.queue_stats(now=now, since=now - timedelta(days=1))
        assert stats.queued + stats.waiting >= 1
    for scope in SEES_ALL:
        async with uow_factory(scope) as uow:
            ids = {job.id for job in (await uow.jobs.list_page(limit=500))[0]}
            assert {job_id, own_id} <= ids
    async with uow_factory(BOB) as uow:
        cancelled = await uow.jobs.cancel(job_id)
        assert cancelled.status == JobStatus.cancelled.value


async def test_system_enqueues_with_no_requester(
    world: World, uow_factory: UnitOfWorkFactory
) -> None:
    async with uow_factory() as uow:
        job = await uow.jobs.enqueue(
            JobKind.refresh, JobTrigger.scheduled, feed_id=world.defaults["alice"], dedup="x:1"
        )
        assert job is not None and job.requested_by is None
    async with uow_factory(ALICE) as uow:
        assert (await uow.jobs.require(job.id)).id == job.id  # her Feed's job


async def test_api_keys_go_by_actor_for_admins_too(
    world: World, uow_factory: UnitOfWorkFactory
) -> None:
    key_id = uuid.UUID(world.key_id)
    for scope in (ALICE, ROOT):
        async with uow_factory(scope) as uow:
            assert await uow.api_keys.get(key_id) is None
            with pytest.raises(NotFound):
                await uow.api_keys.require(key_id)
            assert all(key.owner == scope.actor for key in await uow.api_keys.list())
            assert not await uow.api_keys.delete(key_id)
    async with uow_factory(Scope.SYSTEM) as uow:
        assert key_id in {key.id for key in await uow.api_keys.list()}
        by_hash = await uow.api_keys.by_hash((await uow.api_keys.require(key_id)).key_hash)
        assert by_hash is not None and by_hash.owner == "bob"
    async with uow_factory(BOB) as uow:
        assert [key.id for key in await uow.api_keys.list()] == [key_id]
        assert await uow.api_keys.delete(key_id)


async def test_crawler_stats_count_the_scopes_own_runs(
    world: World, uow_factory: UnitOfWorkFactory
) -> None:
    since = utcnow() - timedelta(days=1)
    async with uow_factory(ALICE) as uow:
        assert await uow.telemetry.crawler_stats(since) == []
    for scope in (BOB, *SEES_ALL):
        async with uow_factory(scope) as uow:
            stats = await uow.telemetry.crawler_stats(since)
            assert sum(row.failed for row in stats) == 1
            assert world.refresh_error in {row.last_error for row in stats}


async def test_a_member_scope_never_adds_for_another_owner(
    world: World, uow_factory: UnitOfWorkFactory
) -> None:
    async with uow_factory(ALICE) as uow:
        with pytest.raises(ValueError, match="not the actor"):
            await uow.feeds.add(mirror_row("https://x.example/feed.xml", owner="bob"))
