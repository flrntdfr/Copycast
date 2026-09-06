"""Archive jobs: retries with backoff, permanent failures, storage full, cancel on pause, delete."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime

import pytest
from sqlalchemy import update

from copycast.adapters.db.models import Job
from copycast.app import Container
from copycast.application.ports import PermanentError, StorageFull, TransientError
from copycast.domain.enums import (
    ArchiveState,
    BackfillMode,
    ErrorKind,
    JobKind,
    JobStatus,
)
from copycast.domain.exceptions import Conflict
from copycast.worker.runner import STORAGE_FULL_ERROR, Runner
from tests.integration.worker.conftest import Source, create_mirror, jobs_of, uow
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
