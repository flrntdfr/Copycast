"""Inboxes: creation, renaming, Requests and pruning (on demand here, automatic in the worker)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from copycast.application.capabilities import capability
from copycast.application.events import FeedEvent, ItemEvent, JobEvent, RequestEvent
from copycast.application.models import (
    InboxCreate,
    InboxRead,
    InboxUpdate,
    PruneRequest,
    PruneResult,
    RequestCreate,
    RequestPage,
    RequestRead,
)
from copycast.application.services.context import (
    AssetRow,
    FeedRow,
    ItemRow,
    ServiceContext,
    UnitOfWorkPort,
)
from copycast.application.services.episodes import delete_episode
from copycast.application.services.feeds import require_inbox
from copycast.application.services.items import PRIORITY_MANUAL, expand_dedup_key
from copycast.application.services.readmodels import inbox_read, job_read, request_read
from copycast.domain.enums import (
    ArchiveState,
    DeleteReason,
    FeedKind,
    JobKind,
    JobTrigger,
    RequestedVia,
    RequestStatus,
)
from copycast.domain.exceptions import Conflict
from copycast.domain.ids import inbox_id
from copycast.logging import get_logger

log = get_logger(__name__)
MAX_PAGE = 500


def _now() -> datetime:
    return datetime.now(UTC)


async def _inbox_read(uow: UnitOfWorkPort, ctx: ServiceContext, feed: FeedRow) -> InboxRead:
    counts = await uow.catalog.count_by_state(feed.id)
    request_count = (await uow.requests.count_for_feeds([feed.id])).get(feed.id, 0)
    return inbox_read(feed, ctx.urls, counts=counts, request_count=request_count)


@capability("create_inbox", request=InboxCreate, response=InboxRead)
async def create_inbox(ctx: ServiceContext, body: InboxCreate) -> InboxRead:
    """A new Inbox; its id is the slugged name plus a random suffix."""
    async with ctx.uow_factory() as uow:
        feed = await uow.feeds.add(
            ctx.rows.feed(
                id=inbox_id(body.name),
                kind=FeedKind.inbox.value,
                title=body.name,
                follow=False,
                paused=False,
                autoprune_days=body.autoprune_days,
                engine_options={},
            )
        )
        ctx.layout.ensure_feed_dirs(feed.id)
        await uow.update_intent(feed.id)
        await uow.publish(FeedEvent(feed_id=feed.id, revision=feed.revision, reason="created"))
        read = await _inbox_read(uow, ctx, feed)
    log.info("inbox.created", feed_id=read.id, name=body.name)
    return read


@capability("update_inbox", request=InboxUpdate, response=InboxRead)
async def update_inbox(ctx: ServiceContext, inbox: str, body: InboxUpdate) -> InboxRead:
    """Rename an Inbox or change its Retention (``autoprune_days: null`` switches it off)."""
    async with ctx.uow_factory() as uow:
        feed = await require_inbox(uow, inbox)
        if body.name is not None:
            feed.title = body.name
        if "autoprune_days" in body.model_fields_set:
            feed.autoprune_days = body.autoprune_days
        await uow.flush()
        await uow.update_intent(feed.id)
        await uow.publish(FeedEvent(feed_id=feed.id, revision=feed.revision, reason="updated"))
        return await _inbox_read(uow, ctx, feed)


# --------------------------------------------------------------------------- requests


@capability("add_request", request=RequestCreate, response=RequestRead)
async def add_request(
    ctx: ServiceContext,
    inbox: str,
    body: RequestCreate,
    *,
    via: RequestedVia = RequestedVia.ui,
) -> RequestRead:
    """Push a URL into an Inbox (by id or name); the worker expands it into items."""
    async with ctx.uow_factory() as uow:
        feed = await require_inbox(uow, inbox)
        request = await uow.requests.add(feed.id, body.url, via)
        job = await uow.jobs.enqueue(
            JobKind.expand_request,
            JobTrigger.request,
            feed_id=feed.id,
            request_id=request.id,
            priority=PRIORITY_MANUAL,
            dedup=expand_dedup_key(request.id),
        )
        if job is not None:
            await uow.publish(JobEvent(job=job_read(job)))
        await uow.publish(
            RequestEvent(
                feed_id=feed.id,
                request_id=request.id,
                status=RequestStatus.queued,
                item_count=0,
            )
        )
        await uow.notify_jobs()
        return await request_read(uow, ctx.urls, request, with_items=False)


@capability("list_requests", response=RequestPage)
async def list_requests(
    ctx: ServiceContext, inbox: str, *, limit: int = 100, offset: int = 0
) -> RequestPage:
    limit = max(1, min(limit, MAX_PAGE))
    offset = max(0, offset)
    async with ctx.uow_factory() as uow:
        feed = await require_inbox(uow, inbox)
        rows, total = await uow.requests.list_page(feed.id, limit=limit, offset=offset)
        requests = [await request_read(uow, ctx.urls, row) for row in rows]
        return RequestPage(requests=requests, total=total, limit=limit, offset=offset)


@capability("get_request", response=RequestRead)
async def get_request(ctx: ServiceContext, inbox: str, request_id: uuid.UUID) -> RequestRead:
    async with ctx.uow_factory() as uow:
        feed = await require_inbox(uow, inbox)
        request = await uow.requests.require(request_id, feed.id)
        return await request_read(uow, ctx.urls, request)


# --------------------------------------------------------------------------- prune


async def _estimate_bytes(uow: UnitOfWorkPort, rows: Sequence[ItemRow]) -> int:
    total = sum(row.media_bytes or 0 for row in rows)
    assets: Mapping[str, Sequence[AssetRow]] = (
        await uow.assets.for_items([row.id for row in rows]) if rows else {}
    )
    for group in assets.values():
        total += sum(asset.size_bytes or 0 for asset in group)
    return total


async def _delete_rows(
    uow: UnitOfWorkPort, feed: FeedRow, rows: Sequence[ItemRow], reason: DeleteReason
) -> PruneResult:
    freed = 0
    for row in rows:
        deleted = await delete_episode(uow, row.id, reason)
        freed += deleted.bytes_freed
        await uow.publish(ItemEvent(feed_id=feed.id, item_id=row.id, state=ArchiveState.deleted))
    if rows:
        await uow.publish(
            FeedEvent(feed_id=feed.id, revision=feed.revision, reason=f"prune:{reason.value}")
        )
    return PruneResult(matched=len(rows), deleted_count=len(rows), bytes_freed=freed, dry_run=False)


@capability("prune_inbox", request=PruneRequest, response=PruneResult)
async def prune_inbox(ctx: ServiceContext, inbox: str, body: PruneRequest) -> PruneResult:
    """Delete archived Inbox Episodes matching every criterion (synchronous); ``dry_run`` counts."""
    async with ctx.uow_factory() as uow:
        feed = await require_inbox(uow, inbox)
        added_before = (
            _now() - timedelta(days=body.older_than_days)
            if body.older_than_days is not None
            else None
        )
        rows = await uow.catalog.prunable(
            feed.id, downloaded=body.downloaded, added_before=added_before
        )
        if body.dry_run:
            return PruneResult(
                matched=len(rows),
                deleted_count=0,
                bytes_freed=await _estimate_bytes(uow, rows),
                dry_run=True,
            )
        result = await _delete_rows(uow, feed, rows, DeleteReason.prune)
    log.info("inbox.pruned", feed_id=feed.id, deleted=result.deleted_count)
    return result


async def autoprune(
    ctx: ServiceContext, feed_id: str, *, now: datetime | None = None
) -> PruneResult:
    """The worker's scheduled prune: Episodes first downloaded ``autoprune_days`` ago or earlier.

    Never-downloaded Episodes are exempt by construction; ``last_autoprune_at``
    is set even when nothing matched.
    """
    now = now or _now()
    async with ctx.uow_factory() as uow:
        feed = await require_inbox(uow, feed_id)
        if feed.autoprune_days is None:
            raise Conflict(f"Inbox {feed_id!r} has no Retention")
        cutoff = now - timedelta(days=feed.autoprune_days)
        rows = await uow.catalog.prunable(feed.id, downloaded_before=cutoff)
        result = await _delete_rows(uow, feed, rows, DeleteReason.prune)
        feed.last_autoprune_at = now
        await uow.flush()
        return result


__all__ = [
    "MAX_PAGE",
    "add_request",
    "autoprune",
    "create_inbox",
    "get_request",
    "list_requests",
    "prune_inbox",
    "update_inbox",
]
