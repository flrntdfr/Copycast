"""JobRepository: enqueue with dedup, claim with per-feed exclusivity, heartbeat, finish, cancel."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from copycast.adapters.db.repositories import (
    PRIORITY_BACKFILL,
    PRIORITY_FOLLOW,
    PRIORITY_MANUAL,
    JobRepository,
    dedup_key,
)
from copycast.adapters.db.repositories.jobs import MAX_ATTEMPTS
from copycast.adapters.db.uow import UnitOfWorkFactory
from copycast.domain.enums import ErrorKind, JobKind, JobStatus, JobTrigger, LogLevel, RequestedVia
from copycast.domain.exceptions import NotFound
from tests.integration.storage.conftest import NOW, item_row, mirror_row, seed

pytestmark = pytest.mark.integration


def test_dedup_keys() -> None:
    assert dedup_key(JobKind.refresh, "feed1") == "refresh:feed1"
    assert dedup_key(JobKind.archive_item, "item1") == "archive:item1"
    rid = uuid.uuid4()
    assert dedup_key(JobKind.expand_request, rid) == f"expand:{rid}"
    assert dedup_key(JobKind.prune, "feed1") == "prune:feed1"
    assert dedup_key(JobKind.rebuild, "all") == "rebuild:all"


async def test_enqueue_dedups_active_jobs_only(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        key = dedup_key(JobKind.refresh, feed.id)
        job = await uow.jobs.enqueue(
            JobKind.refresh, JobTrigger.manual, feed_id=feed.id, dedup=key, priority=PRIORITY_MANUAL
        )
        assert job is not None
        assert (job.status, job.attempt, job.max_attempts) == (
            "queued",
            0,
            MAX_ATTEMPTS[JobKind.refresh],
        )
        assert job.payload == {} and job.priority == PRIORITY_MANUAL
        duplicate = await uow.jobs.enqueue(
            JobKind.refresh, JobTrigger.scheduled, feed_id=feed.id, dedup=key
        )
        assert duplicate is None
        active = await uow.jobs.active_by_dedup_key(key)
        assert active is not None and active.id == job.id
        await uow.jobs.finish(job.id, JobStatus.succeeded, result={"ok": True})
        again = await uow.jobs.enqueue(
            JobKind.refresh, JobTrigger.manual, feed_id=feed.id, dedup=key
        )
        assert again is not None and again.id != job.id
        assert await uow.jobs.active_by_dedup_key(key) is not None
        assert len(await uow.jobs.active(feed_id=feed.id)) == 1
        assert len(await uow.jobs.active(kind=JobKind.prune)) == 0


async def test_claim_orders_by_priority_and_honours_run_after(
    uow_factory: UnitOfWorkFactory,
) -> None:
    async with uow_factory() as uow:
        a = await uow.feeds.add(mirror_row("https://a.example/feed"))
        b = await uow.feeds.add(mirror_row("https://b.example/feed"))
        c = await uow.feeds.add(mirror_row("https://c.example/feed"))
        low = await uow.jobs.enqueue(
            JobKind.refresh, JobTrigger.policy, feed_id=a.id, priority=PRIORITY_BACKFILL
        )
        high = await uow.jobs.enqueue(
            JobKind.refresh, JobTrigger.manual, feed_id=b.id, priority=PRIORITY_MANUAL
        )
        later = await uow.jobs.enqueue(
            JobKind.refresh,
            JobTrigger.scheduled,
            feed_id=c.id,
            priority=1,
            run_after=datetime.now(UTC) + timedelta(hours=1),
        )
        assert low and high and later
    async with uow_factory() as uow:
        first = await uow.jobs.claim("w1")
        assert first is not None and first.id == high.id
        assert (first.status, first.claimed_by, first.attempt) == ("running", "w1", 1)
        assert first.started_at is not None and first.heartbeat_at is not None
        second = await uow.jobs.claim("w1")
        assert second is not None and second.id == low.id
        assert await uow.jobs.claim("w1") is None  # `later` is not due yet
        assert await uow.jobs.running_count() == 2


async def test_claim_is_exclusive_per_feed(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        a = await uow.feeds.add(mirror_row("https://a.example/feed"))
        b = await uow.feeds.add(mirror_row("https://b.example/feed"))
        items = [item_row(a, 1), item_row(a, 2), item_row(b, 1)]
        await seed(uow, *items)
        for n, item in enumerate(items):
            await uow.jobs.enqueue(
                JobKind.archive_item,
                JobTrigger.policy,
                feed_id=item.feed_id,
                item_id=item.id,
                priority=PRIORITY_FOLLOW + n,
                dedup=dedup_key(JobKind.archive_item, item.id),
            )
    async with uow_factory() as uow:
        first = await uow.jobs.claim("w1")
        assert first is not None and first.item_id == items[0].id
        second = await uow.jobs.claim("w1")
        assert second is not None and second.feed_id == b.id  # feed a already has a running job
        assert await uow.jobs.claim("w1") is None
        await uow.jobs.finish(first.id, JobStatus.succeeded)
        third = await uow.jobs.claim("w1")
        assert third is not None and third.item_id == items[1].id


async def test_concurrent_claims_are_disjoint(
    uow_factory: UnitOfWorkFactory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    async with uow_factory() as uow:
        for n in range(4):
            feed = await uow.feeds.add(mirror_row(f"https://{n}.example/feed"))
            await uow.jobs.enqueue(JobKind.refresh, JobTrigger.scheduled, feed_id=feed.id)
    s1, s2 = sessionmaker(), sessionmaker()
    try:
        async with s1.begin(), s2.begin():
            j1 = await JobRepository(s1).claim("w1")
            j2 = await JobRepository(s2).claim("w2")
            assert j1 is not None and j2 is not None
            assert j1.id != j2.id  # SKIP LOCKED: the second worker never waits on the first
    finally:
        await s1.close()
        await s2.close()
    async with uow_factory() as uow:
        assert await uow.jobs.running_count() == 2


async def test_heartbeat_finish_and_cancel(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        queued = await uow.jobs.enqueue(JobKind.refresh, JobTrigger.manual, feed_id=feed.id)
        assert queued is not None
        assert await uow.jobs.heartbeat(queued.id) is None  # not running yet
        running = await uow.jobs.claim("w1")
        assert running is not None and running.id == queued.id
        assert await uow.jobs.heartbeat(running.id, progress={"phase": "listing"}) is False
        cancelled = await uow.jobs.cancel(running.id)
        assert (cancelled.status, cancelled.cancel_requested) == ("running", True)
        assert await uow.jobs.heartbeat(running.id) is True
        assert (await uow.jobs.refresh(running)).progress == {"phase": "listing"}
        with pytest.raises(ValueError, match="terminal"):
            await uow.jobs.finish(running.id, JobStatus.running)
        finished = await uow.jobs.finish(
            running.id,
            JobStatus.cancelled,
            error="cancelled by user",
            error_kind=ErrorKind.cancelled,
            engine_version="2026.08.19",
            progress=None,
        )
        assert finished.status == "cancelled" and finished.finished_at is not None
        assert (finished.error_kind, finished.engine_version) == ("cancelled", "2026.08.19")
        with pytest.raises(NotFound):
            await uow.jobs.finish(uuid.uuid4(), JobStatus.failed)
        with pytest.raises(NotFound):
            await uow.jobs.cancel(uuid.uuid4())
        with pytest.raises(NotFound):
            await uow.jobs.require(uuid.uuid4())

        waiting = await uow.jobs.enqueue(JobKind.refresh, JobTrigger.manual, feed_id=feed.id)
        assert waiting is not None
        cancelled = await uow.jobs.cancel(waiting.id)
        assert (cancelled.status, cancelled.error_kind) == ("cancelled", "cancelled")
        assert cancelled.finished_at is not None


async def test_cancel_for_feed_and_requeue(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        other = await uow.feeds.add(mirror_row("https://other.example/feed"))
        item = item_row(feed, 1)
        await seed(uow, item)
        refresh = await uow.jobs.enqueue(
            JobKind.refresh, JobTrigger.manual, feed_id=feed.id, priority=PRIORITY_MANUAL
        )
        archive = await uow.jobs.enqueue(
            JobKind.archive_item, JobTrigger.policy, feed_id=feed.id, item_id=item.id
        )
        untouched = await uow.jobs.enqueue(JobKind.refresh, JobTrigger.manual, feed_id=other.id)
        assert refresh and archive and untouched
        running = await uow.jobs.claim("w1")
        assert running is not None and running.id == refresh.id
        changed = await uow.jobs.cancel_for_feed(feed.id)
        assert changed == 2
        assert (running.cancel_requested, archive.status) == (True, "cancelled")
        assert (await uow.jobs.require(untouched.id)).status == "queued"

        requeued = await uow.jobs.requeue(
            running.id,
            run_after=NOW + timedelta(minutes=10),
            count_attempt=False,
            error="disk full",
            error_kind=ErrorKind.storage_full,
        )
        assert (requeued.status, requeued.attempt, requeued.claimed_by) == ("queued", 0, None)
        assert requeued.run_after == NOW + timedelta(minutes=10)
        assert (requeued.error, requeued.error_kind) == ("disk full", "storage_full")
        assert requeued.started_at is None and requeued.progress is None
        with pytest.raises(NotFound):
            await uow.jobs.requeue(uuid.uuid4())
        assert await uow.jobs.cancel_for_feed(feed.id, kinds=[JobKind.prune]) == 0


async def test_requeue_stale_and_purge(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        job = await uow.jobs.enqueue(JobKind.refresh, JobTrigger.manual, feed_id=feed.id)
        assert job is not None
        claimed = await uow.jobs.claim("w1")
        assert claimed is not None
        assert await uow.jobs.requeue_stale(timedelta(minutes=5)) == []
        assert await uow.jobs.requeue_stale(timedelta(seconds=-1)) == [job.id]
        assert (claimed.status, claimed.error) == ("queued", "worker heartbeat lost")
        done = await uow.jobs.enqueue(JobKind.prune, JobTrigger.scheduled, feed_id=feed.id)
        assert done is not None
        await uow.jobs.finish(done.id, JobStatus.failed, error="x", error_kind=ErrorKind.permanent)
        assert await uow.jobs.purge_finished(NOW - timedelta(days=30)) == 0
        assert await uow.jobs.purge_finished(datetime.now(UTC) + timedelta(seconds=1)) == 1
        assert await uow.jobs.get(done.id) is None


async def test_list_page_and_lookups(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        item = item_row(feed, 1)
        await seed(uow, item)
        request = await uow.requests.add(feed.id, "https://x.example/v", RequestedVia.ui)
        expand = await uow.jobs.enqueue(
            JobKind.expand_request, JobTrigger.request, feed_id=feed.id, request_id=request.id
        )
        archive = await uow.jobs.enqueue(
            JobKind.archive_item, JobTrigger.request, feed_id=feed.id, item_id=item.id
        )
        refresh = await uow.jobs.enqueue(JobKind.refresh, JobTrigger.manual, feed_id=feed.id)
        assert expand and archive and refresh
        rows, total = await uow.jobs.list_page(feed_id=feed.id)
        assert total == 3 and len(rows) == 3
        rows, total = await uow.jobs.list_page(kind=JobKind.refresh)
        assert (total, rows[0].id) == (1, refresh.id)
        rows, total = await uow.jobs.list_page(status=[JobStatus.queued], limit=2, offset=2)
        assert (total, len(rows)) == (3, 1)
        rows, total = await uow.jobs.list_page(status=JobStatus.failed)
        assert (total, rows) == (0, [])
        latest = await uow.jobs.latest_for_request(request.id)
        assert latest is not None and latest.id == expand.id
        latest = await uow.jobs.latest_for_item(item.id)
        assert latest is not None and latest.id == archive.id
        assert await uow.jobs.latest_for_item("0000000000000000") is None


async def test_log_lines_append_only(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        job = await uow.jobs.enqueue(JobKind.refresh, JobTrigger.manual, feed_id=feed.id)
        assert job is not None
        assert await uow.jobs.append_log(job.id, []) == 0
        lines = [(1, NOW, LogLevel.info, "hello"), (2, NOW, LogLevel.warning, "careful")]
        assert await uow.jobs.append_log(job.id, lines) == 2
        assert await uow.jobs.append_log(job.id, lines) == 0  # duplicates are ignored
        stored = await uow.jobs.log_lines(job.id)
        assert [(line.seq, line.level, line.message) for line in stored] == [
            (1, "info", "hello"),
            (2, "warning", "careful"),
        ]
        assert len(await uow.jobs.log_lines(job.id, limit=1)) == 1
