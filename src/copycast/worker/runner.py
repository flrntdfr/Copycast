"""The claim loop: ``FOR UPDATE SKIP LOCKED`` claims, a thread per job, heartbeats, retries.

One :class:`Runner` per process. Every second (or on ``NOTIFY copycast_jobs``)
it claims runnable jobs while it has free slots and runs each in its own task:
the job's blocking parts execute on a ``ThreadPoolExecutor`` sized like the
pool of slots; a monitor task per job heartbeats, flushes progress (at most
twice a second) and log lines, polls ``cancel_requested`` every two seconds and
enforces the soft timeouts. Outcomes: transient errors back off
(``60s * 2^attempt`` capped at 6 h, +-20 %), permanent ones fail, ``StorageFull``
re-queues in ten minutes without counting the attempt and pauses archive
claims, a cancel finishes the job ``cancelled``. SIGTERM stops claiming, sets
every token, drains for up to ``DRAIN_SECONDS`` and re-queues what is still
running (``.part`` files stay in ``tmp/``).
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import psycopg
from sqlalchemy import func, select

from copycast.adapters.db.models import Job, JobLogLine
from copycast.adapters.db.uow import UnitOfWork, uow_of
from copycast.app import Container
from copycast.application.events import JOBS_CHANNEL, JobEvent, ProgressEvent
from copycast.application.models import JobRead
from copycast.application.ports import (
    Cancelled,
    CancelToken,
    PermanentError,
    StorageFull,
)
from copycast.application.services.about import normalize_engine_version
from copycast.domain.enums import ArchiveState, ErrorKind, JobKind, JobStatus
from copycast.logging import bind_context, get_logger, unbind_context
from copycast.worker.constants import (
    BACKOFF_BASE_SECONDS,
    BACKOFF_JITTER,
    BACKOFF_MAX_SECONDS,
    CANCEL_POLL_SECONDS,
    CLAIM_TICK_SECONDS,
    DRAIN_SECONDS,
    EXIT_OK,
    EXIT_STUCK,
    LOG_FLUSH_SECONDS,
    MONITOR_TICK_SECONDS,
    QUARANTINE_RETRY,
    SOFT_TIMEOUTS,
    STORAGE_FULL_RETRY,
    UNRESPONSIVE_GRACE_SECONDS,
    default_worker_id,
)
from copycast.worker.health import WorkerState
from copycast.worker.joblog import JobLog
from copycast.worker.jobs import JobContext, JobOutcome, handler_for
from copycast.worker.progress import ProgressTracker

log = get_logger(__name__)

SHUTDOWN_ERROR = "worker shutdown"
QUARANTINE_ERROR = "feed quarantined after an unresponsive job"
STORAGE_FULL_ERROR = "storage full; archive claims paused"


def backoff_seconds(attempt: int, *, rng: random.Random | None = None) -> float:
    """``min(60s * 2^attempt, 6h)`` with +-20 % jitter."""
    base = min(BACKOFF_BASE_SECONDS * (2 ** max(attempt, 0)), BACKOFF_MAX_SECONDS)
    jitter = (rng or random).uniform(-BACKOFF_JITTER, BACKOFF_JITTER)
    return base * (1.0 + jitter)


def psycopg_conninfo(database_url: str) -> str:
    """``postgresql+psycopg://`` (SQLAlchemy) -> ``postgresql://`` (psycopg)."""
    parts = urlsplit(database_url)
    scheme = "postgresql" if parts.scheme.startswith("postgres") else parts.scheme
    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, ""))


@dataclass(slots=True)
class RunningJob:
    job: Job
    cancel: CancelToken
    log: JobLog
    progress: ProgressTracker
    started: float
    task: asyncio.Task[None] | None = None
    timed_out_at: float | None = None
    stuck: bool = False
    user_cancelled: bool = False


@dataclass(slots=True)
class RunnerStats:
    claimed: int = 0
    succeeded: int = 0
    failed: int = 0
    requeued: int = 0
    cancelled: int = 0
    stuck: int = 0
    events: list[str] = field(default_factory=list[str])


class Runner:
    def __init__(
        self,
        container: Container,
        *,
        worker_id: str | None = None,
        concurrency: int | None = None,
        state: WorkerState | None = None,
        listen: bool = True,
        claim_tick: float = CLAIM_TICK_SECONDS,
        monitor_tick: float = MONITOR_TICK_SECONDS,
        soft_timeouts: Mapping[JobKind, float] = SOFT_TIMEOUTS,
        unresponsive_grace: float = UNRESPONSIVE_GRACE_SECONDS,
        drain_seconds: float = DRAIN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._container = container
        self.worker_id = worker_id or default_worker_id()
        self.concurrency = concurrency or container.settings.refresh.concurrency
        self.state = state
        self._listen_enabled = listen
        self._claim_tick = claim_tick
        self._monitor_tick = monitor_tick
        self._soft_timeouts = dict(soft_timeouts)
        self._unresponsive_grace = unresponsive_grace
        self._drain_seconds = drain_seconds
        self._clock = clock
        self._executor = ThreadPoolExecutor(
            max_workers=self.concurrency, thread_name_prefix="copycast-job"
        )
        self._running: dict[uuid.UUID, RunningJob] = {}
        self._stuck = 0
        self.quarantined: set[str] = set()
        self.storage_full_until: datetime | None = None
        self.stop_event = asyncio.Event()
        self._wake = asyncio.Event()
        self.exit_code = EXIT_OK
        self.stats = RunnerStats()

    # ------------------------------------------------------------------ lifecycle

    @property
    def free_slots(self) -> int:
        return max(0, self.concurrency - len(self._running) - self._stuck)

    @property
    def running_jobs(self) -> list[Job]:
        return [running.job for running in self._running.values()]

    def request_stop(self) -> None:
        """SIGTERM: stop claiming, cancel every token, drain."""
        if not self.stop_event.is_set():
            log.info("worker.stopping", running=len(self._running))
        self.stop_event.set()
        for running in self._running.values():
            running.cancel.cancel()
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    async def run(self) -> int:
        """Claim and run jobs until :meth:`request_stop`; returns the process exit code."""
        listener = asyncio.create_task(self._listen()) if self._listen_enabled else None
        try:
            while not self.stop_event.is_set():
                await self.claim_available()
                if self._stuck and self._stuck >= self.concurrency:
                    log.error("worker.all_threads_stuck", stuck=self._stuck)
                    self.exit_code = EXIT_STUCK
                    self.request_stop()
                    break
                self._wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=self._claim_tick)
        finally:
            if listener is not None:
                listener.cancel()
                await asyncio.gather(listener, return_exceptions=True)
            await self.drain()
            self._executor.shutdown(wait=False, cancel_futures=True)
        return self.exit_code

    async def run_until_idle(self, *, deadline_seconds: float = 30.0) -> None:
        """Claim and finish everything runnable now (tests); stops when nothing is left."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + deadline_seconds
        while True:
            claimed = await self.claim_available()
            if not claimed and not self._running:
                return
            if loop.time() > deadline:
                raise TimeoutError("runner did not become idle in time")
            tasks = [r.task for r in self._running.values() if r.task is not None]
            if tasks:
                await asyncio.wait(tasks, timeout=max(0.05, deadline - loop.time()))
            else:
                await asyncio.sleep(0.05)

    # ------------------------------------------------------------------ claiming

    async def claim_available(self) -> list[Job]:
        claimed: list[Job] = []
        while self.free_slots > 0 and not self.stop_event.is_set():
            job = await self._claim_one()
            if job is None:
                break
            if await self._defer_if_needed(job):
                continue
            claimed.append(job)
            self._start(job, log_start=await self._last_log_seq(job))
        return claimed

    async def _last_log_seq(self, job: Job) -> int:
        """A retry keeps appending to the job's log after the previous attempt's lines."""
        if job.attempt <= 1:
            return 0
        async with self._uow() as uow:
            result = await uow.session.execute(
                select(func.coalesce(func.max(JobLogLine.seq), 0)).where(
                    JobLogLine.job_id == job.id
                )
            )
            return int(result.scalar_one())

    async def _claim_one(self) -> Job | None:
        async with self._uow() as uow:
            job = await uow.jobs.claim(self.worker_id)
        if job is not None:
            self.stats.claimed += 1
        return job

    async def _defer_if_needed(self, job: Job) -> bool:
        """Quarantined feed or paused archive claims: give the job back for later."""
        now = datetime.now(UTC)
        if job.feed_id is not None and job.feed_id in self.quarantined:
            await self._requeue(job.id, run_after=now + QUARANTINE_RETRY, error=QUARANTINE_ERROR)
            return True
        if (
            job.kind == JobKind.archive_item
            and self.storage_full_until is not None
            and now < self.storage_full_until
        ):
            await self._requeue(job.id, run_after=self.storage_full_until, error=STORAGE_FULL_ERROR)
            return True
        return False

    def _start(self, job: Job, *, log_start: int = 0) -> None:
        running = RunningJob(
            job=job,
            cancel=CancelToken(),
            log=JobLog(job.id, start=log_start),
            progress=ProgressTracker(item_id=job.item_id),
            started=self._clock(),
        )
        self._running[job.id] = running
        running.task = asyncio.create_task(self._execute(running), name=f"job-{job.id}")
        self._sync_state()
        log.info("job.started", job_id=str(job.id), kind=job.kind, feed_id=job.feed_id)

    # ------------------------------------------------------------------ execution

    async def _execute(self, running: RunningJob) -> None:
        job = running.job
        bind_context(job_id=str(job.id), feed_id=job.feed_id)
        monitor = asyncio.create_task(self._monitor(running), name=f"monitor-{job.id}")
        outcome: JobOutcome | None = None
        failure: BaseException | None = None
        try:
            ctx = JobContext(
                container=self._container,
                job=job,
                cancel=running.cancel,
                log=running.log,
                progress=running.progress,
                executor=self._executor,
                stopping=self.stop_event.is_set,
            )
            outcome = await handler_for(JobKind(job.kind))(ctx)
        except asyncio.CancelledError:
            # Abandoned by the stuck handling or the drain; bookkeeping happened there.
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            unbind_context("job_id", "feed_id")
            raise
        except Exception as exc:
            failure = exc
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        if running.stuck:
            unbind_context("job_id", "feed_id")
            return
        try:
            await self._settle(running, outcome, failure)
        except Exception:
            log.exception("job.settle_failed", job_id=str(job.id))
        finally:
            self._running.pop(job.id, None)
            self._sync_state()
            self._wake.set()
            unbind_context("job_id", "feed_id")

    async def _settle(
        self, running: RunningJob, outcome: JobOutcome | None, failure: BaseException | None
    ) -> None:
        job = running.job
        progress = running.progress.take_final()
        progress_json = progress.model_dump(mode="json") if progress is not None else None
        async with self._uow() as uow:
            await uow.jobs.append_log(job.id, running.log.drain())
            if failure is None:
                result = outcome.result if outcome is not None else {}
                engine_version = (
                    normalize_engine_version(outcome.engine_version)
                    if outcome is not None and outcome.engine_version
                    else None
                )
                final = await uow.jobs.finish(
                    job.id,
                    JobStatus.succeeded,
                    result=result,
                    engine_version=engine_version,
                    progress=progress_json,
                )
                self.stats.succeeded += 1
                log.info("job.succeeded", job_id=str(job.id), kind=job.kind)
            elif isinstance(failure, Cancelled):
                if self.stop_event.is_set() and not running.user_cancelled:
                    final = await uow.jobs.requeue(
                        job.id,
                        count_attempt=False,
                        error=SHUTDOWN_ERROR,
                        error_kind=ErrorKind.transient,
                    )
                    self.stats.requeued += 1
                else:
                    final = await uow.jobs.finish(
                        job.id,
                        JobStatus.cancelled,
                        error=str(failure),
                        error_kind=ErrorKind.cancelled,
                    )
                    self.stats.cancelled += 1
                log.info("job.cancelled", job_id=str(job.id), kind=job.kind, error=str(failure))
            elif isinstance(failure, StorageFull):
                until = datetime.now(UTC) + STORAGE_FULL_RETRY
                final = await uow.jobs.requeue(
                    job.id,
                    run_after=until,
                    count_attempt=False,
                    error=str(failure),
                    error_kind=ErrorKind.storage_full,
                )
                if job.kind == JobKind.archive_item:
                    self.storage_full_until = until
                self.stats.requeued += 1
                log.error("job.storage_full", job_id=str(job.id), until=until.isoformat())
            elif isinstance(failure, PermanentError):
                final = await uow.jobs.finish(
                    job.id, JobStatus.failed, error=str(failure), error_kind=ErrorKind.permanent
                )
                self.stats.failed += 1
                log.warning("job.failed", job_id=str(job.id), kind=job.kind, error=str(failure))
            else:
                error = (
                    f"{type(failure).__name__}: {failure}"
                    if not str(failure).strip()
                    else str(failure)
                )
                if job.attempt < job.max_attempts:
                    delay = backoff_seconds(job.attempt)
                    final = await uow.jobs.requeue(
                        job.id,
                        run_after=datetime.now(UTC) + timedelta(seconds=delay),
                        error=error,
                        error_kind=ErrorKind.transient,
                    )
                    self.stats.requeued += 1
                    log.warning(
                        "job.retry",
                        job_id=str(job.id),
                        kind=job.kind,
                        attempt=job.attempt,
                        delay_seconds=round(delay),
                        error=error,
                    )
                else:
                    final = await uow.jobs.finish(
                        job.id, JobStatus.failed, error=error, error_kind=ErrorKind.transient
                    )
                    self.stats.failed += 1
                    log.error("job.exhausted", job_id=str(job.id), kind=job.kind, error=error)
            await uow.publish(JobEvent(job=JobRead.model_validate(final)))
            self._sync_state()

    # ------------------------------------------------------------------ monitor

    async def _monitor(self, running: RunningJob) -> None:
        job = running.job
        next_poll = self._clock()
        next_log = self._clock() + LOG_FLUSH_SECONDS
        limit = self._soft_timeouts.get(JobKind(job.kind), SOFT_TIMEOUTS[JobKind.archive_item])
        while True:
            await asyncio.sleep(self._monitor_tick)
            now = self._clock()
            snapshot = running.progress.take_due()
            poll_due = now >= next_poll
            log_due = now >= next_log
            if snapshot is not None or poll_due or log_due:
                try:
                    async with self._uow() as uow:
                        if log_due:
                            await uow.jobs.append_log(job.id, running.log.drain())
                        if snapshot is not None or poll_due:
                            flag = await uow.jobs.heartbeat(
                                job.id,
                                progress=snapshot.model_dump(mode="json") if snapshot else None,
                            )
                            if flag is None:
                                # No longer ours (cancelled while queued, re-queued as stale).
                                running.cancel.cancel()
                            elif flag:
                                running.user_cancelled = True
                                running.cancel.cancel()
                        if snapshot is not None:
                            await uow.publish(
                                ProgressEvent(
                                    job_id=job.id,
                                    feed_id=job.feed_id,
                                    item_id=job.item_id,
                                    progress=snapshot,
                                )
                            )
                except Exception:
                    log.exception("job.monitor_failed", job_id=str(job.id))
                if poll_due:
                    next_poll = now + CANCEL_POLL_SECONDS
                if log_due:
                    next_log = now + LOG_FLUSH_SECONDS
            elapsed = now - running.started
            if running.timed_out_at is None and elapsed > limit:
                running.timed_out_at = now
                running.cancel.cancel()
                log.warning(
                    "job.soft_timeout", job_id=str(job.id), kind=job.kind, seconds=round(elapsed)
                )
            elif (
                running.timed_out_at is not None
                and now - running.timed_out_at > self._unresponsive_grace
            ):
                await self._mark_stuck(running)
                return

    async def _mark_stuck(self, running: RunningJob) -> None:
        """The thread ignored its token: fail the job, quarantine the feed, abandon the thread."""
        job = running.job
        running.stuck = True
        self._stuck += 1
        self.stats.stuck += 1
        if job.feed_id is not None:
            self.quarantined.add(job.feed_id)
        self._running.pop(job.id, None)
        # Bookkeeping first, shielded: cancelling the job task also cancels this monitor.
        await asyncio.shield(self._fail_stuck(running))
        if running.task is not None:
            running.task.cancel()
        self._sync_state()
        self._wake.set()
        log.error("job.stuck", job_id=str(job.id), kind=job.kind, feed_id=job.feed_id)

    async def _fail_stuck(self, running: RunningJob) -> None:
        job = running.job
        error = "unresponsive after its soft timeout; feed quarantined"
        async with self._uow() as uow:
            await uow.jobs.append_log(job.id, running.log.drain())
            final = await uow.jobs.finish(
                job.id, JobStatus.failed, error=error, error_kind=ErrorKind.transient
            )
            if job.kind == JobKind.archive_item and job.item_id is not None:
                await uow.catalog.mark_state(
                    job.item_id,
                    ArchiveState.failed,
                    only_from=ArchiveState.archiving,
                    error=error,
                    count_attempt=True,
                )
            await uow.publish(JobEvent(job=JobRead.model_validate(final)))

    # ------------------------------------------------------------------ shutdown

    async def drain(self) -> None:
        """Wait for running jobs (tokens already set), then re-queue whatever is left."""
        for running in self._running.values():
            running.cancel.cancel()
        tasks = [r.task for r in self._running.values() if r.task is not None]
        if tasks:
            await asyncio.wait(tasks, timeout=self._drain_seconds)
        leftovers = list(self._running.values())
        for running in leftovers:
            if running.task is not None:
                running.task.cancel()
        if leftovers:
            await asyncio.gather(
                *(r.task for r in leftovers if r.task is not None), return_exceptions=True
            )
            for running in leftovers:
                await self._requeue(running.job.id, error=SHUTDOWN_ERROR)
                self._running.pop(running.job.id, None)
        self._sync_state()
        await self._container.uow_factory.flush_exports()

    async def _requeue(
        self, job_id: uuid.UUID, *, run_after: datetime | None = None, error: str | None = None
    ) -> None:
        async with self._uow() as uow:
            job = await uow.jobs.requeue(
                job_id,
                run_after=run_after,
                count_attempt=False,
                error=error,
                error_kind=ErrorKind.transient,
            )
            await uow.publish(JobEvent(job=JobRead.model_validate(job)))
        self.stats.requeued += 1

    # ------------------------------------------------------------------ helpers

    def _uow(self) -> UnitOfWork:
        return uow_of(self._container.uow_factory())

    def _sync_state(self) -> None:
        if self.state is None:
            return
        self.state.running_jobs = len(self._running)
        self.state.quarantined_feeds = sorted(self.quarantined)
        self.state.storage_full_until = self.storage_full_until

    async def _listen(self) -> None:
        """Wake the claim loop on ``NOTIFY copycast_jobs``; reconnects with a short backoff."""
        conninfo = psycopg_conninfo(self._container.settings.database_url)
        delay = 1.0
        while not self.stop_event.is_set():
            try:
                async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
                    await conn.execute(f"LISTEN {JOBS_CHANNEL}")
                    delay = 1.0
                    while not self.stop_event.is_set():
                        async for _ in conn.notifies(timeout=1.0):
                            self._wake.set()
            except asyncio.CancelledError:
                raise
            except (psycopg.Error, OSError) as exc:
                log.warning("worker.listen_failed", error=str(exc), retry_seconds=delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)


__all__ = [
    "QUARANTINE_ERROR",
    "SHUTDOWN_ERROR",
    "STORAGE_FULL_ERROR",
    "Runner",
    "RunnerStats",
    "RunningJob",
    "backoff_seconds",
    "psycopg_conninfo",
]
