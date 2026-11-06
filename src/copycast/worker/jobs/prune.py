"""The prune job: a Bucket's Retention (``{mode: auto}``) or a queued manual prune.

When it freed bytes, the Owner's items blocked by the quota that now fit are re-queued
at once (``quota.release_if_room``) rather than at the scheduler's next sweep.
"""

from __future__ import annotations

from typing import cast

from copycast.application.models import PruneRequest
from copycast.application.ports import PermanentError
from copycast.application.services.context import UnitOfWorkPort
from copycast.application.services.quota import release_if_room
from copycast.domain.enums import PruneMode
from copycast.domain.exceptions import DomainError
from copycast.logging import get_logger
from copycast.worker.jobs import JobContext, JobOutcome

log = get_logger(__name__)


async def run(ctx: JobContext) -> JobOutcome:
    job = ctx.job
    if job.feed_id is None:
        raise PermanentError("prune job without a feed")
    payload = job.payload or {}
    mode = PruneMode(str(payload.get("mode", PruneMode.auto.value)))
    services = ctx.container.services
    try:
        if mode is PruneMode.auto:
            result = await services.autoprune(job.feed_id)
        else:
            body = PruneRequest(
                downloaded=bool(payload.get("downloaded", False)),
                older_than_days=payload.get("older_than_days"),
                dry_run=False,
            )
            result = await services.prune_bucket(job.feed_id, body)
    except DomainError as exc:
        raise PermanentError(str(exc)) from exc
    except ValueError as exc:  # an unusable manual payload
        raise PermanentError(str(exc)) from exc
    released = await _release_blocked(ctx, job.feed_id) if result.bytes_freed > 0 else 0
    log.info(
        "prune.done",
        feed_id=job.feed_id,
        mode=mode.value,
        deleted=result.deleted_count,
        bytes_freed=result.bytes_freed,
        released=released,
    )
    return JobOutcome(
        result={
            "mode": mode.value,
            "matched": result.matched,
            "deleted_count": result.deleted_count,
            "bytes_freed": result.bytes_freed,
        }
    )


async def _release_blocked(ctx: JobContext, feed_id: str) -> int:
    """The fast path of the quota release; a failure here never fails the prune."""
    try:
        async with ctx.uow() as uow:
            feed = await uow.feeds.get(feed_id)
            if feed is None:
                return 0
            return await release_if_room(
                cast("UnitOfWorkPort", uow), ctx.container.users, feed.owner
            )
    except Exception as exc:
        log.warning("prune.release_failed", feed_id=feed_id, error=str(exc))
        return 0


__all__ = ["run"]
