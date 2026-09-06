"""Jobs: observing and cancelling the worker's queue; the rebuild job."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from copycast.application.capabilities import capability
from copycast.application.events import JobEvent
from copycast.application.models import JobPage, JobRead, RebuildRequest
from copycast.application.services.context import ServiceContext
from copycast.application.services.items import PRIORITY_MANUAL
from copycast.application.services.readmodels import job_read
from copycast.domain.enums import JobKind, JobStatus, JobTrigger
from copycast.domain.exceptions import Conflict

REBUILD_DEDUP_KEY = "rebuild:global"
MAX_PAGE = 500


@capability("list_jobs", response=JobPage)
async def list_jobs(
    ctx: ServiceContext,
    *,
    feed_id: str | None = None,
    kind: JobKind | None = None,
    status: JobStatus | Sequence[JobStatus] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> JobPage:
    limit = max(1, min(limit, MAX_PAGE))
    offset = max(0, offset)
    async with ctx.uow_factory() as uow:
        rows, total = await uow.jobs.list_page(
            feed_id=feed_id, kind=kind, status=status, limit=limit, offset=offset
        )
        jobs = [job_read(row) for row in rows]
        return JobPage(jobs=jobs, total=total, limit=limit, offset=offset)


@capability("get_job", response=JobRead)
async def get_job(ctx: ServiceContext, job_id: uuid.UUID) -> JobRead:
    async with ctx.uow_factory() as uow:
        return job_read(await uow.jobs.require(job_id))


@capability("cancel_job", response=JobRead)
async def cancel_job(ctx: ServiceContext, job_id: uuid.UUID) -> JobRead:
    """Queued -> cancelled now; running -> the worker is asked to stop (``cancel_requested``)."""
    async with ctx.uow_factory() as uow:
        before = await uow.jobs.require(job_id)
        job = await uow.jobs.cancel(job_id)
        if before.status != job.status:
            await uow.publish(JobEvent(job=job_read(job)))
        return job_read(job)


@capability("rebuild", request=RebuildRequest, response=JobRead)
async def rebuild(ctx: ServiceContext, body: RebuildRequest) -> JobRead:
    """Queue a rebuild of the database from the data directory (runs on the worker).

    ``dry_run`` keeps database rows that have no trace on disk (they are only
    counted); 409 while a rebuild is already queued or running.
    """
    async with ctx.uow_factory() as uow:
        job = await uow.jobs.enqueue(
            JobKind.rebuild,
            JobTrigger.manual,
            payload={"dry_run": body.dry_run},
            priority=PRIORITY_MANUAL,
            dedup=REBUILD_DEDUP_KEY,
        )
        if job is None:
            raise Conflict("a rebuild is already queued or running")
        await uow.publish(JobEvent(job=job_read(job)))
        await uow.notify_jobs()
        return job_read(job)


__all__ = ["MAX_PAGE", "REBUILD_DEDUP_KEY", "cancel_job", "get_job", "list_jobs", "rebuild"]
