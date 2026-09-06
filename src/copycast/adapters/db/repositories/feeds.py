"""Feeds: Mirrors and Inboxes, their counters and the default Inbox."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from copycast.adapters.db.base import rows_affected, utcnow
from copycast.adapters.db.models import Asset, CatalogItem, Feed
from copycast.application.models import Totals
from copycast.domain.enums import ArchiveState, AssetState, FeedKind
from copycast.domain.exceptions import NotFound
from copycast.domain.ids import inbox_id

DEFAULT_INBOX_TITLE = "Copycast"

FeedSort = Literal["title", "created_at", "updated_at", "storage_bytes"]
_SORT_COLUMNS = {
    "title": func.lower(Feed.title),
    "created_at": Feed.created_at,
    "updated_at": Feed.updated_at,
    "storage_bytes": Feed.storage_bytes,
}


class FeedRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ reads

    async def get(self, feed_id: str) -> Feed | None:
        return await self._session.get(Feed, feed_id)

    async def require(self, feed_id: str) -> Feed:
        feed = await self.get(feed_id)
        if feed is None:
            raise NotFound("feed", feed_id)
        return feed

    async def get_for_update(self, feed_id: str) -> Feed:
        """Lock the feed row for the rest of the transaction (``SELECT ... FOR UPDATE``)."""
        result = await self._session.execute(
            select(Feed).where(Feed.id == feed_id).with_for_update()
        )
        feed = result.scalar_one_or_none()
        if feed is None:
            raise NotFound("feed", feed_id)
        return feed

    async def by_dedup_key(self, dedup_key: str) -> Feed | None:
        result = await self._session.execute(select(Feed).where(Feed.source_dedup_key == dedup_key))
        return result.scalar_one_or_none()

    async def list(
        self,
        kind: FeedKind | None = None,
        *,
        sort: FeedSort = "title",
        order: Literal["asc", "desc"] = "asc",
    ) -> list[Feed]:
        column = _SORT_COLUMNS[sort]
        stmt = select(Feed).order_by(column.desc() if order == "desc" else column.asc(), Feed.id)
        if kind is not None:
            stmt = stmt.where(Feed.kind == kind.value)
        return list((await self._session.execute(stmt)).scalars())

    async def ids(self) -> list[str]:
        return list((await self._session.execute(select(Feed.id).order_by(Feed.id))).scalars())

    async def count(self, kind: FeedKind | None = None) -> int:
        stmt = select(func.count()).select_from(Feed)
        if kind is not None:
            stmt = stmt.where(Feed.kind == kind.value)
        return int((await self._session.execute(stmt)).scalar_one())

    async def find_inbox(self, ident: str) -> Feed | None:
        """An Inbox by id, else by case-insensitive name; ``None`` when neither matches."""
        feed = await self.get(ident)
        if feed is not None and feed.kind == FeedKind.inbox:
            return feed
        result = await self._session.execute(
            select(Feed)
            .where(Feed.kind == FeedKind.inbox.value, func.lower(Feed.title) == ident.lower())
            .order_by(Feed.is_default.desc(), Feed.created_at)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def default_inbox(self) -> Feed | None:
        result = await self._session.execute(select(Feed).where(Feed.is_default.is_(True)))
        return result.scalar_one_or_none()

    async def mirrors_due(self, before: datetime) -> list[Feed]:
        """Mirrors not Paused whose last Refresh attempt is absent or older than ``before``."""
        stmt = (
            select(Feed)
            .where(
                Feed.kind == FeedKind.mirror.value,
                Feed.paused.is_(False),
                (Feed.last_refresh_attempt_at.is_(None)) | (Feed.last_refresh_attempt_at < before),
            )
            .order_by(Feed.last_refresh_attempt_at.asc().nulls_first(), Feed.id)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def inboxes_due_autoprune(self, before: datetime) -> list[Feed]:
        stmt = (
            select(Feed)
            .where(
                Feed.kind == FeedKind.inbox.value,
                Feed.autoprune_days.is_not(None),
                (Feed.last_autoprune_at.is_(None)) | (Feed.last_autoprune_at < before),
            )
            .order_by(Feed.id)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def totals(self) -> Totals:
        feeds = await self.count()
        episodes = int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(CatalogItem)
                    .where(CatalogItem.archive_state == ArchiveState.archived.value)
                )
            ).scalar_one()
        )
        storage = int(
            (
                await self._session.execute(select(func.coalesce(func.sum(Feed.storage_bytes), 0)))
            ).scalar_one()
        )
        return Totals(feeds=feeds, episodes=episodes, storage_bytes=storage)

    # ------------------------------------------------------------------ writes

    async def add(self, feed: Feed) -> Feed:
        self._session.add(feed)
        await self._session.flush()
        return feed

    async def delete(self, feed_id: str) -> bool:
        result = await self._session.execute(delete(Feed).where(Feed.id == feed_id))
        return rows_affected(result) > 0

    async def ensure_default_inbox(self, title: str = DEFAULT_INBOX_TITLE) -> Feed:
        """The one default Inbox, created on first call; safe under concurrency."""
        existing = await self.default_inbox()
        if existing is not None:
            return existing
        stmt = (
            insert(Feed)
            .values(
                id=inbox_id(title),
                kind=FeedKind.inbox.value,
                is_default=True,
                title=title,
                follow=False,
            )
            .on_conflict_do_nothing(
                index_elements=[Feed.is_default], index_where=text("is_default")
            )
        )
        await self._session.execute(stmt)
        feed = await self.default_inbox()
        if feed is None:  # pragma: no cover - the partial unique index guarantees one row
            raise RuntimeError("default Inbox could not be created")
        return feed

    async def bump_revision(self, feed_id: str) -> int:
        """Advance the published-feed change token; returns the new revision."""
        result = await self._session.execute(
            update(Feed)
            .where(Feed.id == feed_id)
            .values(revision=Feed.revision + 1)
            .returning(Feed.revision)
        )
        revision = result.scalar_one_or_none()
        if revision is None:
            raise NotFound("feed", feed_id)
        await self._refresh_if_loaded(feed_id)
        return int(revision)

    async def update_intent(self, feed_id: str) -> tuple[int, int]:
        """Bump ``intent_version`` and ``revision``; returns ``(intent_version, revision)``."""
        result = await self._session.execute(
            update(Feed)
            .where(Feed.id == feed_id)
            .values(intent_version=Feed.intent_version + 1, revision=Feed.revision + 1)
            .returning(Feed.intent_version, Feed.revision)
        )
        row = result.one_or_none()
        if row is None:
            raise NotFound("feed", feed_id)
        await self._refresh_if_loaded(feed_id)
        return int(row[0]), int(row[1])

    async def recount_storage(self, feed_id: str) -> int:
        """``storage_bytes`` = archived media bytes + archived asset bytes (two SUMs)."""
        media = (
            select(func.coalesce(func.sum(CatalogItem.media_bytes), 0))
            .where(
                CatalogItem.feed_id == feed_id,
                CatalogItem.archive_state == ArchiveState.archived.value,
            )
            .scalar_subquery()
        )
        assets = (
            select(func.coalesce(func.sum(Asset.size_bytes), 0))
            .where(Asset.feed_id == feed_id, Asset.state == AssetState.archived.value)
            .scalar_subquery()
        )
        result = await self._session.execute(
            update(Feed)
            .where(Feed.id == feed_id)
            .values(storage_bytes=media + assets)
            .returning(Feed.storage_bytes)
        )
        total = result.scalar_one_or_none()
        if total is None:
            raise NotFound("feed", feed_id)
        await self._refresh_if_loaded(feed_id)
        return int(total)

    async def set_refresh_attempt(self, feed_id: str, at: datetime | None = None) -> None:
        await self._session.execute(
            update(Feed).where(Feed.id == feed_id).values(last_refresh_attempt_at=at or utcnow())
        )
        await self._refresh_if_loaded(feed_id)

    async def set_refresh_outcome(
        self, feed_id: str, *, success_at: datetime | None, error: str | None
    ) -> None:
        values: dict[str, Any] = {"last_error": error}
        if success_at is not None:
            values["last_refresh_success_at"] = success_at
        await self._session.execute(update(Feed).where(Feed.id == feed_id).values(**values))
        await self._refresh_if_loaded(feed_id)

    async def apply_metadata(
        self,
        feed_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        author: str | None = None,
        artwork_url: str | None = None,
        language: str | None = None,
        service: str | None = None,
    ) -> None:
        """Non-blank overwrite of the Source metadata."""
        values = {
            k: v
            for k, v in {
                "title": title,
                "description": description,
                "author": author,
                "artwork_url": artwork_url,
                "language": language,
                "service": service,
            }.items()
            if v is not None and v.strip()
        }
        if not values:
            return
        await self._session.execute(update(Feed).where(Feed.id == feed_id).values(**values))
        await self._refresh_if_loaded(feed_id)

    async def _refresh_if_loaded(self, feed_id: str) -> None:
        """Keep an already-loaded ORM instance in step with a Core UPDATE."""
        instance = self._session.identity_map.get((Feed, (feed_id,), None))
        if instance is not None:
            await self._session.refresh(instance)


__all__ = ["DEFAULT_INBOX_TITLE", "FeedRepository", "FeedSort"]
