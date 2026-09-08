"""Catalog items: paging, archiving on demand, deleting (tombstones) and download counting."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from copycast.application.capabilities import capability
from copycast.application.events import FeedEvent, ItemEvent, JobEvent
from copycast.application.models import ItemPage, ItemRead, JobRead
from copycast.application.services.context import (
    ItemRow,
    ItemSort,
    JobRow,
    Order,
    ServiceContext,
    UnitOfWorkPort,
)
from copycast.application.services.defaults import effective
from copycast.application.services.episodes import delete_episode
from copycast.application.services.policy import defaults_for
from copycast.application.services.readmodels import item_reads, job_read
from copycast.application.services.retry import retry_concurrent
from copycast.domain.enums import (
    ArchiveState,
    DeleteReason,
    JobKind,
    JobTrigger,
    WantedReason,
)
from copycast.domain.exceptions import Conflict, Unsupported
from copycast.logging import get_logger

PRIORITY_MANUAL = 50
PRIORITY_FOLLOW = 100
PRIORITY_BACKFILL = 200
MAX_PAGE = 500


def archive_dedup_key(item_id: str) -> str:
    return f"archive:{item_id}"


def refresh_dedup_key(feed_id: str) -> str:
    return f"refresh:{feed_id}"


def expand_dedup_key(request_id: object) -> str:
    return f"expand:{request_id}"


def prune_dedup_key(feed_id: str) -> str:
    return f"prune:{feed_id}"


log = get_logger(__name__)


@capability("list_items", response=ItemPage)
async def list_items(
    ctx: ServiceContext,
    feed_id: str,
    *,
    state: ArchiveState | Sequence[ArchiveState] | None = None,
    listed: bool | None = None,
    q: str | None = None,
    sort: ItemSort = "published",
    order: Order = "desc",
    limit: int = 100,
    offset: int = 0,
) -> ItemPage:
    """One page of a Feed's Catalog with assets inlined; ``limit`` is capped at 500."""
    limit = max(1, min(limit, MAX_PAGE))
    offset = max(0, offset)
    async with ctx.uow_factory() as uow:
        await uow.feeds.require(feed_id)
        rows, total = await uow.catalog.list_page(
            feed_id,
            state=state,
            listed=listed,
            q=q,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )
        return ItemPage(
            items=await item_reads(uow, ctx.urls, list(rows)),
            total=total,
            limit=limit,
            offset=offset,
        )


@capability("get_item", response=ItemRead)
async def get_item(ctx: ServiceContext, feed_id: str, item_id: str) -> ItemRead:
    async with ctx.uow_factory() as uow:
        item = await uow.catalog.require(item_id, feed_id)
        return (await item_reads(uow, ctx.urls, [item]))[0]


async def enqueue_archive(
    uow: UnitOfWorkPort,
    item: ItemRow,
    *,
    trigger: JobTrigger,
    priority: int,
) -> JobRow | None:
    """Queue ``archive_item`` for a wanted item; returns the job (existing or new) or None."""
    job = await uow.jobs.enqueue(
        JobKind.archive_item,
        trigger,
        feed_id=item.feed_id,
        item_id=item.id,
        priority=priority,
        dedup=archive_dedup_key(item.id),
    )
    if job is not None:
        await uow.publish(JobEvent(job=job_read(job)))
        return job
    return await uow.jobs.active_by_dedup_key(archive_dedup_key(item.id))


@capability("archive_item", response=JobRead)
async def archive_item(
    ctx: ServiceContext,
    feed_id: str,
    item_id: str,
    *,
    trigger: JobTrigger = JobTrigger.manual,
) -> JobRead:
    """Archive one Available (or deleted, or failed) item now; 409 when already archived."""
    async with ctx.uow_factory() as uow:
        item = await uow.catalog.require(item_id, feed_id)
        if not item.archivable:
            raise Unsupported(f"item {item_id!r} has no media to archive")
        if item.archive_state == ArchiveState.archived:
            raise Conflict(f"item {item_id!r} is already archived")
        changed = await uow.catalog.set_wanted([item_id], WantedReason.manual)
        if changed:
            item = await uow.catalog.require(item_id, feed_id)
            await uow.update_intent(feed_id)
            await uow.publish(
                ItemEvent(feed_id=feed_id, item_id=item_id, state=ArchiveState.wanted)
            )
        job = await enqueue_archive(uow, item, trigger=trigger, priority=PRIORITY_MANUAL)
        if job is None:
            raise Conflict(f"item {item_id!r} is being archived by a job that is finishing")
        await uow.notify_jobs()
        return job_read(job)


@capability("fetch_item_metadata", response=ItemRead)
async def fetch_item_metadata(ctx: ServiceContext, feed_id: str, item_id: str) -> ItemRead:
    """Ask the Source for one item's full metadata now: description, exact date, artwork.

    A flat YouTube listing has neither a description nor an exact date; this is
    the manual, one-request way to get them before the item is archived.
    """
    async with ctx.uow_factory() as uow:
        item = await uow.catalog.require(item_id, feed_id)
        feed = await uow.feeds.require(feed_id)
        url = item.source_url
        options = dict(feed.engine_options or {})
        language = effective(
            defaults_for(ctx), language=feed.preferred_language, min_duration_seconds=None
        ).language
    if not url:
        raise Unsupported(f"item {item_id!r} has no Source page to ask")
    found = await asyncio.to_thread(
        ctx.sources.inspect_video, url, options=options, language=language
    )
    if found is None:
        raise Unsupported(f"the Source returned nothing for {url}")
    async with ctx.uow_factory() as uow:
        await uow.catalog.fill_metadata(
            item_id,
            description=found.description,
            published_at=found.published_at if found.published_at_exact else None,
            author=found.author,
            artwork_url=found.artwork_url,
            overwrite=True,
        )
        item = await uow.catalog.require(item_id, feed_id)
        _, revision = await uow.update_intent(feed_id)
        await uow.publish(
            ItemEvent(feed_id=feed_id, item_id=item_id, state=ArchiveState(item.archive_state))
        )
        await uow.publish(FeedEvent(feed_id=feed_id, revision=revision, reason="metadata"))
    log.info("item.metadata_fetched", feed_id=feed_id, item_id=item_id, url=url)
    return await get_item(ctx, feed_id, item_id)


@capability("delete_item")
async def delete_item(ctx: ServiceContext, feed_id: str, item_id: str) -> None:
    """Delete an Episode's media and tombstone the row (never re-archived automatically).

    Retried when the transaction loses a deadlock against the worker archiving it.
    """

    async def _delete() -> None:
        async with ctx.uow_factory() as uow:
            await uow.catalog.require(item_id, feed_id)
            job = await uow.jobs.latest_for_item(item_id)
            if job is not None and job.is_active:
                cancelled = await uow.jobs.cancel(job.id)
                await uow.publish(JobEvent(job=job_read(cancelled)))
            deleted = await delete_episode(uow, item_id, DeleteReason.user)
            await uow.publish(
                ItemEvent(feed_id=feed_id, item_id=item_id, state=ArchiveState.deleted)
            )
            await uow.publish(
                FeedEvent(feed_id=feed_id, revision=deleted.revision, reason="item-deleted")
            )

    await retry_concurrent(_delete, what=f"delete_item:{item_id}")


@capability("record_download")
async def record_download(ctx: ServiceContext, feed_id: str, item_id: str) -> bool:
    """Count one download of an archived item (the byte-0 rule is the media route's)."""
    async with ctx.uow_factory() as uow:
        item = await uow.catalog.get(item_id)
        if item is None or item.feed_id != feed_id:
            return False
        return await uow.catalog.record_download(item_id)


__all__ = [
    "MAX_PAGE",
    "PRIORITY_BACKFILL",
    "PRIORITY_FOLLOW",
    "PRIORITY_MANUAL",
    "archive_dedup_key",
    "archive_item",
    "delete_item",
    "enqueue_archive",
    "expand_dedup_key",
    "fetch_item_metadata",
    "get_item",
    "list_items",
    "prune_dedup_key",
    "record_download",
    "refresh_dedup_key",
]
