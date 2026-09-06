"""Jobs: the worker queue (claim, heartbeat, finish, cancel) and its append-only log."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, delete, exists, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from copycast.adapters.db.base import refresh_loaded, rows_affected
from copycast.adapters.db.models import ACTIVE_STATUSES, Job, JobLogLine
from copycast.domain.enums import ErrorKind, JobKind, JobStatus, JobTrigger, LogLevel
from copycast.domain.exceptions import NotFound

PRIORITY_MANUAL = 50
PRIORITY_FOLLOW = 100
PRIORITY_BACKFILL = 200
MAX_ATTEMPTS = {
    JobKind.refresh: 3,
    JobKind.archive_item: 5,
    JobKind.expand_request: 3,
    JobKind.prune: 3,
    JobKind.rebuild: 1,
}
FINISHED_STATUSES: tuple[str, ...] = (
    JobStatus.succeeded.value,
    JobStatus.failed.value,
    JobStatus.cancelled.value,
)


def dedup_key(kind: JobKind, ident: str | uuid.UUID) -> str:
    """``refresh:{feed_id}``, ``archive:{item_id}``, ``expand:{request_id}``, ``prune:{feed_id}``.

    The worker's partial unique index refuses a second active job with the same key.
    """
    prefix = {
        JobKind.refresh: "refresh",
        JobKind.archive_item: "archive",
        JobKind.expand_request: "expand",
        JobKind.prune: "prune",
        JobKind.rebuild: "rebuild",
    }[kind]
    return f"{prefix}:{ident}"


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ reads

    async def get(self, job_id: uuid.UUID) -> Job | None:
        return await self._session.get(Job, job_id)

    async def require(self, job_id: uuid.UUID) -> Job:
        job = await self.get(job_id)
        if job is None:
            raise NotFound("job", str(job_id))
        return job

    async def refresh(self, job: Job) -> Job:
        await self._session.refresh(job)
        return job

    async def list_page(
        self,
        *,
        feed_id: str | None = None,
        kind: JobKind | None = None,
        status: JobStatus | Sequence[JobStatus] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Job], int]:
        base: Select[tuple[Job]] = select(Job)
        if feed_id is not None:
            base = base.where(Job.feed_id == feed_id)
        if kind is not None:
            base = base.where(Job.kind == kind.value)
        if status is not None:
            statuses = [status] if isinstance(status, JobStatus) else list(status)
            base = base.where(Job.status.in_([s.value for s in statuses]))
        total = int(
            (
                await self._session.execute(select(func.count()).select_from(base.subquery()))
            ).scalar_one()
        )
        rows = await self._session.execute(
            base.order_by(Job.created_at.desc(), Job.id).limit(limit).offset(offset)
        )
        return list(rows.scalars()), total

    async def active(self, *, feed_id: str | None = None, kind: JobKind | None = None) -> list[Job]:
        stmt = select(Job).where(Job.status.in_(ACTIVE_STATUSES)).order_by(Job.created_at, Job.id)
        if feed_id is not None:
            stmt = stmt.where(Job.feed_id == feed_id)
        if kind is not None:
            stmt = stmt.where(Job.kind == kind.value)
        return list((await self._session.execute(stmt)).scalars())

    async def active_by_dedup_key(self, key: str) -> Job | None:
        result = await self._session.execute(
            select(Job).where(Job.dedup_key == key, Job.status.in_(ACTIVE_STATUSES))
        )
        return result.scalar_one_or_none()

    async def running_count(self) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(Job).where(Job.status == JobStatus.running.value)
        )
        return int(result.scalar_one())

    async def latest_for_request(self, request_id: uuid.UUID) -> Job | None:
        result = await self._session.execute(
            select(Job)
            .where(Job.request_id == request_id, Job.kind == JobKind.expand_request.value)
            .order_by(Job.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def latest_for_item(self, item_id: str) -> Job | None:
        result = await self._session.execute(
            select(Job)
            .where(Job.item_id == item_id, Job.kind == JobKind.archive_item.value)
            .order_by(Job.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------ enqueue and claim

    async def enqueue(
        self,
        kind: JobKind,
        trigger: JobTrigger,
        *,
        feed_id: str | None = None,
        item_id: str | None = None,
        request_id: uuid.UUID | None = None,
        payload: dict[str, Any] | None = None,
        priority: int = PRIORITY_FOLLOW,
        max_attempts: int | None = None,
        run_after: datetime | None = None,
        dedup: str | None = None,
        job_id: uuid.UUID | None = None,
    ) -> Job | None:
        """Insert a queued job; ``None`` when an active job with the same ``dedup`` key exists."""
        values: dict[str, Any] = {
            "id": job_id or uuid.uuid4(),
            "kind": kind.value,
            "trigger": trigger.value,
            "feed_id": feed_id,
            "item_id": item_id,
            "request_id": request_id,
            "payload": payload or {},
            "status": JobStatus.queued.value,
            "priority": priority,
            "max_attempts": max_attempts or MAX_ATTEMPTS[kind],
            "dedup_key": dedup,
        }
        if run_after is not None:
            values["run_after"] = run_after
        stmt = (
            insert(Job)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[Job.dedup_key],
                index_where=text("status IN ('queued', 'running')"),
            )
            .returning(Job.id)
        )
        inserted = (await self._session.execute(stmt)).scalar_one_or_none()
        if inserted is None:
            return None
        return await self._session.get(Job, inserted, populate_existing=True)

    async def claim(self, worker_id: str) -> Job | None:
        """Claim the next runnable job with per-feed exclusivity (``FOR UPDATE SKIP LOCKED``)."""
        j = aliased(Job, name="j")
        r = aliased(Job, name="r")
        running_same_feed = exists(
            select(1).where(r.status == JobStatus.running.value, r.feed_id == j.feed_id)
        )
        candidate = (
            select(j.id)
            .where(
                j.status == JobStatus.queued.value,
                j.run_after <= func.now(),
                ~running_same_feed,
            )
            .order_by(j.priority, j.run_after, j.id)
            .with_for_update(skip_locked=True)
            .limit(1)
            .scalar_subquery()
        )
        stmt = (
            update(Job)
            .where(Job.id == candidate)
            .values(
                status=JobStatus.running.value,
                claimed_by=worker_id,
                claimed_at=func.now(),
                heartbeat_at=func.now(),
                started_at=func.now(),
                attempt=Job.attempt + 1,
            )
            .returning(Job.id)
        )
        job_id = (await self._session.execute(stmt)).scalar_one_or_none()
        if job_id is None:
            return None
        return await self._session.get(Job, job_id, populate_existing=True)

    async def heartbeat(
        self, job_id: uuid.UUID, *, progress: dict[str, Any] | None = None
    ) -> bool | None:
        """Touch ``heartbeat_at`` (and ``progress``); returns ``cancel_requested``, or ``None``
        when the job is no longer running (cancelled, re-queued by the scheduler...)."""
        values: dict[str, Any] = {"heartbeat_at": func.now()}
        if progress is not None:
            values["progress"] = progress
        result = await self._session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.running.value)
            .values(**values)
            .returning(Job.cancel_requested)
        )
        flag = result.scalar_one_or_none()
        return None if flag is None else bool(flag)

    async def finish(
        self,
        job_id: uuid.UUID,
        status: JobStatus,
        *,
        error: str | None = None,
        error_kind: ErrorKind | None = None,
        result: dict[str, Any] | None = None,
        engine_version: str | None = None,
        progress: dict[str, Any] | None = None,
    ) -> Job:
        if status.value not in FINISHED_STATUSES:
            raise ValueError(f"finish() takes a terminal status, not {status}")
        values: dict[str, Any] = {
            "status": status.value,
            "finished_at": func.now(),
            "error": error,
            "error_kind": error_kind.value if error_kind else None,
        }
        if result is not None:
            values["result"] = result
        if engine_version is not None:
            values["engine_version"] = engine_version
        if progress is not None:
            values["progress"] = progress
        await self._session.execute(update(Job).where(Job.id == job_id).values(**values))
        job = await self._session.get(Job, job_id, populate_existing=True)
        if job is None:
            raise NotFound("job", str(job_id))
        return job

    async def requeue(
        self,
        job_id: uuid.UUID,
        *,
        run_after: datetime | None = None,
        count_attempt: bool = True,
        error: str | None = None,
        error_kind: ErrorKind | None = None,
    ) -> Job:
        """Back to ``queued`` (transient failure, storage full, shutdown, stale heartbeat).

        The claim counted an attempt; ``count_attempt=False`` gives it back.
        """
        values: dict[str, Any] = {
            "status": JobStatus.queued.value,
            "run_after": run_after if run_after is not None else func.now(),
            "claimed_by": None,
            "claimed_at": None,
            "heartbeat_at": None,
            "started_at": None,
            "progress": None,
            "error": error,
            "error_kind": error_kind.value if error_kind else None,
        }
        if not count_attempt:
            values["attempt"] = func.greatest(Job.attempt - 1, 0)
        await self._session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.running.value)
            .values(**values)
        )
        job = await self._session.get(Job, job_id, populate_existing=True)
        if job is None:
            raise NotFound("job", str(job_id))
        return job

    async def cancel(self, job_id: uuid.UUID) -> Job:
        """Queued -> cancelled now; running -> ``cancel_requested`` for the worker to honour."""
        await self._session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.queued.value)
            .values(
                status=JobStatus.cancelled.value,
                finished_at=func.now(),
                error_kind=ErrorKind.cancelled.value,
            )
        )
        await self._session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.running.value)
            .values(cancel_requested=True)
        )
        job = await self._session.get(Job, job_id, populate_existing=True)
        if job is None:
            raise NotFound("job", str(job_id))
        return job

    async def cancel_for_feed(self, feed_id: str, *, kinds: Iterable[JobKind] | None = None) -> int:
        """Cancel every active job of a feed; returns how many rows changed."""
        kind_values = [k.value for k in kinds] if kinds is not None else None
        queued = update(Job).where(Job.feed_id == feed_id, Job.status == JobStatus.queued.value)
        running = update(Job).where(Job.feed_id == feed_id, Job.status == JobStatus.running.value)
        if kind_values is not None:
            queued = queued.where(Job.kind.in_(kind_values))
            running = running.where(Job.kind.in_(kind_values))
        first = await self._session.execute(
            queued.values(
                status=JobStatus.cancelled.value,
                finished_at=func.now(),
                error_kind=ErrorKind.cancelled.value,
            )
        )
        second = await self._session.execute(running.values(cancel_requested=True))
        await refresh_loaded(self._session, Job, lambda job: job.feed_id == feed_id)
        return rows_affected(first) + rows_affected(second)

    async def requeue_stale(self, older_than: timedelta) -> list[uuid.UUID]:
        """Running jobs whose heartbeat is older than ``older_than`` go back to queued."""
        cutoff = func.now() - older_than
        result = await self._session.execute(
            update(Job)
            .where(
                Job.status == JobStatus.running.value,
                (Job.heartbeat_at.is_(None)) | (Job.heartbeat_at < cutoff),
            )
            .values(
                status=JobStatus.queued.value,
                claimed_by=None,
                claimed_at=None,
                heartbeat_at=None,
                started_at=None,
                progress=None,
                error="worker heartbeat lost",
                error_kind=ErrorKind.transient.value,
            )
            .returning(Job.id)
        )
        ids = list(result.scalars())
        stale = set(ids)
        await refresh_loaded(self._session, Job, lambda job: job.id in stale)
        return ids

    async def purge_finished(self, before: datetime) -> int:
        result = await self._session.execute(
            delete(Job).where(Job.status.in_(FINISHED_STATUSES), Job.finished_at < before)
        )
        return rows_affected(result)

    # ------------------------------------------------------------------ log lines

    async def append_log(
        self, job_id: uuid.UUID, lines: Sequence[tuple[int, datetime, LogLevel, str]]
    ) -> int:
        if not lines:
            return 0
        rows = [
            {"job_id": job_id, "seq": seq, "at": at, "level": level.value, "message": message}
            for seq, at, level, message in lines
        ]
        result = await self._session.execute(
            insert(JobLogLine).values(rows).on_conflict_do_nothing().returning(JobLogLine.seq)
        )
        return len(list(result.scalars()))

    async def log_lines(self, job_id: uuid.UUID, *, limit: int = 5000) -> list[JobLogLine]:
        result = await self._session.execute(
            select(JobLogLine)
            .where(JobLogLine.job_id == job_id)
            .order_by(JobLogLine.seq)
            .limit(limit)
        )
        return list(result.scalars())


__all__ = [
    "FINISHED_STATUSES",
    "MAX_ATTEMPTS",
    "PRIORITY_BACKFILL",
    "PRIORITY_FOLLOW",
    "PRIORITY_MANUAL",
    "JobRepository",
    "dedup_key",
]
