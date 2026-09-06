"""The 60 s scheduler: due Refreshes, due autoprunes, stale jobs, retention purges."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from copycast.adapters.db.uow import uow_of
from copycast.app import Container
from copycast.application.events import JobEvent
from copycast.application.models import JobRead
from copycast.application.services.items import PRIORITY_FOLLOW, prune_dedup_key
from copycast.domain.enums import JobKind, JobTrigger, PruneMode
from copycast.logging import get_logger
from copycast.worker.constants import (
    AUTOPRUNE_INTERVAL,
    JOB_RETENTION,
    REFRESH_RUN_RETENTION,
    SCHEDULER_TICK_SECONDS,
    STALE_HEARTBEAT,
)

log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SchedulerTick:
    refreshes: int = 0
    prunes: int = 0
    requeued: int = 0
    purged_jobs: int = 0
    purged_runs: int = 0


class Scheduler:
    def __init__(
        self,
        container: Container,
        *,
        interval: float = SCHEDULER_TICK_SECONDS,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._container = container
        self._interval = interval
        self._clock = clock
        self.last_tick_at: datetime | None = None
        self.on_tick: Callable[[datetime], None] | None = None

    async def tick(self) -> SchedulerTick:
        now = self._clock()
        requeued, purged_jobs, purged_runs = await self._maintenance(now)
        refreshes = await self._due_refreshes(now)
        prunes = await self._due_autoprunes(now)
        self.last_tick_at = now
        if self.on_tick is not None:
            self.on_tick(now)
        result = SchedulerTick(refreshes, prunes, requeued, purged_jobs, purged_runs)
        if any((refreshes, prunes, requeued, purged_jobs, purged_runs)):
            log.info("scheduler.tick", **_as_dict(result))
        return result

    async def run(self, stop: asyncio.Event) -> None:
        """Tick immediately, then every ``interval`` seconds until ``stop`` is set."""
        while not stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("scheduler.tick_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval)
            except TimeoutError:
                continue

    # ------------------------------------------------------------------ steps

    async def _due_refreshes(self, now: datetime) -> int:
        interval = timedelta(hours=self._container.settings.refresh.interval_hours)
        async with uow_of(self._container.uow_factory()) as uow:
            due = [feed.id for feed in await uow.feeds.mirrors_due(now - interval)]
        count = 0
        for feed_id in due:
            job = await self._container.services.request_refresh(feed_id, JobTrigger.scheduled)
            if job is not None:
                count += 1
        return count

    async def _due_autoprunes(self, now: datetime) -> int:
        count = 0
        async with uow_of(self._container.uow_factory()) as uow:
            for feed in await uow.feeds.inboxes_due_autoprune(now - AUTOPRUNE_INTERVAL):
                job = await uow.jobs.enqueue(
                    JobKind.prune,
                    JobTrigger.scheduled,
                    feed_id=feed.id,
                    payload={"mode": PruneMode.auto.value},
                    priority=PRIORITY_FOLLOW,
                    dedup=prune_dedup_key(feed.id),
                )
                if job is not None:
                    await uow.publish(JobEvent(job=JobRead.model_validate(job)))
                    count += 1
            if count:
                await uow.notify_jobs()
        return count

    async def _maintenance(self, now: datetime) -> tuple[int, int, int]:
        async with uow_of(self._container.uow_factory()) as uow:
            stale = await uow.jobs.requeue_stale(STALE_HEARTBEAT)
            for job_id in stale:
                job = await uow.jobs.get(job_id)
                if job is not None:
                    await uow.publish(JobEvent(job=JobRead.model_validate(job)))
            if stale:
                await uow.notify_jobs()
                log.warning("scheduler.requeued_stale", count=len(stale))
            purged_jobs = await uow.jobs.purge_finished(now - JOB_RETENTION)
            purged_runs = await uow.telemetry.purge_refresh_runs(now - REFRESH_RUN_RETENTION)
        return len(stale), purged_jobs, purged_runs


def _as_dict(tick: SchedulerTick) -> dict[str, int]:
    return {
        "refreshes": tick.refreshes,
        "prunes": tick.prunes,
        "requeued": tick.requeued,
        "purged_jobs": tick.purged_jobs,
        "purged_runs": tick.purged_runs,
    }


__all__ = ["Scheduler", "SchedulerTick"]
