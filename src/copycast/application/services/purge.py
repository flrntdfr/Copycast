"""Purge and clear: delete archived Episodes, keeping feeds, Catalogs and artwork.

Purge is an operator's way to reclaim the disk at once; each feed is handled in its own
transaction so a large purge neither locks everything nor loses what it did. Clearing
empties one Feed's bucket of everything not kept for good.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from copycast.application.capabilities import capability
from copycast.application.models import (
    ClearFeedRequest,
    ClearFeedResult,
    PruneResult,
    PurgeRequest,
)
from copycast.application.services.context import FeedRow, ItemRow, ServiceContext
from copycast.application.services.policy import delete_rows
from copycast.application.services.retry import retry_concurrent
from copycast.domain.enums import ArchiveState, BackfillMode, DeleteReason, FeedKind
from copycast.logging import get_logger

log = get_logger(__name__)

ARCHIVE_POLICY_MODES: Final = frozenset(
    {BackfillMode.all, BackfillMode.new, BackfillMode.latest, BackfillMode.selection}
)
"""The modes of the Archive policy: what they archive stays for good."""


def keeps_everything(feed: FeedRow) -> bool:
    """Whether every Episode of ``feed`` is kept for good: a Mirror on the Archive policy."""
    return feed.kind == FeedKind.mirror and feed.backfill_mode in ARCHIVE_POLICY_MODES


@capability("clear_feed", request=ClearFeedRequest, response=ClearFeedResult)
async def clear_feed(ctx: ServiceContext, feed_id: str, body: ClearFeedRequest) -> ClearFeedResult:
    """Tombstone every archived Episode not kept for good; ``dry_run`` (the default) counts.

    Kept for good: an Episode flagged Archive, and every Episode of a Mirror on the Archive
    policy. The Feed, its Catalog and what is kept stay; the default Inbox may be cleared.
    """

    async def _clear() -> ClearFeedResult:
        async with ctx.uow_factory() as uow:
            feed = await uow.feeds.require(feed_id)
            ids = await uow.catalog.ids_in_state(feed_id, ArchiveState.archived)
            rows = list(await uow.catalog.get_many(ids)) if ids else []
            everything = keeps_everything(feed)
            kept = [row for row in rows if everything or row.archive_flag]
            doomed = [row for row in rows if not (everything or row.archive_flag)]
            if body.dry_run:
                return ClearFeedResult(
                    matched=len(doomed),
                    deleted_count=0,
                    bytes_freed=_bytes(doomed),
                    dry_run=True,
                    kept_count=len(kept),
                    kept_bytes=_bytes(kept),
                )
            result = await delete_rows(uow, feed, doomed, DeleteReason.user)
            await uow.feeds.recount_storage(feed_id)
            return ClearFeedResult(
                **result.model_dump(), kept_count=len(kept), kept_bytes=_bytes(kept)
            )

    result = await retry_concurrent(_clear, what=f"clear_feed:{feed_id}")
    if not body.dry_run:
        log.info("feed.cleared", feed_id=feed_id, deleted=result.deleted_count)
    return result


def _bytes(rows: Sequence[ItemRow]) -> int:
    return sum(row.media_bytes or 0 for row in rows)


@capability("purge_episodes", request=PurgeRequest, response=PruneResult)
async def purge_episodes(ctx: ServiceContext, body: PurgeRequest) -> PruneResult:
    """Tombstone every archived Episode everywhere (``dry_run`` only counts).

    Feeds keep listing them as Available; Rolling, Everything and Follow never
    re-archive a Tombstone, Automatic Mirrors download again on request.
    """
    async with ctx.uow_factory() as uow:
        feed_ids = [feed.id for feed in await uow.feeds.list()]
    matched = deleted = freed = 0
    for feed_id in feed_ids:
        async with ctx.uow_factory() as uow:
            feed = await uow.feeds.require(feed_id)
            ids = await uow.catalog.ids_in_state(feed_id, ArchiveState.archived)
            rows = list(await uow.catalog.get_many(ids)) if ids else []
            matched += len(rows)
            if body.dry_run:
                freed += sum(row.media_bytes or 0 for row in rows)
                continue
            if rows:
                result = await delete_rows(uow, feed, rows, DeleteReason.user)
                deleted += result.deleted_count
                freed += result.bytes_freed
                await uow.feeds.recount_storage(feed_id)
    if not body.dry_run:
        log.info("episodes.purged", feeds=len(feed_ids), deleted=deleted, bytes_freed=freed)
    return PruneResult(
        matched=matched, deleted_count=deleted, bytes_freed=freed, dry_run=body.dry_run
    )


__all__ = ["ARCHIVE_POLICY_MODES", "clear_feed", "keeps_everything", "purge_episodes"]
