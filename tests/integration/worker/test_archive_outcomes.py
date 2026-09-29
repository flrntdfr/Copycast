"""Archive jobs: retries with backoff, permanent failures, storage full, live streams, cancel on
pause, delete."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import update

from copycast.adapters.db.models import Job
from copycast.app import Container
from copycast.application.ports import NotReady, PermanentError, StorageFull, TransientError
from copycast.domain.enums import (
    ArchiveState,
    BackfillMode,
    ErrorKind,
    JobKind,
    JobStatus,
    LiveStatus,
)
from copycast.domain.exceptions import Conflict
from copycast.worker.constants import LIVE_RETRY, LIVE_WAIT_MAX
from copycast.worker.jobs import archive_item
from copycast.worker.runner import STORAGE_FULL_ERROR, Runner
from tests.integration.worker.conftest import Source, create_mirror, jobs_of, uow
from tests.support.factories import listing
from tests.support.fake_engine import FakeEngine, part_files

pytestmark = pytest.mark.integration


async def _one_wanted_item(
    container: Container, source: Source, runner: Runner, name: str
) -> tuple[str, str]:
    url = source.write_rss(name, items=[1], artwork=False)
    mirror = await create_mirror(container, url, mode=BackfillMode.selection)
    async with uow(container) as unit:
        item = (await unit.catalog.for_feed(mirror.id))[0]
    return mirror.id, item.id


async def test_transient_failure_backs_off_then_succeeds(
    container: Container, source: Source, runner: Runner, engine: FakeEngine
) -> None:
    feed_id, item_id = await _one_wanted_item(container, source, runner, "transient")
    job = await container.services.archive_item(feed_id, item_id)
    engine.fail_next(TransientError("connection reset"))
    await runner.run_until_idle()
    async with uow(container) as unit:
        row = await unit.jobs.require(job.id)
        assert row.status == JobStatus.queued.value
        assert row.error_kind == ErrorKind.transient.value and row.attempt == 1
        assert row.run_after > datetime.now(UTC), "backoff pushed run_after into the future"
        item = await unit.catalog.require(item_id)
        assert item.archive_state == ArchiveState.wanted and item.attempt_count == 1
        assert item.last_error == "connection reset"
        # Bring the retry forward and let it succeed.
        await unit.session.execute(
            update(Job).where(Job.id == job.id).values(run_after=datetime.now(UTC))
        )
    await runner.run_until_idle()
    async with uow(container) as unit:
        row = await unit.jobs.require(job.id)
        assert row.status == JobStatus.succeeded.value and row.attempt == 2
        item = await unit.catalog.require(item_id)
        assert item.archive_state == ArchiveState.archived
        lines = await unit.jobs.log_lines(job.id)
        assert any("fake engine failure" in line.message for line in lines)
        assert any("fetched" in line.message for line in lines)


async def test_permanent_failure_fails_item_and_job(
    container: Container, source: Source, runner: Runner, engine: FakeEngine
) -> None:
    feed_id, item_id = await _one_wanted_item(container, source, runner, "permanent")
    job = await container.services.archive_item(feed_id, item_id)
    engine.fail_next(PermanentError("video is private"))
    await runner.run_until_idle()
    async with uow(container) as unit:
        row = await unit.jobs.require(job.id)
        assert row.status == JobStatus.failed.value
        assert row.error_kind == ErrorKind.permanent.value
        item = await unit.catalog.require(item_id)
        assert item.archive_state == ArchiveState.failed and item.last_error == "video is private"
    read = await container.services.get_feed(feed_id)
    assert read.health.status.value == "warn"
    # Retry on demand: failed -> wanted again with a new job.
    retry = await container.services.archive_item(feed_id, item_id)
    assert retry.id != job.id
    await runner.run_until_idle()
    async with uow(container) as unit:
        assert (await unit.catalog.require(item_id)).archive_state == ArchiveState.archived
    with pytest.raises(Conflict):
        await container.services.archive_item(feed_id, item_id)


async def test_exhausted_attempts_fail(
    container: Container, source: Source, runner: Runner, engine: FakeEngine
) -> None:
    feed_id, item_id = await _one_wanted_item(container, source, runner, "exhausted")
    job = await container.services.archive_item(feed_id, item_id)
    async with uow(container) as unit:
        await unit.session.execute(update(Job).where(Job.id == job.id).values(max_attempts=1))
    engine.fail_next(TransientError("timeout"))
    await runner.run_until_idle()
    async with uow(container) as unit:
        row = await unit.jobs.require(job.id)
        assert row.status == JobStatus.failed.value
        assert row.error_kind == ErrorKind.transient.value
        assert (await unit.catalog.require(item_id)).archive_state == ArchiveState.failed


async def test_storage_full_requeues_without_counting_and_pauses_archive_claims(
    container: Container, source: Source, runner: Runner, engine: FakeEngine
) -> None:
    url = source.write_rss("full", items=[2, 1], artwork=False)
    mirror = await create_mirror(container, url, selection="1-2")
    engine.fail_next(StorageFull("No space left on device"))
    await runner.run_until_idle()
    archives = await jobs_of(container, mirror.id, kind=JobKind.archive_item)
    assert len(archives) == 2
    assert runner.storage_full_until is not None
    statuses = sorted((job.status, job.attempt, job.error_kind) for job in archives)
    assert statuses == [
        (JobStatus.queued.value, 0, ErrorKind.storage_full.value),
        (JobStatus.queued.value, 0, ErrorKind.transient.value),
    ], "one hit ENOSPC, the other was deferred by the pause; neither counted an attempt"
    deferred = next(job for job in archives if job.error_kind == ErrorKind.transient.value)
    assert deferred.error == STORAGE_FULL_ERROR
    assert all(job.run_after >= runner.storage_full_until for job in archives)
    async with uow(container) as unit:
        states = {i.archive_state for i in await unit.catalog.for_feed(mirror.id)}
        assert states == {ArchiveState.wanted}
    # Once storage is back the deferred claims run.
    runner.storage_full_until = None
    async with uow(container) as unit:
        await unit.session.execute(
            update(Job).where(Job.feed_id == mirror.id).values(run_after=datetime.now(UTC))
        )
    await runner.run_until_idle()
    async with uow(container) as unit:
        states = {i.archive_state for i in await unit.catalog.for_feed(mirror.id)}
        assert states == {ArchiveState.archived}


async def _run_now(container: Container, job_id: object) -> None:
    async with uow(container) as unit:
        await unit.session.execute(
            update(Job).where(Job.id == job_id).values(run_after=datetime.now(UTC))
        )


async def test_a_stream_without_a_recording_waits_without_counting_then_gives_up(
    container: Container, runner: Runner, engine: FakeEngine
) -> None:
    """NotReady re-queues in LIVE_RETRY (or the Engine's estimate) attempt-free, stores the
    live status on the item, and fails for good once the job waited LIVE_WAIT_MAX."""
    url = "https://www.youtube.com/@streams/streams"
    engine.script_listing(url, listing(1, service="YouTube", raw={"_type": "playlist"}))
    mirror = await create_mirror(container, url, mode=BackfillMode.selection)
    async with uow(container) as unit:
        item_id = (await unit.catalog.for_feed(mirror.id))[0].id
    job = await container.services.archive_item(mirror.id, item_id)
    engine.fail_next(NotReady("still live", live_status=LiveStatus.is_live))
    before = datetime.now(UTC)
    await runner.run_until_idle()
    async with uow(container) as unit:
        row = await unit.jobs.require(job.id)
        assert row.status == JobStatus.queued.value
        assert (row.attempt, row.error_kind, row.error) == (
            0,
            ErrorKind.transient.value,
            "still live",
        )
        assert before + LIVE_RETRY <= row.run_after <= datetime.now(UTC) + LIVE_RETRY
        item = await unit.catalog.require(item_id)
        assert (item.archive_state, item.attempt_count) == (ArchiveState.wanted, 0)
        assert item.last_error == "still live" and item.live_status == LiveStatus.is_live
        lines = await unit.jobs.log_lines(job.id)
        assert any("still live" in line.message for line in lines)
    assert runner.stats.requeued == 1 and runner.stats.failed == 0

    # An upcoming stream with a known start waits for it (never less than LIVE_RETRY).
    await _run_now(container, job.id)
    engine.fail_next(
        NotReady(
            "not started yet", live_status=LiveStatus.is_upcoming, retry_after=timedelta(hours=3)
        )
    )
    before = datetime.now(UTC)
    await runner.run_until_idle()
    async with uow(container) as unit:
        row = await unit.jobs.require(job.id)
        assert row.status == JobStatus.queued.value and row.attempt == 0
        assert (
            before + timedelta(hours=3) <= row.run_after <= datetime.now(UTC) + timedelta(hours=3)
        )
        assert (await unit.catalog.require(item_id)).live_status == LiveStatus.is_upcoming
    await _run_now(container, job.id)
    engine.fail_next(
        NotReady(
            "not started yet", live_status=LiveStatus.is_upcoming, retry_after=timedelta(minutes=2)
        )
    )
    before = datetime.now(UTC)
    await runner.run_until_idle()
    async with uow(container) as unit:
        row = await unit.jobs.require(job.id)
        assert before + LIVE_RETRY <= row.run_after, (
            "an estimate shorter than LIVE_RETRY is not honoured"
        )

    # The recording is published: the same job archives it, still on its first attempt.
    await _run_now(container, job.id)
    await runner.run_until_idle()
    async with uow(container) as unit:
        row = await unit.jobs.require(job.id)
        assert row.status == JobStatus.succeeded.value and row.attempt == 1
        item = await unit.catalog.require(item_id)
        assert item.archive_state == ArchiveState.archived and item.attempt_count == 0
        assert item.live_status == LiveStatus.is_upcoming, "a listing, not an archive, updates it"

    # Past the ceiling the job and the item fail for good; the status still says why.
    await container.services.delete_item(mirror.id, item_id)
    again = await container.services.archive_item(mirror.id, item_id)
    assert again.id != job.id
    async with uow(container) as unit:
        await unit.session.execute(
            update(Job)
            .where(Job.id == again.id)
            .values(created_at=datetime.now(UTC) - LIVE_WAIT_MAX - timedelta(minutes=1))
        )
    engine.fail_next(NotReady("recording being processed", live_status=LiveStatus.post_live))
    await runner.run_until_idle()
    async with uow(container) as unit:
        row = await unit.jobs.require(again.id)
        assert row.status == JobStatus.failed.value and row.error_kind == ErrorKind.permanent.value
        assert row.error == "recording being processed; still not published after 48 h"
        item = await unit.catalog.require(item_id)
        assert item.archive_state == ArchiveState.failed and item.attempt_count == 1
        assert item.last_error == row.error and item.live_status == LiveStatus.post_live
    assert runner.stats.failed == 1


async def _one_stream_job(
    container: Container, engine: FakeEngine, name: str
) -> tuple[str, str, Any]:
    url = f"https://www.youtube.com/@{name}/streams"
    engine.script_listing(url, listing(1, service="YouTube", raw={"_type": "playlist"}))
    mirror = await create_mirror(container, url, mode=BackfillMode.selection)
    async with uow(container) as unit:
        item_id = (await unit.catalog.for_feed(mirror.id))[0].id
    job = await container.services.archive_item(mirror.id, item_id)
    return mirror.id, item_id, job


async def _age_job(container: Container, job_id: object, age: timedelta) -> datetime:
    created_at = datetime.now(UTC) - age
    async with uow(container) as unit:
        await unit.session.execute(
            update(Job).where(Job.id == job_id).values(created_at=created_at)
        )
    return created_at


async def test_the_runner_follows_the_archive_jobs_verdict_on_the_ceiling(
    container: Container, runner: Runner, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ceiling is measured once, by the archive job: when it lets a NotReady through
    (the item stays wanted), the runner re-queues even though its own clock says the 48 h
    are over, so the Catalog and the job never disagree (review finding). Nothing is left
    of the budget, so the next try is immediate; there the job decides again, and with the
    recording published it archives the item."""
    _feed_id, item_id, job = await _one_stream_job(container, engine, "verdict")
    await _age_job(container, job.id, LIVE_WAIT_MAX + timedelta(hours=1))
    monkeypatch.setattr(archive_item, "live_wait_exceeded", lambda *a, **k: False)
    engine.fail_next(NotReady("still live", live_status=LiveStatus.is_live))
    await runner.run_until_idle()
    assert (runner.stats.requeued, runner.stats.failed) == (1, 0), (
        "the runner followed the job's NotReady instead of failing on its own clock"
    )
    assert engine.records.fetched_item_ids == [item_id, item_id], "the last try ran at once"
    async with uow(container) as unit:
        row = await unit.jobs.require(job.id)
        item = await unit.catalog.require(item_id)
        assert (row.status, row.attempt) == (JobStatus.succeeded.value, 1)
        assert (item.archive_state, item.attempt_count) == (ArchiveState.archived, 0)
        assert item.live_status == LiveStatus.is_live, "a listing, not an archive, updates it"
        lines = await unit.jobs.log_lines(job.id)
        assert any("still live" in line.message for line in lines)


async def test_a_streams_wait_is_never_scheduled_past_the_jobs_ceiling(
    container: Container, runner: Runner, engine: FakeEngine
) -> None:
    """A start estimate beyond the budget left is cut to it: the job gets its last try at
    the ceiling instead of being parked past it and failing without one (review finding)."""
    _feed_id, item_id, job = await _one_stream_job(container, engine, "ceiling")
    created_at = await _age_job(container, job.id, timedelta(hours=10))
    engine.fail_next(
        NotReady(
            "not started yet", live_status=LiveStatus.is_upcoming, retry_after=timedelta(hours=40)
        )
    )
    await runner.run_until_idle()
    async with uow(container) as unit:
        row = await unit.jobs.require(job.id)
        assert row.status == JobStatus.queued.value and row.attempt == 0
        ceiling = created_at + LIVE_WAIT_MAX
        assert ceiling - timedelta(seconds=5) <= row.run_after <= ceiling
        assert (await unit.catalog.require(item_id)).archive_state == ArchiveState.wanted


async def test_pause_cancels_a_running_download_and_keeps_the_part_file(
    container: Container, source: Source, runner: Runner, engine: FakeEngine
) -> None:
    feed_id, item_id = await _one_wanted_item(container, source, runner, "pause")
    job = await container.services.archive_item(feed_id, item_id)
    gate = threading.Event()
    engine.block_fetches(gate)
    claimed = await runner.claim_available()
    assert [j.id for j in claimed] == [job.id]
    tmp = container.layout.tmp_dir(feed_id)
    for _ in range(100):
        if part_files(tmp):
            break
        await asyncio.sleep(0.05)
    assert part_files(tmp), "the download wrote its .part file"

    paused = await container.services.set_paused(feed_id, True)
    assert paused.paused
    await runner.run_until_idle()

    async with uow(container) as unit:
        row = await unit.jobs.require(job.id)
        assert row.status == JobStatus.cancelled.value
        assert row.error_kind == ErrorKind.cancelled.value
        item = await unit.catalog.require(item_id)
        assert item.archive_state == ArchiveState.wanted and item.attempt_count == 0
        feed = await unit.feeds.require(feed_id)
        assert feed.paused and feed.revision >= 1
    assert part_files(tmp), ".part stays in tmp/ for a later resume"
    assert not list(container.layout.media_dir(feed_id).glob("*.m4a"))
    gate.set()


async def test_delete_item_during_archive_leaves_no_media(
    container: Container, source: Source, runner: Runner, engine: FakeEngine
) -> None:
    feed_id, item_id = await _one_wanted_item(container, source, runner, "delete")
    job = await container.services.archive_item(feed_id, item_id)
    gate = threading.Event()
    engine.block_fetches(gate)
    await runner.claim_available()
    await container.services.delete_item(feed_id, item_id)
    await runner.run_until_idle()
    async with uow(container) as unit:
        row = await unit.jobs.require(job.id)
        assert row.status == JobStatus.cancelled.value
        item = await unit.catalog.require(item_id)
        assert item.archive_state == ArchiveState.deleted and item.listed is True
    assert not part_files(container.layout.tmp_dir(feed_id))
    page = await container.services.list_items(feed_id)
    assert page.items[0].state is ArchiveState.deleted and page.items[0].media is None
    gate.set()
