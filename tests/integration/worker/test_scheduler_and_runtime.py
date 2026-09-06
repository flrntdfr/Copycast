"""Scheduler ticks, soft timeouts and quarantine, drain on shutdown, descriptor debounce, health."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import update

from copycast.adapters.db.locks import WORKER_LOCK, SessionLock
from copycast.adapters.db.models import Feed, Job
from copycast.adapters.storage.descriptor import read_intent_version
from copycast.app import Container
from copycast.application.models import InboxCreate, RebuildRequest
from copycast.domain.enums import (
    ArchiveState,
    BackfillMode,
    ErrorKind,
    JobKind,
    JobStatus,
    JobTrigger,
)
from copycast.settings import Settings
from copycast.worker import main as worker_main
from copycast.worker.constants import EXIT_LOCKED, EXIT_STUCK
from copycast.worker.health import HealthServer, WorkerState, create_health_app, readiness
from copycast.worker.runner import SHUTDOWN_ERROR, Runner
from copycast.worker.scheduler import Scheduler
from tests.integration.worker.conftest import Source, create_mirror, jobs_of, uow
from tests.support.fake_engine import FakeEngine, part_files

pytestmark = pytest.mark.integration


async def test_scheduler_tick(container: Container, source: Source, runner: Runner) -> None:
    url = source.write_rss("sched", items=[1], artwork=False)
    mirror = await create_mirror(container, url)
    paused_url = source.write_rss("paused", items=[1], artwork=False)
    paused = await create_mirror(container, paused_url)
    await runner.run_until_idle()
    await container.services.set_paused(paused.id, True)
    inbox = await container.services.create_inbox(InboxCreate(name="Auto", autoprune_days=1))
    async with uow(container) as unit:
        long_ago = datetime.now(UTC) - timedelta(days=2)
        await unit.session.execute(
            update(Feed)
            .where(Feed.id.in_([mirror.id, paused.id]))
            .values(last_refresh_attempt_at=long_ago)
        )
        # A job whose heartbeat went stale (a crashed worker) and an old finished one.
        stale = await unit.jobs.enqueue(
            JobKind.refresh, JobTrigger.manual, feed_id=mirror.id, dedup=f"refresh:{mirror.id}"
        )
        assert stale is not None
        await unit.session.execute(
            update(Job)
            .where(Job.id == stale.id)
            .values(status=JobStatus.running.value, heartbeat_at=long_ago, claimed_by="dead")
        )
        old = await unit.jobs.enqueue(
            JobKind.prune, JobTrigger.manual, feed_id=inbox.id, dedup="prune:old"
        )
        assert old is not None
        await unit.session.execute(
            update(Job)
            .where(Job.id == old.id)
            .values(
                status=JobStatus.succeeded.value, finished_at=datetime.now(UTC) - timedelta(days=40)
            )
        )

    scheduler = Scheduler(container)
    tick = await scheduler.tick()
    assert tick.requeued == 1 and tick.purged_jobs == 1
    assert tick.refreshes == 0, "the stale Refresh of the same Mirror was re-queued first"
    assert tick.prunes == 1
    assert scheduler.last_tick_at is not None
    async with uow(container) as unit:
        requeued = await unit.jobs.require(stale.id)
        assert (
            requeued.status == JobStatus.queued.value and requeued.error == "worker heartbeat lost"
        )
        assert await unit.jobs.get(old.id) is None
        prunes, _ = await unit.jobs.list_page(feed_id=inbox.id, kind=JobKind.prune)
        assert prunes and prunes[0].payload == {"mode": "auto"}
        # The Paused Mirror got no Refresh.
        paused_jobs, _ = await unit.jobs.list_page(feed_id=paused.id, kind=JobKind.refresh)
        assert all(j.status != JobStatus.queued.value for j in paused_jobs)

    second = await scheduler.tick()
    assert second.requeued == 0 and second.prunes == 0
    await runner.run_until_idle()
    async with uow(container) as unit:
        done = await unit.jobs.require(prunes[0].id)
        assert done.status == JobStatus.succeeded.value and done.result == {
            "mode": "auto",
            "matched": 0,
            "deleted_count": 0,
            "bytes_freed": 0,
        }
        feed = await unit.feeds.require(inbox.id)
        assert feed.last_autoprune_at is not None


async def test_soft_timeout_quarantines_the_feed_and_exits_3_when_all_stuck(
    container: Container, source: Source, engine: FakeEngine
) -> None:
    runner = Runner(
        container,
        worker_id="stuck-worker",
        concurrency=1,
        listen=False,
        claim_tick=0.05,
        monitor_tick=0.05,
        soft_timeouts={JobKind.archive_item: 0.2},
        unresponsive_grace=0.2,
        drain_seconds=0.5,
    )
    url = source.write_rss("stuck", items=[2, 1], artwork=False)
    mirror = await create_mirror(container, url, selection="1-2")
    gate = threading.Event()
    engine.block_fetches(gate)
    # Make the fake engine ignore the token: it polls `cancel`, so give it one that never sets.
    original_fetch = engine.fetch_item

    def ignoring_token(spec, options, cancel, on_progress, log):  # type: ignore[no-untyped-def]
        from copycast.application.ports import CancelToken

        return original_fetch(spec, options, CancelToken(), on_progress, log)

    engine.fetch_item = ignoring_token  # type: ignore[method-assign]
    exit_code = await asyncio.wait_for(runner.run(), timeout=15)
    gate.set()
    assert exit_code == EXIT_STUCK
    assert mirror.id in runner.quarantined
    archives = await jobs_of(container, mirror.id, kind=JobKind.archive_item)
    failed = [j for j in archives if j.status == JobStatus.failed.value]
    assert len(failed) == 1 and "unresponsive" in (failed[0].error or "")
    assert failed[0].error_kind == ErrorKind.transient.value
    async with uow(container) as unit:
        item = await unit.catalog.require(failed[0].item_id)
        assert item.archive_state == ArchiveState.failed


async def test_shutdown_drains_and_requeues_running_jobs(
    container: Container, source: Source, engine: FakeEngine
) -> None:
    runner = Runner(
        container,
        worker_id="drain",
        concurrency=1,
        listen=False,
        claim_tick=0.05,
        monitor_tick=0.05,
        drain_seconds=2.0,
    )
    url = source.write_rss("drain", items=[1], artwork=False)
    mirror = await create_mirror(container, url, selection="1")
    gate = threading.Event()
    engine.block_fetches(gate)
    run_task = asyncio.create_task(runner.run())
    tmp = container.layout.tmp_dir(mirror.id)
    for _ in range(200):
        if part_files(tmp):
            break
        await asyncio.sleep(0.05)
    assert part_files(tmp)
    runner.request_stop()
    assert await asyncio.wait_for(run_task, timeout=10) == 0
    archives = await jobs_of(container, mirror.id, kind=JobKind.archive_item)
    assert len(archives) == 1
    job = archives[0]
    assert job.status == JobStatus.queued.value and job.attempt == 0
    assert job.error == SHUTDOWN_ERROR
    async with uow(container) as unit:
        item = await unit.catalog.require(job.item_id)
        assert item.archive_state == ArchiveState.wanted
    assert part_files(tmp), "the .part stays for the resume"
    gate.set()


async def test_descriptor_export_is_debounced_for_archive_jobs(
    container: Container, source: Source, runner: Runner
) -> None:
    url = source.write_rss("debounce", items=[1], artwork=False)
    mirror = await create_mirror(container, url, selection="1")
    path = container.layout.descriptor_path(mirror.id)
    before = read_intent_version(path)
    assert before is not None
    await runner.run_until_idle()
    async with uow(container) as unit:
        feed = await unit.feeds.require(mirror.id)
        assert feed.intent_version > before
    assert read_intent_version(path) == before, "archive jobs export through the 5 s debounce"
    await container.uow_factory.flush_exports()
    assert read_intent_version(path) == feed.intent_version


async def test_rebuild_job_runs_under_the_worker_lock(
    container: Container, source: Source, runner: Runner
) -> None:
    url = source.write_rss("rebuild", items=[1], artwork=False)
    mirror = await create_mirror(container, url, mode=BackfillMode.all)
    await runner.run_until_idle()
    await container.uow_factory.flush_exports()
    job = await container.services.rebuild(RebuildRequest(dry_run=True))
    async with SessionLock(container.db_engine, WORKER_LOCK) as lock:
        assert lock.held
        await runner.run_until_idle()
    async with uow(container) as unit:
        done = await unit.jobs.require(job.id)
        assert done.status == JobStatus.succeeded.value, done.error
        assert done.result and done.result["feeds_on_disk"] >= 1
        assert mirror.id in {f.id for f in await unit.feeds.list()}


async def test_health_endpoints(container: Container, unused_tcp_port: int) -> None:
    state = WorkerState(ffmpeg_version="fake 0.0")
    report = await readiness(container, state)
    assert report.status == "degraded"
    assert report.checks["db"].ok and report.checks["schema"].ok and report.checks["layout"].ok
    assert not report.checks["worker_seen_at"].ok and not report.checks["scheduler"].ok
    state.last_heartbeat_at = datetime.now(UTC)
    state.scheduler_last_tick_at = datetime.now(UTC)
    server = HealthServer(
        create_health_app(container, state), host="127.0.0.1", port=unused_tcp_port
    )
    serving = asyncio.create_task(server.serve())
    assert await server.wait_started()
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{unused_tcp_port}") as client:
            live = await client.get("/healthz/live")
            assert live.status_code == 200 and live.json() == {"status": "ok"}
            ready = await client.get("/healthz/ready")
            assert ready.status_code == 200
            body = ready.json()
            assert body["status"] == "ok" and body["checks"]["ffmpeg"]["detail"] == "fake 0.0"
            state.last_heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
            degraded = await client.get("/healthz/ready")
            assert degraded.status_code == 503 and degraded.json()["status"] == "degraded"
    finally:
        server.stop()
        await asyncio.wait_for(serving, timeout=10)


async def test_second_worker_exits_locked(
    container: Container, settings: Settings, engine: FakeEngine
) -> None:
    async with SessionLock(container.db_engine, WORKER_LOCK) as lock:
        assert lock.held
        assert await worker_main.main(settings, engine=engine) == EXIT_LOCKED


async def test_main_starts_serves_and_stops_on_sigterm(
    settings: Settings,
    engine: FakeEngine,
    db: str,
    unused_tcp_port: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from copycast.settings import get_settings

    own = get_settings(
        base_url=settings.base_url,
        data_dir=settings.data_dir,
        database_url=db,
        refresh={"interval_hours": 24, "fetch_cooldown_minutes": 15, "concurrency": 1},
        COPYCAST_WORKER_PORT=unused_tcp_port,
        COPYCAST_BIND="127.0.0.1",
    )
    task = asyncio.create_task(worker_main.main(own, engine=engine))
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{unused_tcp_port}") as client:
        for _ in range(200):
            try:
                ready = await client.get("/healthz/ready")
            except httpx.HTTPError:
                await asyncio.sleep(0.05)
                continue
            if ready.status_code == 200:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("worker never became ready")
        assert ready.json()["checks"]["worker_seen_at"]["ok"]
    os.kill(os.getpid(), signal.SIGTERM)
    assert await asyncio.wait_for(task, timeout=15) == 0


@pytest.mark.skipif(sys.platform != "linux", reason="subprocess SIGTERM drain test runs on Linux")
async def test_worker_subprocess_exits_zero_on_sigterm(
    settings: Settings, db: str, unused_tcp_port: int
) -> None:
    env = dict(os.environ)
    env.update(
        {
            "COPYCAST__DATABASE_URL": db,
            "COPYCAST__DATA_DIR": str(settings.data_dir),
            "COPYCAST__BASE_URL": settings.base_url,
            "COPYCAST_WORKER_PORT": str(unused_tcp_port),
            "COPYCAST_BIND": "127.0.0.1",
        }
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "copycast.cli",
        "worker",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{unused_tcp_port}") as client:
        for _ in range(600):
            try:
                if (await client.get("/healthz/live")).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
        else:
            process.kill()
            pytest.fail("worker subprocess never came up")
    process.send_signal(signal.SIGTERM)
    output, _ = await asyncio.wait_for(process.communicate(), timeout=60)
    assert process.returncode == 0, output.decode(errors="replace")[-2000:]
