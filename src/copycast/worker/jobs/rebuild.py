"""The rebuild job: ``adapters.storage.rebuild`` under the worker's own ``WORKER_LOCK``."""

from __future__ import annotations

from copycast.adapters.storage.rebuild import rebuild
from copycast.application.events import ResyncEvent
from copycast.application.ports import PermanentError
from copycast.logging import get_logger
from copycast.settings import SettingsError
from copycast.worker.jobs import JobContext, JobOutcome

log = get_logger(__name__)


async def run(ctx: JobContext) -> JobOutcome:
    payload = ctx.job.payload or {}
    dry_run = bool(payload.get("dry_run", True))
    try:
        report = await rebuild(
            ctx.container.settings,
            yes=not dry_run,
            lock_held=True,
            db_engine=ctx.container.db_engine,
        )
    except SettingsError as exc:  # LayoutMismatch: the data directory is not ours
        raise PermanentError(str(exc)) from exc
    async with ctx.uow() as uow:
        await uow.publish(ResyncEvent(reason="rebuild"))
    log.info(
        "rebuild.done",
        dry_run=dry_run,
        feeds=report.feeds_upserted,
        items=report.items_upserted,
        deleted_items=report.deleted_items,
    )
    return JobOutcome(result=report.model_dump(mode="json"))


__all__ = ["run"]
