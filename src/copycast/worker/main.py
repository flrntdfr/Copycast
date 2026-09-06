"""``copycast worker``: startup sequence, WORKER_LOCK, heartbeat, health server, shutdown.

Startup: layout version, schema (``COPYCAST_AUTO_MIGRATE``), the process-wide
advisory lock (a second worker exits with a message), the default Inbox, the
engine version, then the claim loop, the scheduler, the 30 s heartbeat and the
health endpoints on ``COPYCAST_WORKER_PORT``. SIGTERM/SIGINT drain the runner
and flush the debounced descriptor exports before the process exits.
"""

from __future__ import annotations

import asyncio
import shutil
import signal
import sys
from datetime import UTC, datetime

from copycast.adapters.db.locks import WORKER_LOCK, SessionLock
from copycast.adapters.db.migrate import SchemaNotCurrent, prepare_schema
from copycast.adapters.db.uow import uow_of
from copycast.adapters.sources.http import close_client
from copycast.adapters.storage.layout import Layout
from copycast.app import Container, build_container
from copycast.application.ports import Engine
from copycast.application.services.about import normalize_engine_version
from copycast.logging import get_logger
from copycast.settings import Settings, SettingsError
from copycast.version import APP_VERSION
from copycast.worker.constants import (
    EXIT_CONFIG,
    EXIT_LOCKED,
    HEARTBEAT_SECONDS,
    default_worker_id,
)
from copycast.worker.health import HealthServer, WorkerState, create_health_app
from copycast.worker.runner import Runner
from copycast.worker.scheduler import Scheduler

log = get_logger(__name__)


def run(settings: Settings) -> int:
    """Entry point of ``copycast worker``; returns the process exit code."""
    return asyncio.run(main(settings))


async def main(settings: Settings, *, engine: Engine | None = None) -> int:
    try:
        Layout(settings.data_dir).ensure_version()
    except SettingsError as exc:
        _fatal(str(exc))
        return EXIT_CONFIG
    container = build_container(settings, engine=engine)
    try:
        try:
            await prepare_schema(container.db_engine, auto_migrate=settings.auto_migrate)
        except SchemaNotCurrent as exc:
            _fatal(str(exc))
            return EXIT_CONFIG
        async with SessionLock(container.db_engine, WORKER_LOCK) as lock:
            if not lock.held:
                _fatal("another worker holds WORKER_LOCK on this database; only one runs at a time")
                return EXIT_LOCKED
            return await _serve(container, settings)
    finally:
        close_client()
        await container.aclose()


async def _serve(container: Container, settings: Settings) -> int:
    worker_id = default_worker_id()
    await container.services.ensure_default_inbox()
    version = await asyncio.to_thread(container.engine.version)
    async with uow_of(container.uow_factory()) as uow:
        await uow.telemetry.record_engine_version(
            normalize_engine_version(version.version),
            channel=version.channel,
            release_date=version.release_date,
            git_head=version.git_head,
            app_version=APP_VERSION,
        )
    state = WorkerState(ffmpeg_version=version.ffmpeg_version)
    runner = Runner(container, worker_id=worker_id, state=state)
    scheduler = Scheduler(container)
    scheduler.on_tick = lambda at: setattr(state, "scheduler_last_tick_at", at)
    health = HealthServer(
        create_health_app(container, state), host=settings.bind, port=settings.worker_port
    )
    _install_signal_handlers(runner)
    log.info(
        "worker.started",
        worker_id=worker_id,
        engine=f"{version.name} {version.version}",
        ffmpeg=version.ffmpeg_version,
        concurrency=runner.concurrency,
        health_port=settings.worker_port,
    )
    background = [
        asyncio.create_task(scheduler.run(runner.stop_event), name="scheduler"),
        asyncio.create_task(_heartbeat_loop(container, runner, state), name="heartbeat"),
        asyncio.create_task(health.serve(), name="health"),
    ]
    try:
        exit_code = await runner.run()
    finally:
        health.stop()
        for task in background:
            if task.get_name() != "health":
                task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
    log.info("worker.stopped", exit_code=exit_code, **_stats(runner))
    return exit_code


async def _heartbeat_loop(container: Container, runner: Runner, state: WorkerState) -> None:
    while not runner.stop_event.is_set():
        try:
            await _heartbeat(container, runner, state)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("worker.heartbeat_failed")
        try:
            await asyncio.wait_for(runner.stop_event.wait(), timeout=HEARTBEAT_SECONDS)
        except TimeoutError:
            continue


async def _heartbeat(container: Container, runner: Runner, state: WorkerState) -> None:
    try:
        free_bytes: int | None = shutil.disk_usage(container.settings.data_dir).free
    except OSError:
        free_bytes = None
    status = {
        "running_jobs": len(runner.running_jobs),
        "quarantined_feeds": sorted(runner.quarantined),
        "storage_full_until": (
            runner.storage_full_until.isoformat() if runner.storage_full_until else None
        ),
        "free_bytes": free_bytes,
        "concurrency": runner.concurrency,
        "app_version": APP_VERSION,
    }
    async with uow_of(container.uow_factory()) as uow:
        await uow.telemetry.heartbeat(runner.worker_id, status)
    state.last_heartbeat_at = datetime.now(UTC)


def _install_signal_handlers(runner: Runner) -> None:
    loop = asyncio.get_running_loop()

    def _from_signal(_signum: int, _frame: object) -> None:
        loop.call_soon_threadsafe(runner.request_stop)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, runner.request_stop)
        except (NotImplementedError, RuntimeError):  # not the main thread, or Windows
            signal.signal(sig, _from_signal)


def _stats(runner: Runner) -> dict[str, int]:
    stats = runner.stats
    return {
        "claimed": stats.claimed,
        "succeeded": stats.succeeded,
        "failed": stats.failed,
        "requeued": stats.requeued,
        "cancelled": stats.cancelled,
        "stuck": stats.stuck,
    }


def _fatal(message: str) -> None:
    log.error("worker.fatal", error=message)
    print(f"copycast worker: {message}", file=sys.stderr)  # noqa: T201 - the one actionable line


__all__ = ["main", "run"]
