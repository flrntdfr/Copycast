"""A Mirror's archive policy: what Backfill, Follow, a rolling window and Automatic want.

Shared by the worker's Refresh (after every listing) and by ``update_mirror``
(a mode change applies at once). Rolling is the one policy that deletes on
its own; ADR 0012 narrows ADR 0009 for it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from copycast.application.capabilities import capability
from copycast.application.events import FeedEvent, ItemEvent
from copycast.application.models import (
    BackfillRequest,
    MirrorChangePreview,
    MirrorDefaults,
    MirrorUpdate,
    PruneResult,
)
from copycast.application.services.context import FeedRow, ItemRow, ServiceContext, UnitOfWorkPort
from copycast.application.services.defaults import effective, load_defaults
from copycast.application.services.episodes import delete_episode
from copycast.application.services.feeds import require_mirror
from copycast.domain.enums import ArchiveState, BackfillMode, DeleteReason, WantedReason
from copycast.logging import get_logger

log = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def shortest_for(feed: FeedRow, defaults: MirrorDefaults) -> int | None:
    return effective(
        defaults,
        language=feed.preferred_language,
        min_duration_seconds=feed.min_duration_seconds,
    ).min_duration_seconds


async def delete_rows(
    uow: UnitOfWorkPort, feed: FeedRow, rows: Sequence[ItemRow], reason: DeleteReason
) -> PruneResult:
    """Tombstone ``rows`` and publish the item and feed events; files go after the commit."""
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


async def rolled_out(uow: UnitOfWorkPort, feed: FeedRow, *, size: int) -> list[ItemRow]:
    """Archived items outside the window of the newest ``size``."""
    window = set(await uow.catalog.window_ids(feed.id, size))
    archived = await uow.catalog.ids_in_state(feed.id, ArchiveState.archived)
    outside = [item_id for item_id in archived if item_id not in window]
    return list(await uow.catalog.get_many(outside)) if outside else []


async def apply_policy(
    uow: UnitOfWorkPort,
    feed: FeedRow,
    *,
    min_duration_seconds: int | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Want what the policy wants and roll out what a window no longer keeps.

    all: every listed archivable Available item once, then Follow wants items first seen
    after the policy was applied. latest: the N highest ordinals once, then Follow.
    rolling: the newest N by publication, every time, and archived items outside that
    window are tombstoned. automatic and selection: nothing. Deleted (tombstoned) and
    failed items are never re-wanted; items shorter than ``min_duration_seconds`` stay
    Available. Returns the ids made wanted.
    """
    now = now or _now()
    shortest = min_duration_seconds
    mode = BackfillMode(feed.backfill_mode or BackfillMode.all)
    if mode is BackfillMode.rolling:
        size = feed.backfill_latest_n or 1
        window = await uow.catalog.window_ids(feed.id, size, min_duration_seconds=shortest)
        available = set(await uow.catalog.available_ids(feed.id, min_duration_seconds=shortest))
        changed = list(
            await uow.catalog.set_wanted(
                [item_id for item_id in window if item_id in available], WantedReason.follow
            )
        )
        outside = await rolled_out(uow, feed, size=size)
        if outside:
            result = await delete_rows(uow, feed, outside, DeleteReason.rolled)
            log.info(
                "policy.rolled_out",
                feed_id=feed.id,
                deleted=result.deleted_count,
                bytes_freed=result.bytes_freed,
            )
        if feed.policy_applied_at is None:
            feed.policy_applied_at = now
            await uow.flush()
        return changed
    if feed.policy_applied_at is None:
        if mode is BackfillMode.all:
            ids = await uow.catalog.available_ids(feed.id, min_duration_seconds=shortest)
        elif mode is BackfillMode.latest:
            ids = await uow.catalog.available_ids(
                feed.id, latest_n=feed.backfill_latest_n, min_duration_seconds=shortest
            )
        else:
            ids: Sequence[str] = []
        changed = list(await uow.catalog.set_wanted(ids, WantedReason.backfill))
        feed.policy_applied_at = now
        await uow.flush()
        return changed
    if feed.follow and mode in (BackfillMode.all, BackfillMode.latest):
        ids = await uow.catalog.available_ids(
            feed.id, first_seen_after=feed.policy_applied_at, min_duration_seconds=shortest
        )
        return list(await uow.catalog.set_wanted(ids, WantedReason.follow))
    return []


async def preview_change(
    uow: UnitOfWorkPort, feed: FeedRow, backfill: BackfillRequest | None
) -> MirrorChangePreview:
    """What a policy change would delete now and archive next; nothing is changed."""
    if backfill is None:
        return MirrorChangePreview()
    if backfill.mode is BackfillMode.rolling:
        outside = await rolled_out(uow, feed, size=backfill.latest_n or 1)
        window = set(await uow.catalog.window_ids(feed.id, backfill.latest_n or 1))
        available = set(await uow.catalog.available_ids(feed.id))
        return MirrorChangePreview(
            would_delete_count=len(outside),
            would_delete_bytes=sum(row.media_bytes or 0 for row in outside),
            would_archive_count=len(window & available),
        )
    if backfill.mode is BackfillMode.all:
        ids = await uow.catalog.available_ids(feed.id)
        return MirrorChangePreview(would_archive_count=len(ids))
    if backfill.mode is BackfillMode.latest:
        ids = await uow.catalog.available_ids(feed.id, latest_n=backfill.latest_n)
        return MirrorChangePreview(would_archive_count=len(ids))
    return MirrorChangePreview()


@capability("preview_mirror_update", request=MirrorUpdate, response=MirrorChangePreview)
async def preview_mirror_update(
    ctx: ServiceContext, feed_id: str, body: MirrorUpdate
) -> MirrorChangePreview:
    """Count what a ``MirrorUpdate`` would delete and archive before it is applied."""
    async with ctx.uow_factory() as uow:
        feed = await require_mirror(uow, feed_id)
        return await preview_change(uow, feed, body.backfill)


async def expire_automatic(
    ctx: ServiceContext, feed_id: str, *, now: datetime | None = None
) -> PruneResult:
    """An Automatic Mirror's Retention: Episodes idle for ``retention_days`` are tombstoned."""
    now = now or _now()
    async with ctx.uow_factory() as uow:
        feed = await require_mirror(uow, feed_id)
        if feed.retention_days is None or feed.backfill_mode != BackfillMode.automatic:
            return PruneResult(matched=0, deleted_count=0, bytes_freed=0, dry_run=False)
        cutoff = now - timedelta(days=feed.retention_days)
        rows = await uow.catalog.prunable(feed.id, last_activity_before=cutoff)
        result = await delete_rows(uow, feed, list(rows), DeleteReason.expired)
        feed.last_autoprune_at = now
        await uow.flush()
        return result


def defaults_for(ctx: ServiceContext) -> MirrorDefaults:
    return load_defaults(ctx.layout)


__all__ = [
    "apply_policy",
    "defaults_for",
    "delete_rows",
    "expire_automatic",
    "preview_change",
    "preview_mirror_update",
    "rolled_out",
    "shortest_for",
]
