"""Blocked items are re-queued when usage drops, within the room left and never in a storm.

The scheduler's sweep (``Scheduler._due_unblocks``, every tick) releases, per Owner, at
most ``RELEASE_PER_TICK`` blocked items in archive priority, and only those whose
estimate fits the room left; items blocked while the Owner was over (no estimate) go
once the Owner is no longer over. A prune job that freed bytes releases at once.
"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from copycast.adapters.db.models import CatalogItem
from copycast.app import Container
from copycast.application.models import BucketCreate, RequestCreate
from copycast.application.services.items import prune_dedup_key
from copycast.application.services.quota import RELEASE_PER_TICK
from copycast.domain.enums import ArchiveState, JobKind, JobTrigger, PruneMode, WantedReason
from copycast.worker.runner import Runner
from copycast.worker.scheduler import Scheduler
from tests.integration.worker.conftest import jobs_of, uow
from tests.integration.worker.test_quota_blocking import request_three, set_quota, use_bytes
from tests.support.factories import listing
from tests.support.fake_engine import FakeEngine

pytestmark = pytest.mark.integration

MB = 1_000_000


async def blocked_bucket(
    container: Container, count: int, *, estimate: int | None, manual: frozenset[int] = frozenset()
) -> tuple[str, dict[int, str]]:
    """A Local feed of ``count`` wanted items blocked with ``estimate``: Backfill items but
    the ordinals in ``manual``. Returns its id and its item ids by ordinal."""
    bucket = await container.services.create_bucket(BucketCreate(name="Blocked"))
    async with uow(container) as unit:
        upsert = await unit.catalog.upsert_listing(bucket.id, listing(count), partial=True)
        rows = await unit.catalog.get_many(upsert.seen_item_ids)
        await unit.session.execute(
            update(CatalogItem)
            .where(CatalogItem.feed_id == bucket.id)
            .values(
                archive_state=ArchiveState.wanted.value,
                wanted_reason=WantedReason.backfill.value,
                blocked_reason="quota",
                blocked_bytes=estimate,
            )
        )
        ids = {row.ordinal: row.id for row in rows}
        if manual:
            await unit.session.execute(
                update(CatalogItem)
                .where(CatalogItem.id.in_([ids[n] for n in manual]))
                .values(wanted_reason=WantedReason.manual.value)
            )
    return bucket.id, ids


async def archive_jobs(container: Container, feed_id: str) -> list[object]:
    return await jobs_of(container, feed_id, kind=JobKind.archive_item)


async def test_no_storm_then_a_budgeted_release_in_priority_order(container: Container) -> None:
    set_quota(container, 100 * MB)
    await use_bytes(container, 99 * MB)  # 1 MB left
    manual = frozenset({10, 20, 30})
    bucket, ids = await blocked_bucket(container, 500, estimate=50 * MB, manual=manual)
    scheduler = Scheduler(container)

    for _ in range(3):
        assert (await scheduler.tick()).unblocked == 0
    assert await archive_jobs(container, bucket) == []

    # Room for three of them: the three manual ones go first, then the walk stops.
    set_quota(container, 99 * MB + 160 * MB)
    assert (await scheduler.tick()).unblocked == 3
    jobs = await jobs_of(container, bucket, kind=JobKind.archive_item)
    assert sorted(job.item_id for job in jobs) == sorted(ids[n] for n in manual)
    assert {job.priority for job in jobs} == {50}
    async with uow(container) as unit:
        released = await unit.catalog.get_many(ids[n] for n in manual)
        assert {(row.blocked_reason, row.blocked_bytes) for row in released} == {(None, None)}

    # Unlimited: at most RELEASE_PER_TICK per tick, Backfill priority.
    set_quota(container, None)
    assert (await scheduler.tick()).unblocked == RELEASE_PER_TICK
    assert (await scheduler.tick()).unblocked == RELEASE_PER_TICK
    jobs = await jobs_of(container, bucket, kind=JobKind.archive_item)
    assert len(jobs) == 3 + 2 * RELEASE_PER_TICK
    assert sorted({job.priority for job in jobs}) == [50, 200]


async def test_items_blocked_while_over_go_once_the_owner_is_no_longer_over(
    container: Container, runner: Runner, engine: FakeEngine
) -> None:
    set_quota(container, 10 * MB)
    filler = await use_bytes(container, 10 * MB)
    bucket = await request_three(container, engine)
    await runner.run_until_idle()
    async with uow(container) as unit:
        assert {row.blocked_bytes for row in await unit.catalog.for_feed(bucket)} == {None}
    scheduler = Scheduler(container)
    assert (await scheduler.tick()).unblocked == 0

    await container.services.delete_feed(filler)  # usage drops
    assert (await scheduler.tick()).unblocked == 3
    await runner.run_until_idle()
    page = await container.services.list_items(bucket)
    assert {item.state for item in page.items} == {ArchiveState.archived}
    assert {item.blocked_reason for item in page.items} == {None}
    assert len(engine.records.fetches) == 3


async def test_a_prune_that_frees_bytes_releases_at_once(
    container: Container, runner: Runner, engine: FakeEngine
) -> None:
    later = await container.services.create_bucket(BucketCreate(name="Later"))
    url = "https://www.youtube.com/playlist?list=PLlater"
    engine.script_listing(url, listing(3, service="YouTube"))
    await container.services.add_request(later.id, RequestCreate(url=url))
    await runner.run_until_idle()
    used = (await container.services.get_feed(later.id)).storage_bytes
    assert used > 0
    set_quota(container, used)  # over
    bucket = await request_three(container, engine)
    await runner.run_until_idle()
    page = await container.services.list_items(bucket)
    assert {item.blocked_reason for item in page.items} == {"quota"}

    async with uow(container) as unit:
        job = await unit.jobs.enqueue(
            JobKind.prune,
            JobTrigger.manual,
            feed_id=later.id,
            payload={"mode": PruneMode.manual.value, "older_than_days": 0},
            dedup=prune_dedup_key(later.id),
        )
        assert job is not None
    await runner.run_until_idle()  # no scheduler tick: the prune job released them

    page = await container.services.list_items(bucket)
    assert {item.state for item in page.items} == {ArchiveState.archived}
    (prune,) = await jobs_of(container, later.id, kind=JobKind.prune)
    assert prune.result is not None and prune.result["bytes_freed"] == used


async def test_the_sweep_drops_blocks_left_on_items_no_longer_wanted(
    container: Container, runner: Runner, engine: FakeEngine
) -> None:
    set_quota(container, 1000)
    await use_bytes(container, 1000)
    bucket = await request_three(container, engine)
    await runner.run_until_idle()
    async with uow(container) as unit:
        first, *_ = await unit.catalog.for_feed(bucket)
        await unit.catalog.tombstone(first.id, listed=True)
    await Scheduler(container).tick()
    async with uow(container) as unit:
        rows = {row.id: row for row in await unit.catalog.for_feed(bucket)}
    assert rows[first.id].blocked_reason is None
    assert [r.blocked_reason for r in rows.values() if r.id != first.id] == ["quota", "quota"]
