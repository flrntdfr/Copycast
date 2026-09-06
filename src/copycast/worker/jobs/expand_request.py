"""The expand job: turn one Inbox Request into Catalog items and archive jobs.

A podcast feed URL is refused ("use create_mirror"); a URL serving
``audio/*`` or ``video/*`` becomes one item; anything else is listed by the
Engine (leaves capped at 1000). Every leaf is upserted ``wanted`` with
``wanted_reason=request`` (a deleted row flips back), linked to the Request
and queued for archiving.
"""

from __future__ import annotations

from collections.abc import Mapping
from posixpath import basename
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import update

from copycast.adapters.db.base import refresh_loaded
from copycast.adapters.db.models import CatalogItem
from copycast.adapters.db.uow import UnitOfWork
from copycast.adapters.sources import rss
from copycast.adapters.sources.http import (
    MAX_FEED_BYTES,
    NotAFeed,
    SourceError,
    content_type_of,
    fetch,
)
from copycast.application.events import FeedEvent, RequestEvent
from copycast.application.ports import (
    Cancelled,
    CancelToken,
    Engine,
    EngineError,
    EngineLog,
    PermanentError,
    StorageFull,
)
from copycast.application.services.items import PRIORITY_MANUAL
from copycast.domain.enums import (
    ArchiveState,
    FeedKind,
    JobTrigger,
    ListingOrder,
    ProgressPhase,
    RequestStatus,
    WantedReason,
)
from copycast.domain.listing import SourceListing, SourceListingItem
from copycast.logging import get_logger
from copycast.worker.constants import LEAF_CAP
from copycast.worker.jobs import JobContext, JobOutcome
from copycast.worker.jobs.common import engine_options_for, enqueue_archive_jobs

log = get_logger(__name__)

MEDIA_PREFIXES = ("audio/", "video/")
FEED_TYPES = frozenset(
    {"application/rss+xml", "application/xml", "text/xml", "application/atom+xml"}
)
DIRECT_SERVICE = "Direct"
USE_CREATE_MIRROR = "this URL is a podcast feed; mirror it with create_mirror instead"


def direct_listing(url: str, content_type: str | None) -> SourceListing:
    """One synthesized leaf for a URL that serves a media file."""
    name = basename(urlsplit(url).path) or url
    return SourceListing(
        service=DIRECT_SERVICE,
        title=name,
        webpage_url=url,
        listing_order=ListingOrder.newest_first,
        items=[
            SourceListingItem(
                source_key=url,
                source_url=url,
                title=name,
                position=0,
                enclosure_url=url,
                enclosure_type=content_type,
                archivable=True,
            )
        ],
    )


def _is_feed(url: str, content_type: str | None) -> bool:
    """True when the URL parses as an RSS document (checked only for XML-ish answers)."""
    looks_xml = (content_type in FEED_TYPES) or url.lower().endswith((".xml", ".rss"))
    if not looks_xml:
        return False
    try:
        fetched = fetch(url, max_bytes=MAX_FEED_BYTES, accept=rss.FEED_ACCEPT)
        rss.parse_feed(fetched.body, url=fetched.url)
    except (SourceError, NotAFeed):
        return False
    return True


def expand(
    engine: Engine, url: str, options: Mapping[str, Any], cancel: CancelToken, log_: EngineLog
) -> SourceListing:
    """The listing a Request expands to (blocking)."""
    content_type = content_type_of(url)
    if content_type and content_type.startswith(MEDIA_PREFIXES):
        return direct_listing(url, content_type)
    if _is_feed(url, content_type):
        raise PermanentError(USE_CREATE_MIRROR)
    listing = engine.list_source(url, options, cancel, log_)
    if not listing.items:
        raise PermanentError(f"nothing to archive at {url}")
    if len(listing.items) > LEAF_CAP:
        log_.warning(f"request lists {len(listing.items)} items; keeping the first {LEAF_CAP}")
        listing = listing.model_copy(update={"items": list(listing.items[:LEAF_CAP])})
    return listing


async def relist_hidden(uow: UnitOfWork, feed_id: str) -> int:
    """Undo the delisting ``upsert_listing`` applies to Inbox rows absent from one Request.

    An Inbox has no Source listing: every Request adds items, so the only rows
    that stay hidden are the tombstones (``deleted``).
    """
    result = await uow.session.execute(
        update(CatalogItem)
        .where(
            CatalogItem.feed_id == feed_id,
            CatalogItem.listed.is_(False),
            CatalogItem.archive_state != ArchiveState.deleted.value,
        )
        .values(listed=True)
        .returning(CatalogItem.id)
    )
    ids = list(result.scalars())
    await refresh_loaded(uow.session, CatalogItem, lambda row: row.feed_id == feed_id)
    return len(ids)


async def run(ctx: JobContext) -> JobOutcome:
    job = ctx.job
    if job.request_id is None:
        raise PermanentError("expand job without a request")
    request_id = job.request_id
    async with ctx.uow() as uow:
        request = await uow.requests.require(request_id)
        feed = await uow.feeds.require(request.feed_id)
        if feed.kind != FeedKind.inbox:
            raise PermanentError(f"feed {feed.id!r} is not an Inbox")
        feed_id, url = feed.id, request.url
        options = engine_options_for(ctx.container.settings, feed.engine_options, language=None)

    ctx.progress.set_phase(ProgressPhase.listing)
    try:
        ctx.check_cancelled("request expansion")
        listing = await ctx.run_blocking(
            expand, ctx.container.engine, url, options, ctx.cancel, ctx.log
        )
    except EngineError as exc:
        await _record_failure(ctx, feed_id, request_id, exc)
        raise
    except Exception as exc:
        wrapped = PermanentError(f"{type(exc).__name__}: {exc}")
        await _record_failure(ctx, feed_id, request_id, wrapped)
        raise wrapped from exc

    async with ctx.uow() as uow:
        upsert = await uow.catalog.upsert_listing(
            feed_id, listing, wanted_reason=WantedReason.request
        )
        await relist_hidden(uow, feed_id)
        linked = await uow.requests.link_items(request_id, upsert.seen_item_ids)
        rows = await uow.catalog.get_many(upsert.seen_item_ids)
        enqueued = await enqueue_archive_jobs(
            uow, rows, trigger=JobTrigger.request, request_id=request_id, priority=PRIORITY_MANUAL
        )
        await uow.requests.mark_expanded(request_id, len(upsert.seen_item_ids))
        _, revision = await uow.update_intent(feed_id)
        await uow.publish(
            RequestEvent(
                feed_id=feed_id,
                request_id=request_id,
                status=RequestStatus.expanded,
                item_count=len(upsert.seen_item_ids),
            )
        )
        await uow.publish(FeedEvent(feed_id=feed_id, revision=revision, reason="request"))
        if enqueued:
            await uow.notify_jobs()
    log.info(
        "request.expanded",
        request_id=str(request_id),
        feed_id=feed_id,
        items=len(upsert.seen_item_ids),
        new=upsert.new_count,
        enqueued=len(enqueued),
    )
    return JobOutcome(
        result={
            "item_count": len(upsert.seen_item_ids),
            "new": upsert.new_count,
            "linked": linked,
            "wanted": len(upsert.wanted_item_ids),
            "enqueued": len(enqueued),
            "service": listing.service,
        }
    )


async def _record_failure(ctx: JobContext, feed_id: str, request_id: Any, exc: EngineError) -> None:
    """Permanent (or the last transient attempt) -> Request ``failed``; else it stays queued."""
    final = isinstance(exc, PermanentError) or (
        not isinstance(exc, Cancelled | StorageFull) and ctx.exhausted
    )
    if not final:
        return
    async with ctx.uow() as uow:
        await uow.requests.mark_failed(request_id, str(exc))
        await uow.publish(
            RequestEvent(feed_id=feed_id, request_id=request_id, status=RequestStatus.failed)
        )
    log.warning("request.failed", request_id=str(request_id), error=str(exc))


__all__ = ["USE_CREATE_MIRROR", "direct_listing", "expand", "relist_hidden", "run"]
