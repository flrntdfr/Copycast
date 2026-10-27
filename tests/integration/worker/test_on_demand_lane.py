"""The on-demand slot: an archive a podcast app waits for never queues behind a Backfill."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable

import pytest

from copycast.app import Container
from copycast.domain.enums import ArchiveState, BackfillMode, JobKind, JobStatus, JobTrigger
from copycast.worker.runner import ON_DEMAND_SLOTS, Runner
from tests.integration.worker.conftest import Source, create_mirror, jobs_of, uow
from tests.support.fake_engine import FakeEngine

pytestmark = pytest.mark.integration


async def _wait_for(predicate: Callable[[], Awaitable[bool]], *, seconds: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    until = loop.time() + seconds
    while loop.time() < until:
        if await predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met in time")


async def _item_ids(container: Container, feed_id: str) -> list[str]:
    async with uow(container) as unit:
        return [row.id for row in await unit.catalog.for_feed(feed_id)]


async def _state(container: Container, item_id: str) -> str:
    async with uow(container) as unit:
        return (await unit.catalog.require(item_id)).archive_state


async def test_an_on_demand_archive_runs_while_the_backfill_fills_every_general_slot(
    container: Container, engine: FakeEngine, source: Source
) -> None:
    runner = Runner(
        container,
        worker_id="lane",
        concurrency=1,
        listen=False,
        claim_tick=0.05,
        monitor_tick=0.05,
    )
    assert ON_DEMAND_SLOTS == 1 and runner.free_on_demand_slots == 1
    busy = await create_mirror(container, source.write_rss("busy", items=[2, 1]))
    automatic = await create_mirror(
        container, source.write_rss("waiting", items=[1]), mode=BackfillMode.automatic
    )
    await runner.run_until_idle(deadline_seconds=10)  # the two first Refreshes
    busy_items = await _item_ids(container, busy.id)
    (waited,) = await _item_ids(container, automatic.id)
    assert all([await _state(container, i) == ArchiveState.archived for i in busy_items])
    # Make the Backfill slow: purge it and archive it again, blocked mid-download.
    release = threading.Event()
    engine.block_fetches(release, item_ids=busy_items)
    for item_id in busy_items:
        async with uow(container) as unit:
            await unit.catalog.tombstone(item_id, listed=True)
        await container.services.archive_item(busy.id, item_id, trigger=JobTrigger.manual)
    loop_task = asyncio.create_task(runner.run())
    try:
        await _wait_for(lambda: _running(container, busy.id))
        assert runner.free_slots == 0, "the general slot is taken by the Backfill"
        # A podcast app asks for the Automatic feed's Episode: it gets the reserved slot.
        await container.services.archive_item(automatic.id, waited, trigger=JobTrigger.feed_fetch)
        runner.wake()
        await _wait_for(lambda: _archived(container, waited))
        assert await _running(container, busy.id), "the Backfill is still downloading"
        on_demand = await jobs_of(container, automatic.id, kind=JobKind.archive_item)
        assert [j.status for j in on_demand] == [JobStatus.succeeded.value]
        assert container.layout.find_media(automatic.id, waited) is not None
    finally:
        release.set()
        runner.request_stop()
        await asyncio.wait_for(loop_task, timeout=15)


async def _running(container: Container, feed_id: str) -> bool:
    jobs = await jobs_of(container, feed_id, kind=JobKind.archive_item)
    return any(j.status == JobStatus.running.value for j in jobs)


async def _archived(container: Container, item_id: str) -> bool:
    return await _state(container, item_id) == ArchiveState.archived


async def test_an_on_demand_archive_skips_its_feeds_running_job_but_not_its_items(
    container: Container, source: Source
) -> None:
    """Per-feed exclusivity holds for every job but an archive an app waits for; that one
    only waits for another job of the same item."""
    automatic = await create_mirror(
        container, source.write_rss("exclusive", items=[2, 1]), mode=BackfillMode.automatic
    )
    first, second = await _item_ids(container, automatic.id)
    async with uow(container) as unit:
        refresh = await unit.jobs.claim("other-worker")  # the Mirror's first Refresh, running
        assert refresh is not None and refresh.kind == JobKind.refresh.value
        await unit.jobs.enqueue(
            JobKind.archive_item, JobTrigger.policy, feed_id=automatic.id, item_id=first
        )
    async with uow(container) as unit:
        assert await unit.jobs.claim("w") is None, "a policy archive waits for the Refresh"
        await unit.jobs.enqueue(
            JobKind.archive_item, JobTrigger.feed_fetch, feed_id=automatic.id, item_id=second
        )
    async with uow(container) as unit:
        assert await unit.jobs.claim("w", on_demand_only=True) is not None
    async with uow(container) as unit:
        await unit.jobs.enqueue(
            JobKind.archive_item,
            JobTrigger.feed_fetch,
            feed_id=automatic.id,
            item_id=second,
            dedup=None,
        )
    async with uow(container) as unit:
        assert await unit.jobs.claim("w", on_demand_only=True) is None, "same item running"
        assert await unit.jobs.claim("w") is None
