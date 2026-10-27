"""System status: every component Copycast depends on, for the header popover and agents.

Each component reports ``ok``, ``warn``, ``error`` or ``unknown`` with a one-line
summary and a few facts. The api sees Postgres, the schema, the storage and the
Engine it imports for itself; the worker's side comes from what it leaves behind:
its heartbeat (scheduler tick, its own yt-dlp and ffmpeg), the job queue and the
Refresh runs. Nothing here reaches the internet, so polling it is cheap.

Storage is the data directory today; the S3 bucket of docs/plans/object-storage.md
replaces :func:`_storage` and keeps the component's id.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from copycast.application.capabilities import capability
from copycast.application.models import (
    ComponentStatus,
    CrawlerStats,
    QueueStats,
    StatusFact,
    StatusRead,
    StatusState,
    Totals,
)
from copycast.application.ports import EngineVersion
from copycast.application.services.about import normalize_engine_version
from copycast.application.services.context import HeartbeatRow, ServiceContext
from copycast.application.services.engine import get_engine_cookies
from copycast.version import APP_VERSION

WORKER_STALE_SECONDS = 90.0
"""Three missed heartbeats (every 30 s), as ``/healthz/ready`` counts them."""
SCHEDULER_STALE_SECONDS = 180.0
"""Three missed scheduler ticks (every 60 s)."""
BACKLOG_AFTER = timedelta(minutes=15)
RECENT = timedelta(hours=24)
LOW_FREE_BYTES = 2 * 1024**3
ENGINE_STALE_DAYS = 60
"""A yt-dlp older than this is likely to fail on YouTube; automation bumps it weekly."""
CRAWLER_DOWN_RUNS = 3
"""A service whose last this-many-or-more runs all failed is reported as an error."""

_RANK: dict[StatusState, int] = {"ok": 0, "unknown": 0, "warn": 1, "error": 2}


def worst(states: Iterable[StatusState]) -> StatusState:
    """The overall state: ``error`` over ``warn`` over ``ok``; ``unknown`` counts as ``ok``."""
    result: StatusState = "ok"
    for state in states:
        if _RANK[state] > _RANK[result]:
            result = state
    return result


@dataclass(slots=True)
class _Snapshot:
    """What one database round trip returned; ``error`` set when Postgres failed."""

    error: str | None = None
    latency_ms: float | None = None
    schema_current: bool | None = None
    heartbeat: HeartbeatRow | None = None
    queue: QueueStats | None = None
    crawlers: list[CrawlerStats] = field(default_factory=list[CrawlerStats])
    totals: Totals | None = None

    @property
    def worker(self) -> dict[str, Any]:
        return dict(self.heartbeat.status) if self.heartbeat else {}


@capability("get_status", response=StatusRead)
async def get_status(ctx: ServiceContext) -> StatusRead:
    """Database, worker, scheduler, job queue, storage, yt-dlp, ffmpeg and crawlers."""
    now = datetime.now(UTC)
    snapshot = await _snapshot(ctx, now)
    engine = await asyncio.to_thread(ctx.engine.version)
    cookies = await get_engine_cookies(ctx)
    unlocked = {service.name.casefold() for service in cookies.services if service.authenticated}
    components = [
        _database(snapshot),
        _worker(snapshot, now),
        _scheduler(snapshot, now),
        _queue(snapshot, now),
        await asyncio.to_thread(_storage, ctx, snapshot),
        _engine(engine, snapshot, now),
        _ffmpeg(engine, snapshot),
        _crawlers(snapshot, unlocked),
    ]
    return StatusRead(
        state=worst(component.state for component in components),
        checked_at=now,
        components=components,
    )


async def _snapshot(ctx: ServiceContext, now: datetime) -> _Snapshot:
    snapshot = _Snapshot()
    started = time.perf_counter()
    try:
        async with ctx.uow_factory() as uow:
            snapshot.heartbeat = await uow.telemetry.latest_heartbeat()
            snapshot.latency_ms = (time.perf_counter() - started) * 1000
            snapshot.queue = await uow.jobs.queue_stats(now=now, since=now - RECENT)
            snapshot.crawlers = await uow.telemetry.crawler_stats(now - RECENT)
            snapshot.totals = await uow.feeds.totals()
        snapshot.schema_current = await ctx.schema_is_current()
    except Exception as exc:  # any driver or network failure
        snapshot.error = str(exc)[:200] or type(exc).__name__
    return snapshot


# --------------------------------------------------------------------------- components


def _database(snapshot: _Snapshot) -> ComponentStatus:
    name = "Database"
    if snapshot.error:
        return ComponentStatus(
            id="database",
            name=name,
            state="error",
            summary="Postgres is unreachable",
            facts=[StatusFact(label="Error", detail=snapshot.error)],
        )
    facts = [StatusFact(label="Answered in", value=f"{snapshot.latency_ms or 0:.0f} ms")]
    if snapshot.schema_current is False:
        return ComponentStatus(
            id="database",
            name=name,
            state="error",
            summary="The schema is not at the latest migration",
            facts=facts,
        )
    return ComponentStatus(
        id="database", name=name, state="ok", summary="Postgres is up", facts=facts
    )


def _worker(snapshot: _Snapshot, now: datetime) -> ComponentStatus:
    name = "Worker"
    if snapshot.error:
        return _unknown("worker", name, "Unknown while the database is unreachable")
    heartbeat = snapshot.heartbeat
    if heartbeat is None:
        return ComponentStatus(
            id="worker", name=name, state="error", summary="Never seen: is the worker running?"
        )
    status = snapshot.worker
    age = (now - heartbeat.seen_at).total_seconds()
    running = _int(status.get("running_jobs"))
    concurrency = _int(status.get("concurrency"))
    facts = [StatusFact(label="Last seen", at=heartbeat.seen_at)]
    if concurrency:
        facts.append(StatusFact(label="Jobs", value=f"{running} of {concurrency} slots busy"))
    if started_at := _parse(status.get("started_at")):
        facts.append(StatusFact(label="Started", at=started_at))
    version = status.get("app_version")
    if isinstance(version, str):
        facts.append(StatusFact(label="Version", value=version))
    if age > WORKER_STALE_SECONDS:
        return ComponentStatus(
            id="worker",
            name=name,
            state="error",
            summary=f"Not seen for {_duration(age)}: jobs are not running",
            facts=facts,
        )
    full_until = _parse(status.get("storage_full_until"))
    if full_until and full_until > now:
        return ComponentStatus(
            id="worker",
            name=name,
            state="error",
            summary="Storage is full: archiving is paused",
            facts=[*facts, StatusFact(label="Retries at", at=full_until)],
        )
    quarantined = status.get("quarantined_feeds")
    if isinstance(quarantined, list) and quarantined:
        count = len(quarantined)  # pyright: ignore[reportUnknownArgumentType]
        noun = "Feed" if count == 1 else "Feeds"
        return ComponentStatus(
            id="worker",
            name=name,
            state="warn",
            summary=f"{count} {noun} quarantined after an unresponsive job; restart to clear",
            facts=facts,
        )
    if isinstance(version, str) and version != APP_VERSION:
        return ComponentStatus(
            id="worker",
            name=name,
            state="warn",
            summary=f"Runs Copycast {version} while the api runs {APP_VERSION}",
            facts=facts,
        )
    summary = f"Running {running} job{'' if running == 1 else 's'}" if running else "Idle"
    return ComponentStatus(id="worker", name=name, state="ok", summary=summary, facts=facts)


def _scheduler(snapshot: _Snapshot, now: datetime) -> ComponentStatus:
    name = "Scheduler"
    if snapshot.error or snapshot.heartbeat is None:
        return _unknown("scheduler", name, "Runs inside the worker")
    if (now - snapshot.heartbeat.seen_at).total_seconds() > WORKER_STALE_SECONDS:
        return _unknown("scheduler", name, "Stopped with the worker")
    tick = _parse(snapshot.worker.get("scheduler_last_tick_at"))
    if "scheduler_last_tick_at" not in snapshot.worker:
        return _unknown("scheduler", name, "This worker does not report its scheduler")
    if tick is None:
        return ComponentStatus(
            id="scheduler",
            name=name,
            state="warn",
            summary="Has not ticked since the worker started",
        )
    facts = [StatusFact(label="Last tick", at=tick)]
    if (now - tick).total_seconds() > SCHEDULER_STALE_SECONDS:
        return ComponentStatus(
            id="scheduler",
            name=name,
            state="error",
            summary="Stalled: Refreshes are not being scheduled",
            facts=facts,
        )
    return ComponentStatus(
        id="scheduler", name=name, state="ok", summary="Schedules Refreshes", facts=facts
    )


def _queue(snapshot: _Snapshot, now: datetime) -> ComponentStatus:
    name = "Job queue"
    queue = snapshot.queue
    if snapshot.error or queue is None:
        return _unknown("queue", name, "Unknown while the database is unreachable")
    facts = [
        StatusFact(label="Queued", value=str(queue.queued)),
        StatusFact(label="Running", value=str(queue.running)),
        StatusFact(label="Waiting to retry", value=str(queue.waiting)),
        StatusFact(label="Failed in 24 h", value=str(queue.failed_recently)),
    ]
    parts = [f"{queue.queued} queued"] if queue.queued else []
    if queue.running:
        parts.append(f"{queue.running} running")
    summary = ", ".join(parts) or "Empty"
    oldest = queue.oldest_queued_at
    if oldest is not None and now - oldest > BACKLOG_AFTER:
        waited = _duration((now - oldest).total_seconds())
        return ComponentStatus(
            id="queue",
            name=name,
            state="warn",
            summary=f"Backlog: the oldest job has waited {waited}",
            facts=facts,
        )
    return ComponentStatus(id="queue", name=name, state="ok", summary=summary, facts=facts)


def _storage(ctx: ServiceContext, snapshot: _Snapshot) -> ComponentStatus:
    name = "Storage"
    data_dir = ctx.settings.data_dir
    facts = [
        StatusFact(label="Backend", value="Data directory"),
        StatusFact(label="Path", value=str(data_dir)),
    ]
    if snapshot.totals is not None:
        facts.append(StatusFact(label="Used by Feeds", value=_bytes(snapshot.totals.storage_bytes)))
    if not (data_dir.is_dir() and os.access(data_dir, os.W_OK)):
        return ComponentStatus(
            id="storage", name=name, state="error", summary="Not writable", facts=facts
        )
    version = ctx.layout.read_version()
    expected = ctx.expected_layout_version
    if version != expected:
        return ComponentStatus(
            id="storage",
            name=name,
            state="error",
            summary=f"Layout {version or 'missing'}, expected {expected}",
            facts=facts,
        )
    try:
        usage = shutil.disk_usage(data_dir)
    except OSError:
        return ComponentStatus(id="storage", name=name, state="ok", summary="Writable", facts=facts)
    facts.append(StatusFact(label="Free", value=f"{_bytes(usage.free)} of {_bytes(usage.total)}"))
    if usage.free < LOW_FREE_BYTES:
        return ComponentStatus(
            id="storage",
            name=name,
            state="warn",
            summary=f"Low on space: {_bytes(usage.free)} free",
            facts=facts,
        )
    return ComponentStatus(
        id="storage", name=name, state="ok", summary=f"{_bytes(usage.free)} free", facts=facts
    )


def _engine(engine: EngineVersion, snapshot: _Snapshot, now: datetime) -> ComponentStatus:
    name = engine.name
    version = normalize_engine_version(engine.version)
    facts = [
        StatusFact(label="Version", value=version),
        StatusFact(label="Channel", value=engine.channel),
    ]
    if engine.release_date:
        facts.append(StatusFact(label="Released", at=_midnight(engine.release_date)))
    worker_version = snapshot.worker.get("engine_version")
    if isinstance(worker_version, str) and worker_version != version:
        facts.append(StatusFact(label="Worker runs", value=worker_version))
        return ComponentStatus(
            id="engine",
            name=name,
            state="warn",
            summary=f"The worker runs {worker_version}; restart it to pick up {version}",
            facts=facts,
        )
    if engine.release_date:
        age = (now.date() - engine.release_date).days
        if age > ENGINE_STALE_DAYS:
            return ComponentStatus(
                id="engine",
                name=name,
                state="warn",
                summary=f"{version} is {age} days old; sites such as YouTube may break",
                facts=facts,
            )
    return ComponentStatus(id="engine", name=name, state="ok", summary=version, facts=facts)


def _ffmpeg(engine: EngineVersion, snapshot: _Snapshot) -> ComponentStatus:
    name = "ffmpeg"
    worker = snapshot.worker
    found = worker.get("ffmpeg_version") if "ffmpeg_version" in worker else engine.ffmpeg_version
    if not isinstance(found, str) or not found:
        return ComponentStatus(
            id="ffmpeg",
            name=name,
            state="error",
            summary="Not found: audio cannot be extracted or converted",
        )
    return ComponentStatus(id="ffmpeg", name=name, state="ok", summary=found)


def _crawlers(snapshot: _Snapshot, unlocked: set[str]) -> ComponentStatus:
    name = "Crawlers"
    if snapshot.error:
        return _unknown("crawlers", name, "Unknown while the database is unreachable")
    if not snapshot.crawlers:
        return _unknown("crawlers", name, "No Refresh in the last 24 h")
    facts: list[StatusFact] = []
    states: list[StatusState] = []
    for stats in snapshot.crawlers:
        service = stats.service or "Other"
        ok = stats.runs - stats.failed
        state: StatusState = "ok"
        if stats.failed and ok == 0 and stats.runs >= CRAWLER_DOWN_RUNS:
            state = "error"
        elif stats.failed:
            state = "warn"
        states.append(state)
        value = f"{ok} ok" + (f", {stats.failed} failed" if stats.failed else "")
        if service.casefold() in unlocked:
            value += " · cookies"
        facts.append(
            StatusFact(
                label=service,
                value=value,
                at=stats.last_success_at,
                detail=stats.last_error if stats.failed else None,
                state=state,
            )
        )
    overall = worst(states)
    runs = sum(stats.runs for stats in snapshot.crawlers)
    failed = sum(stats.failed for stats in snapshot.crawlers)
    if overall == "error":
        down = [fact.label for fact in facts if fact.state == "error"]
        summary = f"{', '.join(down)} failing on every Refresh"
    elif failed:
        summary = f"{failed} of {runs} Refreshes failed in 24 h"
    else:
        summary = f"{runs} Refreshes in 24 h, none failed"
    return ComponentStatus(id="crawlers", name=name, state=overall, summary=summary, facts=facts)


# --------------------------------------------------------------------------- helpers


def _unknown(component_id: str, name: str, summary: str) -> ComponentStatus:
    return ComponentStatus(id=component_id, name=name, state="unknown", summary=summary)


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _duration(seconds: float) -> str:
    if seconds < 120:
        return f"{seconds:.0f} s"
    if seconds < 7200:
        return f"{seconds / 60:.0f} min"
    if seconds < 172800:
        return f"{seconds / 3600:.0f} h"
    return f"{seconds / 86400:.0f} days"


def _bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1000:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} TB"


__all__ = ["get_status", "worst"]
