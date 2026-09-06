"""The prune job: an Inbox's Retention (``{mode: auto}``) or a queued manual prune."""

from __future__ import annotations

from copycast.application.models import PruneRequest
from copycast.application.ports import PermanentError
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
            result = await services.prune_inbox(job.feed_id, body)
    except DomainError as exc:
        raise PermanentError(str(exc)) from exc
    except ValueError as exc:  # an unusable manual payload
        raise PermanentError(str(exc)) from exc
    log.info(
        "prune.done",
        feed_id=job.feed_id,
        mode=mode.value,
        deleted=result.deleted_count,
        bytes_freed=result.bytes_freed,
    )
    return JobOutcome(
        result={
            "mode": mode.value,
            "matched": result.matched,
            "deleted_count": result.deleted_count,
            "bytes_freed": result.bytes_freed,
        }
    )


__all__ = ["run"]
