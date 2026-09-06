"""Feeds: listing, reading and deleting Mirrors and Inboxes; the default Inbox."""

from __future__ import annotations

from copycast.application.capabilities import capability
from copycast.application.events import FeedEvent
from copycast.application.models import FeedList, FeedRead, InboxRead
from copycast.application.services.context import (
    FeedRow,
    FeedSort,
    Order,
    ServiceContext,
    UnitOfWorkPort,
)
from copycast.application.services.readmodels import feed_reads, inbox_read, one_feed_read
from copycast.application.services.retry import retry_concurrent
from copycast.domain.credentials import new_feed_password, new_feed_username
from copycast.domain.enums import FeedKind
from copycast.domain.exceptions import Conflict, NotFound
from copycast.logging import get_logger

log = get_logger(__name__)


@capability("list_feeds", response=FeedList)
async def list_feeds(
    ctx: ServiceContext,
    kind: FeedKind | None = None,
    *,
    sort: FeedSort = "title",
    order: Order = "asc",
) -> FeedList:
    """Every Feed (optionally one kind) with its counts, health and public URL."""
    async with ctx.uow_factory() as uow:
        rows = await uow.feeds.list(kind, sort=sort, order=order)
        return FeedList(feeds=await feed_reads(uow, ctx.urls, list(rows)))


@capability("get_feed")
async def get_feed(ctx: ServiceContext, feed_id: str) -> FeedRead:
    """One Feed by id; 404 when unknown."""
    async with ctx.uow_factory() as uow:
        feed = await uow.feeds.require(feed_id)
        return await one_feed_read(uow, ctx.urls, feed)


async def require_mirror(uow: UnitOfWorkPort, feed_id: str) -> FeedRow:
    feed = await uow.feeds.require(feed_id)
    if feed.kind != FeedKind.mirror:
        raise NotFound("mirror", feed_id)
    return feed


async def require_inbox(uow: UnitOfWorkPort, ident: str) -> FeedRow:
    """An Inbox by id or case-insensitive name."""
    feed = await uow.feeds.find_inbox(ident)
    if feed is None:
        raise NotFound("inbox", ident)
    return feed


@capability("delete_feed")
async def delete_feed(ctx: ServiceContext, feed_id: str) -> None:
    """Delete a Feed and its data directory; the default Inbox is refused (409).

    Active jobs are cancelled first; the files go once the transaction committed.
    The transaction is retried when it loses a deadlock against a running job of
    the same Feed (the worker locks item -> feed, the cascade locks feed -> items).
    """

    async def _delete() -> None:
        async with ctx.uow_factory() as uow:
            feed = await uow.feeds.require(feed_id)
            if feed.is_default:
                raise Conflict("the default Inbox cannot be deleted; rename it instead")
            await uow.jobs.cancel_for_feed(feed_id)
            revision = feed.revision
            await uow.feeds.delete(feed_id)
            await uow.publish(FeedEvent(feed_id=feed_id, revision=revision, reason="deleted"))
            layout = ctx.layout

            def _remove_files() -> None:
                layout.remove_feed(feed_id)

            uow.after_commit(_remove_files)

    await retry_concurrent(_delete, what=f"delete_feed:{feed_id}")
    log.info("feed.deleted", feed_id=feed_id)


@capability("rotate_feed_credentials")
async def rotate_feed_credentials(ctx: ServiceContext, feed_id: str) -> FeedRead:
    """Mint a new Basic auth pair for a Feed; every client holding the old one stops working."""
    async with ctx.uow_factory() as uow:
        feed = await uow.feeds.require(feed_id)
        feed.auth_username = new_feed_username()
        feed.auth_password = new_feed_password()
        await uow.flush()
        await uow.update_intent(feed_id)
        await uow.publish(FeedEvent(feed_id=feed_id, revision=feed.revision, reason="updated"))
        read = await one_feed_read(uow, ctx.urls, feed)
    log.info("feed.credentials_rotated", feed_id=feed_id)
    return read


@capability("ensure_default_inbox", response=InboxRead)
async def ensure_default_inbox(ctx: ServiceContext) -> InboxRead:
    """The one default Inbox "Copycast", created on first call (api lifespan, worker start)."""
    async with ctx.uow_factory() as uow:
        existed = await uow.feeds.default_inbox()
        feed = await uow.feeds.ensure_default_inbox()
        if existed is None:
            await uow.update_intent(feed.id)
            ctx.layout.ensure_feed_dirs(feed.id)
            log.info("inbox.default_created", feed_id=feed.id)
        counts = await uow.catalog.count_by_state(feed.id)
        request_count = (await uow.requests.count_for_feeds([feed.id])).get(feed.id, 0)
        return inbox_read(feed, ctx.urls, counts=counts, request_count=request_count)


@capability("resolve_inbox", response=InboxRead)
async def resolve_inbox(ctx: ServiceContext, ident: str) -> InboxRead:
    """An Inbox by id or case-insensitive name (MCP's ``inbox="Copycast"`` default)."""
    async with ctx.uow_factory() as uow:
        feed = await require_inbox(uow, ident)
        counts = await uow.catalog.count_by_state(feed.id)
        request_count = (await uow.requests.count_for_feeds([feed.id])).get(feed.id, 0)
        return inbox_read(feed, ctx.urls, counts=counts, request_count=request_count)


__all__ = [
    "delete_feed",
    "ensure_default_inbox",
    "get_feed",
    "list_feeds",
    "require_inbox",
    "require_mirror",
    "resolve_inbox",
    "rotate_feed_credentials",
]
