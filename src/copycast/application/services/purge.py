"""Purge: delete every archived Episode of every feed, keeping feeds, Catalogs and artwork.

An operator's way to reclaim the disk at once. Each feed is handled in its own
transaction so a large purge neither locks everything nor loses what it did.
"""

from __future__ import annotations

from copycast.application.capabilities import capability
from copycast.application.models import PruneResult, PurgeRequest
from copycast.application.services.context import ServiceContext
from copycast.application.services.policy import delete_rows
from copycast.domain.enums import ArchiveState, DeleteReason
from copycast.logging import get_logger

log = get_logger(__name__)


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


__all__ = ["purge_episodes"]
