"""Quota: storage per Owner and the items an Over quota Owner keeps waiting."""

from __future__ import annotations

from collections.abc import Collection, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from copycast.adapters.db.models import CatalogItem, Feed
from copycast.domain.enums import BlockedReason


class QuotaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
        raise NotImplementedError("quota blocking arrives with quota enforcement")

    async def owners_with_blocked(self) -> list[str]:
        raise NotImplementedError("quota blocking arrives with quota enforcement")

    async def blocked_for_release(self, owner: str, *, limit: int) -> Sequence[CatalogItem]:
        raise NotImplementedError("quota blocking arrives with quota enforcement")

    async def unblock(self, item_ids: Collection[str]) -> None:
        raise NotImplementedError("quota blocking arrives with quota enforcement")


__all__ = ["QuotaRepository"]
