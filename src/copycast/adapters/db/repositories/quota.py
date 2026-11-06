"""Quota: storage per Owner and the items an Over quota Owner keeps waiting.

A blocked item keeps ``archive_state = wanted`` with ``blocked_reason`` set (and
``blocked_bytes``, the estimate that blocked it, or null when the Owner was already
over). Nothing here enqueues or publishes: ``application.services.quota`` does, in the
caller's unit of work.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from sqlalchemy import Case, case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from copycast.adapters.db.base import refresh_loaded
from copycast.adapters.db.locks import QUOTA_LOCK_SPACE
from copycast.adapters.db.models import CatalogItem, Feed
from copycast.domain.enums import ArchiveState, BlockedReason, WantedReason

_BLOCKABLE = (ArchiveState.wanted.value, ArchiveState.archiving.value)
"""States an item may be blocked from: queued, or archiving when the Engine's estimate
did not fit. A row that left them meanwhile (deleted, archived) is never resurrected."""


def _priority() -> Case[int]:
    """The archive priority an item's ``wanted_reason`` gives (manual 50, follow 100,
    backfill 200), as ``application.services.items`` and the worker compute it."""
    return case(
        (CatalogItem.wanted_reason == WantedReason.backfill.value, 200),
        (CatalogItem.wanted_reason == WantedReason.follow.value, 100),
        else_=50,
    )


class QuotaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_owner(self, owner: str) -> None:
        """Serialise quota admission for ``owner`` until this transaction ends.

        Every statement after it sees what an earlier holder committed (READ COMMITTED
        takes a fresh snapshot per statement), so check-then-add cannot overshoot.
        """
        await self._session.execute(
            select(func.pg_advisory_xact_lock(QUOTA_LOCK_SPACE, func.hashtext(owner)))
        )

    async def owned_storage_bytes(self, owner: str) -> int:
        """``SUM(feeds.storage_bytes)`` over ``owner``'s Feeds (0 without any)."""
        result = await self._session.execute(
            select(func.coalesce(func.sum(Feed.storage_bytes), 0)).where(Feed.owner == owner)
        )
        return int(result.scalar_one())

    async def storage_by_owner(self) -> dict[str, int]:
        """Usage of every Owner having Feeds."""
        result = await self._session.execute(
            select(Feed.owner, func.coalesce(func.sum(Feed.storage_bytes), 0)).group_by(Feed.owner)
        )
        return {owner: int(used) for owner, used in result.tuples()}

    async def mark_blocked(
        self, item_id: str, reason: BlockedReason, *, estimate_bytes: int | None
    ) -> None:
        """``archive_state`` -> wanted with ``reason`` and the estimate; publishes nothing."""
        await self._session.execute(
            update(CatalogItem)
            .where(CatalogItem.id == item_id, CatalogItem.archive_state.in_(_BLOCKABLE))
            .values(
                archive_state=ArchiveState.wanted.value,
                blocked_reason=reason.value,
                blocked_bytes=estimate_bytes,
            )
        )
        await refresh_loaded(self._session, CatalogItem, lambda row: row.id == item_id)

    async def owners_with_blocked(self) -> list[str]:
        """Every Owner with at least one blocked, still wanted item."""
        result = await self._session.execute(
            select(Feed.owner)
            .join(CatalogItem, CatalogItem.feed_id == Feed.id)
            .where(
                CatalogItem.blocked_reason.is_not(None),
                CatalogItem.archive_state == ArchiveState.wanted.value,
            )
            .group_by(Feed.owner)
            .order_by(Feed.owner)
        )
        return list(result.scalars())

    async def blocked_for_release(
        self, owner: str, *, limit: int, max_bytes: int | None = None
    ) -> Sequence[CatalogItem]:
        """``owner``'s blocked items in archive priority, unknown sizes first, newest first.

        With ``max_bytes``, an item whose estimate is larger is left out, so items too
        large for the room left never fill the ``limit`` and hide the ones that fit.
        Items of a Paused Feed stay where they are: a job would only be cancelled.
        """
        fits = (
            []
            if max_bytes is None
            else [(CatalogItem.blocked_bytes.is_(None)) | (CatalogItem.blocked_bytes <= max_bytes)]
        )
        result = await self._session.execute(
            select(CatalogItem)
            .join(Feed, CatalogItem.feed_id == Feed.id)
            .where(
                Feed.owner == owner,
                Feed.paused.is_(False),
                CatalogItem.blocked_reason.is_not(None),
                CatalogItem.archive_state == ArchiveState.wanted.value,
                *fits,
            )
            .order_by(
                _priority(),
                CatalogItem.blocked_bytes.asc().nulls_first(),
                CatalogItem.published_at.desc().nulls_last(),
                CatalogItem.id,
            )
            .limit(limit)
        )
        return list(result.scalars())

    async def unblock(self, item_ids: Collection[str]) -> None:
        """Clear ``blocked_reason`` and ``blocked_bytes``."""
        ids = list(dict.fromkeys(item_ids))
        if not ids:
            return
        await self._session.execute(
            update(CatalogItem)
            .where(CatalogItem.id.in_(ids), CatalogItem.blocked_reason.is_not(None))
            .values(blocked_reason=None, blocked_bytes=None)
        )
        wanted = set(ids)
        await refresh_loaded(self._session, CatalogItem, lambda row: row.id in wanted)

    async def clear_unwanted(self) -> int:
        """Drop the block of items that are no longer wanted (deleted, archived another way,
        unwanted by a policy switch); returns how many rows changed."""
        result = await self._session.execute(
            update(CatalogItem)
            .where(
                CatalogItem.blocked_reason.is_not(None),
                CatalogItem.archive_state != ArchiveState.wanted.value,
            )
            .values(blocked_reason=None, blocked_bytes=None)
            .returning(CatalogItem.id)
        )
        cleared = set(result.scalars())
        if cleared:
            await refresh_loaded(self._session, CatalogItem, lambda row: row.id in cleared)
        return len(cleared)


__all__ = ["QuotaRepository"]
