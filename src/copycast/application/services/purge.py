"""Purge and clear: delete archived Episodes, keeping feeds, Catalogs, artwork and locks.

Purge is an operator's way to reclaim the disk at once; each feed is handled in its own
transaction so a large purge neither locks everything nor loses what it did. Clearing
purges one Feed's bucket. Both spare every locked Episode (``archive_flag``).
"""

from __future__ import annotations

from collections.abc import Sequence

from copycast.application.capabilities import capability
from copycast.application.models import (
    ClearFeedRequest,
    ClearFeedResult,
    PruneResult,
    PurgeRequest,
)
from copycast.application.services.context import ItemRow, ServiceContext
from copycast.application.services.policy import delete_rows
from copycast.application.services.retry import retry_concurrent
from copycast.domain.enums import ArchiveState, DeleteReason
from copycast.logging import get_logger

log = get_logger(__name__)


@capability("clear_feed", request=ClearFeedRequest, response=ClearFeedResult)
async def clear_feed(ctx: ServiceContext, feed_id: str, body: ClearFeedRequest) -> ClearFeedResult:
    """Purge a Feed's bucket: tombstone every archived Episode that is not locked; ``dry_run``
    (the default) counts.

    Locked Episodes are kept and counted in ``kept_count``. The Feed, its Catalog and its own
    files (``feed.json``, ``source/``, the feed artwork) stay; the default Bucket may be cleared.
    """

    async def _clear() -> ClearFeedResult:
        async with ctx.uow_factory() as uow:
            feed = await uow.feeds.require(feed_id)
            ids = await uow.catalog.ids_in_state(feed_id, ArchiveState.archived)
            rows = list(await uow.catalog.get_many(ids)) if ids else []
            kept = [row for row in rows if row.archive_flag]
            doomed = [row for row in rows if not row.archive_flag]
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
    """Tombstone every archived Episode everywhere except the locked ones (``dry_run`` only
    counts; ``matched`` leaves the locked out).

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
            rows = [row for row in rows if not row.archive_flag]
            matched += len(rows)
            if body.dry_run:
                freed += _bytes(rows)
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


__all__ = ["clear_feed", "purge_episodes"]
