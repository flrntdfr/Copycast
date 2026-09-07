"""The Catalog: listing upserts with ordinals, archive-state transitions, selections, counting."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import (
    Boolean,
    Integer,
    Select,
    String,
    Table,
    Text,
    Update,
    and_,
    bindparam,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from copycast.adapters.db.base import TZDateTime, refresh_loaded, rows_affected, utcnow
from copycast.adapters.db.models import CatalogItem, Feed
from copycast.application.models import CatalogCounts
from copycast.domain.enums import ArchiveState, Numbering, WantedReason
from copycast.domain.exceptions import NotFound
from copycast.domain.ids import item_id as make_item_id
from copycast.domain.listing import SourceListing, SourceListingItem
from copycast.domain.ordinals import assign_ordinals
from copycast.domain.selection import (
    ResolvedSelection,
    SelectionCandidate,
    resolve_selection,
)

ItemSort = Literal["published", "ordinal", "title", "added"]
RE_WANTABLE: tuple[str, ...] = (
    ArchiveState.available.value,
    ArchiveState.deleted.value,
    ArchiveState.failed.value,
)
PUBLISHED_ORDER = (
    CatalogItem.published_at.desc().nulls_last(),
    CatalogItem.ordinal.desc(),
)


@dataclass(frozen=True, slots=True)
class ListingUpsert:
    """What one :meth:`CatalogRepository.upsert_listing` did."""

    listed_count: int
    new_count: int
    delisted_count: int
    new_item_ids: list[str] = field(default_factory=list[str])
    seen_item_ids: list[str] = field(default_factory=list[str])
    wanted_item_ids: list[str] = field(default_factory=list[str])


class CatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ reads

    async def get(self, item_id: str) -> CatalogItem | None:
        return await self._session.get(CatalogItem, item_id)

    async def require(self, item_id: str, feed_id: str | None = None) -> CatalogItem:
        item = await self.get(item_id)
        if item is None or (feed_id is not None and item.feed_id != feed_id):
            raise NotFound("item", item_id)
        return item

    async def get_many(self, item_ids: Iterable[str]) -> list[CatalogItem]:
        ids = list(dict.fromkeys(item_ids))
        if not ids:
            return []
        result = await self._session.execute(
            select(CatalogItem).where(CatalogItem.id.in_(ids)).order_by(CatalogItem.ordinal)
        )
        return list(result.scalars())

    async def by_source_key(self, feed_id: str, source_key: str) -> CatalogItem | None:
        result = await self._session.execute(
            select(CatalogItem).where(
                CatalogItem.feed_id == feed_id, CatalogItem.source_key == source_key.strip()
            )
        )
        return result.scalar_one_or_none()

    async def for_feed(self, feed_id: str) -> list[CatalogItem]:
        result = await self._session.execute(
            select(CatalogItem).where(CatalogItem.feed_id == feed_id).order_by(CatalogItem.ordinal)
        )
        return list(result.scalars())

    async def for_render(self, feed_id: str) -> list[CatalogItem]:
        """Archived items in feed order: ``published_at DESC NULLS LAST, ordinal DESC``."""
        result = await self._session.execute(
            select(CatalogItem)
            .where(
                CatalogItem.feed_id == feed_id,
                CatalogItem.archive_state == ArchiveState.archived.value,
            )
            .order_by(*PUBLISHED_ORDER)
        )
        return list(result.scalars())

    async def max_ordinal(self, feed_id: str) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(CatalogItem.ordinal), 0)).where(
                CatalogItem.feed_id == feed_id
            )
        )
        return int(result.scalar_one())

    async def list_page(
        self,
        feed_id: str,
        *,
        state: ArchiveState | Sequence[ArchiveState] | None = None,
        listed: bool | None = None,
        q: str | None = None,
        sort: ItemSort = "published",
        order: Literal["asc", "desc"] = "desc",
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[CatalogItem], int]:
        base: Select[tuple[CatalogItem]] = select(CatalogItem).where(CatalogItem.feed_id == feed_id)
        if state is not None:
            states = [state] if isinstance(state, ArchiveState) else list(state)
            base = base.where(CatalogItem.archive_state.in_([s.value for s in states]))
        if listed is not None:
            base = base.where(CatalogItem.listed.is_(listed))
        if q and q.strip():
            pattern = f"%{q.strip()}%"
            base = base.where(
                or_(CatalogItem.title.ilike(pattern), CatalogItem.description.ilike(pattern))
            )
        total = int(
            (
                await self._session.execute(select(func.count()).select_from(base.subquery()))
            ).scalar_one()
        )
        desc = order == "desc"
        match sort:
            case "ordinal":
                ordering = [CatalogItem.ordinal.desc() if desc else CatalogItem.ordinal.asc()]
            case "title":
                title = func.lower(CatalogItem.title)
                ordering = [title.desc() if desc else title.asc(), CatalogItem.ordinal.desc()]
            case "added":
                ordering = [
                    CatalogItem.first_seen_at.desc() if desc else CatalogItem.first_seen_at.asc(),
                    CatalogItem.ordinal.desc() if desc else CatalogItem.ordinal.asc(),
                ]
            case _:
                ordering = (
                    list(PUBLISHED_ORDER)
                    if desc
                    else [CatalogItem.published_at.asc().nulls_first(), CatalogItem.ordinal.asc()]
                )
        rows = await self._session.execute(base.order_by(*ordering).limit(limit).offset(offset))
        return list(rows.scalars()), total

    async def count_by_state(self, feed_id: str) -> CatalogCounts:
        return (await self.count_by_state_many([feed_id])).get(feed_id, CatalogCounts())

    async def count_by_state_many(self, feed_ids: Iterable[str]) -> dict[str, CatalogCounts]:
        """Derived Catalog counts per feed in one query."""
        ids = list(dict.fromkeys(feed_ids))
        if not ids:
            return {}
        archived = CatalogItem.archive_state == ArchiveState.archived.value
        available = CatalogItem.archive_state.in_(
            [ArchiveState.available.value, ArchiveState.deleted.value]
        )
        stmt = (
            select(
                CatalogItem.feed_id,
                func.count().filter(and_(archived, CatalogItem.listed.is_(True))),
                func.count().filter(and_(available, CatalogItem.listed.is_(True))),
                func.count().filter(and_(archived, CatalogItem.listed.is_(False))),
                func.count().filter(CatalogItem.archive_state == ArchiveState.wanted.value),
                func.count().filter(archived),
                func.count().filter(CatalogItem.archive_state == ArchiveState.failed.value),
            )
            .where(CatalogItem.feed_id.in_(ids))
            .group_by(CatalogItem.feed_id)
        )
        counts: dict[str, CatalogCounts] = {}
        for row in await self._session.execute(stmt):
            counts[row[0]] = CatalogCounts(
                listed=row[1],
                available=row[2],
                delisted=row[3],
                wanted=row[4],
                archived=row[5],
                failed=row[6],
            )
        return counts

    async def selection_candidates(self, feed_id: str) -> list[SelectionCandidate]:
        stmt = (
            select(
                CatalogItem.id,
                CatalogItem.ordinal,
                CatalogItem.source_number,
                CatalogItem.archive_state,
                CatalogItem.archivable,
            )
            .where(CatalogItem.feed_id == feed_id)
            .order_by(CatalogItem.ordinal)
        )
        return [
            SelectionCandidate(
                item_id=row[0],
                ordinal=row[1],
                source_number=row[2],
                archive_state=ArchiveState(row[3]),
                archivable=row[4],
            )
            for row in await self._session.execute(stmt)
        ]

    async def resolve_numbers(
        self,
        feed_id: str,
        *,
        numbers: Sequence[int] = (),
        item_ids: Sequence[str] = (),
        numbering: Numbering = Numbering.source,
    ) -> ResolvedSelection:
        """Map ``"1-42, 180"`` numbers and explicit ids onto this feed's items."""
        candidates = await self.selection_candidates(feed_id)
        return resolve_selection(
            candidates, numbers=numbers, item_ids=item_ids, numbering=numbering
        )

    async def available_ids(
        self,
        feed_id: str,
        *,
        first_seen_after: datetime | None = None,
        latest_n: int | None = None,
        min_duration_seconds: int | None = None,
    ) -> list[str]:
        """Listed, archivable, Available items, newest ordinal first (policy input).

        ``min_duration_seconds`` leaves out shorter items; an unknown length passes.
        """
        stmt = (
            select(CatalogItem.id)
            .where(
                CatalogItem.feed_id == feed_id,
                CatalogItem.listed.is_(True),
                CatalogItem.archivable.is_(True),
                CatalogItem.archive_state == ArchiveState.available.value,
            )
            .order_by(CatalogItem.ordinal.desc())
        )
        if first_seen_after is not None:
            stmt = stmt.where(CatalogItem.first_seen_at > first_seen_after)
        if min_duration_seconds is not None:
            stmt = stmt.where(
                or_(
                    CatalogItem.duration_seconds.is_(None),
                    CatalogItem.duration_seconds >= min_duration_seconds,
                )
            )
        if latest_n is not None:
            stmt = stmt.limit(latest_n)
        return list((await self._session.execute(stmt)).scalars())

    async def ids_in_state(self, feed_id: str, state: ArchiveState) -> list[str]:
        stmt = (
            select(CatalogItem.id)
            .where(CatalogItem.feed_id == feed_id, CatalogItem.archive_state == state.value)
            .order_by(CatalogItem.ordinal)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def prunable(
        self,
        feed_id: str,
        *,
        downloaded: bool = False,
        added_before: datetime | None = None,
        downloaded_before: datetime | None = None,
    ) -> list[CatalogItem]:
        """Archived items matching every given criterion (AND); at least one is required.

        ``downloaded``: downloaded at least once. ``added_before``: ``first_seen_at`` older.
        ``downloaded_before``: ``first_downloaded_at`` older (autoprune; never-downloaded
        items are exempt by construction).
        """
        if not downloaded and added_before is None and downloaded_before is None:
            raise ValueError("prunable() needs at least one criterion")
        stmt = select(CatalogItem).where(
            CatalogItem.feed_id == feed_id,
            CatalogItem.archive_state == ArchiveState.archived.value,
        )
        if downloaded:
            stmt = stmt.where(CatalogItem.first_downloaded_at.is_not(None))
        if added_before is not None:
            stmt = stmt.where(CatalogItem.first_seen_at <= added_before)
        if downloaded_before is not None:
            stmt = stmt.where(CatalogItem.first_downloaded_at <= downloaded_before)
        result = await self._session.execute(stmt.order_by(CatalogItem.ordinal))
        return list(result.scalars())

    # ------------------------------------------------------------------ writes

    async def add(self, item: CatalogItem) -> CatalogItem:
        self._session.add(item)
        await self._session.flush()
        return item

    async def upsert_listing(
        self,
        feed_id: str,
        listing: SourceListing,
        *,
        now: datetime | None = None,
        wanted_reason: WantedReason | None = None,
    ) -> ListingUpsert:
        """Apply a Source listing to the Catalog under ``SELECT ... FOR UPDATE`` on the feed.

        Unseen keys get ordinals from the oldest onwards, known keys get their
        metadata, ``source_number``/``source_position``/``tab`` and ``listed``
        refreshed, rows absent from a non-empty listing are delisted. With
        ``wanted_reason`` every archivable item of the listing that is
        Available, deleted or failed becomes wanted (Requests use this).
        """
        now = now or utcnow()
        feed = (
            await self._session.execute(select(Feed).where(Feed.id == feed_id).with_for_update())
        ).scalar_one_or_none()
        if feed is None:
            raise NotFound("feed", feed_id)

        items: dict[str, SourceListingItem] = {}
        for entry in listing.items:
            key = entry.source_key.strip()
            if key and key not in items:
                items[key] = entry

        existing_rows = await self._session.execute(
            select(CatalogItem.id, CatalogItem.source_key, CatalogItem.archive_state).where(
                CatalogItem.feed_id == feed_id
            )
        )
        existing: dict[str, tuple[str, str]] = {row[1]: (row[0], row[2]) for row in existing_rows}
        max_ordinal = await self.max_ordinal(feed_id)

        new_items = [entry for key, entry in items.items() if key not in existing]
        ordinals = assign_ordinals(max_ordinal, new_items, listing.listing_order)

        inserts: list[dict[str, Any]] = []
        new_ids: list[str] = []
        wanted_ids: list[str] = []
        for entry in new_items:
            key = entry.source_key.strip()
            iid = make_item_id(feed_id, key)
            new_ids.append(iid)
            wanted = wanted_reason is not None and entry.archivable
            if wanted:
                wanted_ids.append(iid)
            inserts.append(
                {
                    "id": iid,
                    "feed_id": feed_id,
                    "source_key": key,
                    "ordinal": ordinals[key],
                    **_metadata_values(entry),
                    "archivable": entry.archivable,
                    "listed": True,
                    "first_seen_at": now,
                    "last_listed_at": now,
                    "archive_state": ArchiveState.wanted.value
                    if wanted
                    else ArchiveState.available.value,
                    "wanted_reason": wanted_reason.value if wanted and wanted_reason else None,
                }
            )
        if inserts:
            await self._session.execute(insert(CatalogItem), inserts)

        updates: list[dict[str, Any]] = []
        seen_ids: list[str] = []
        for key, entry in items.items():
            found = existing.get(key)
            if found is None:
                seen_ids.append(make_item_id(feed_id, key))
                continue
            iid, _state = found
            seen_ids.append(iid)
            updates.append(_refresh_params(iid, entry, now))
        if updates:
            connection = await self._session.connection()
            await connection.execute(_REFRESH_STMT, updates)

        if wanted_reason is not None and updates:
            known_ids = [
                found[0]
                for key, found in existing.items()
                if key in items and items[key].archivable
            ]
            if known_ids:
                result = await self._session.execute(
                    update(CatalogItem)
                    .where(
                        CatalogItem.id.in_(known_ids),
                        CatalogItem.archive_state.in_(RE_WANTABLE),
                    )
                    .values(
                        archive_state=ArchiveState.wanted.value,
                        wanted_reason=wanted_reason.value,
                        last_error=None,
                        deleted_at=None,
                    )
                    .returning(CatalogItem.id)
                )
                wanted_ids.extend(result.scalars())

        delisted = 0
        if items:
            result = await self._session.execute(
                update(CatalogItem)
                .where(
                    CatalogItem.feed_id == feed_id,
                    CatalogItem.listed.is_(True),
                    CatalogItem.last_listed_at < now,
                )
                .values(listed=False)
            )
            delisted = rows_affected(result)

        await refresh_loaded(self._session, CatalogItem, lambda row: row.feed_id == feed_id)
        return ListingUpsert(
            listed_count=len(items),
            new_count=len(new_ids),
            delisted_count=delisted,
            new_item_ids=new_ids,
            seen_item_ids=seen_ids,
            wanted_item_ids=wanted_ids,
        )

    async def set_wanted(self, item_ids: Iterable[str], reason: WantedReason) -> list[str]:
        """Available, deleted or failed archivable items -> wanted; returns the ids changed."""
        ids = list(dict.fromkeys(item_ids))
        if not ids:
            return []
        result = await self._session.execute(
            update(CatalogItem)
            .where(
                CatalogItem.id.in_(ids),
                CatalogItem.archive_state.in_(RE_WANTABLE),
                CatalogItem.archivable.is_(True),
            )
            .values(
                archive_state=ArchiveState.wanted.value,
                wanted_reason=reason.value,
                last_error=None,
                deleted_at=None,
            )
            .returning(CatalogItem.id)
        )
        changed = list(result.scalars())
        changed_set = set(changed)
        await refresh_loaded(self._session, CatalogItem, lambda row: row.id in changed_set)
        return changed

    async def mark_state(
        self,
        item_id: str,
        state: ArchiveState,
        *,
        only_from: ArchiveState | Sequence[ArchiveState] | None = None,
        error: str | None = None,
        count_attempt: bool = False,
        media_path: str | None = None,
        media_mime: str | None = None,
        media_bytes: int | None = None,
        duration_seconds: int | None = None,
        archived_at: datetime | None = None,
        wanted_reason: WantedReason | None = None,
    ) -> bool:
        """Move an item to ``state``; returns False when ``only_from`` did not match.

        ``archived`` requires the media columns; ``failed``/``wanted`` may carry
        ``error`` and ``count_attempt``; ``deleted`` is :meth:`tombstone`'s job.
        """
        values: dict[str, Any] = {"archive_state": state.value}
        if count_attempt:
            values["attempt_count"] = CatalogItem.attempt_count + 1
        if state is ArchiveState.archived:
            if media_path is None or media_mime is None or media_bytes is None:
                raise ValueError("archived needs media_path, media_mime and media_bytes")
            values.update(
                media_path=media_path,
                media_mime=media_mime,
                media_bytes=media_bytes,
                archived_at=archived_at or utcnow(),
                last_error=None,
                deleted_at=None,
            )
            if duration_seconds is not None:
                values["duration_seconds"] = duration_seconds
        elif state in (ArchiveState.failed, ArchiveState.wanted, ArchiveState.archiving):
            values["last_error"] = error
        elif state is ArchiveState.available:
            values.update(last_error=error, wanted_reason=None)
        if wanted_reason is not None:
            values["wanted_reason"] = wanted_reason.value
        stmt = update(CatalogItem).where(CatalogItem.id == item_id).values(**values)
        if only_from is not None:
            froms = [only_from] if isinstance(only_from, ArchiveState) else list(only_from)
            stmt = stmt.where(CatalogItem.archive_state.in_([s.value for s in froms]))
        result = await self._session.execute(stmt.returning(CatalogItem.id))
        changed = result.scalar_one_or_none() is not None
        await self._refresh_if_loaded(item_id)
        return changed

    async def tombstone(self, item_id: str, *, listed: bool, at: datetime | None = None) -> bool:
        """Deleted by the user or a prune: media columns cleared, row kept, never re-archived
        automatically. Inbox rows also drop ``listed`` so they hide."""
        result = await self._session.execute(
            update(CatalogItem)
            .where(CatalogItem.id == item_id)
            .values(
                archive_state=ArchiveState.deleted.value,
                deleted_at=at or utcnow(),
                listed=listed,
                wanted_reason=None,
                media_path=None,
                media_mime=None,
                media_bytes=None,
                archived_at=None,
                last_error=None,
            )
            .returning(CatalogItem.id)
        )
        changed = result.scalar_one_or_none() is not None
        await self._refresh_if_loaded(item_id)
        return changed

    async def record_download(self, item_id: str, at: datetime | None = None) -> bool:
        """Count one download of an archived item; the byte-0 rule is the caller's."""
        at = at or utcnow()
        result = await self._session.execute(
            update(CatalogItem)
            .where(
                CatalogItem.id == item_id,
                CatalogItem.archive_state == ArchiveState.archived.value,
            )
            .values(
                download_count=CatalogItem.download_count + 1,
                first_downloaded_at=func.coalesce(CatalogItem.first_downloaded_at, at),
                last_downloaded_at=at,
            )
            .returning(CatalogItem.id)
        )
        counted = result.scalar_one_or_none() is not None
        await self._refresh_if_loaded(item_id)
        return counted

    async def fill_metadata(
        self,
        item_id: str,
        *,
        description: str | None = None,
        published_at: datetime | None = None,
        author: str | None = None,
        artwork_url: str | None = None,
    ) -> bool:
        """Fill columns the listing left empty from the engine's full info (never overwrite)."""
        values: dict[str, Any] = {}
        if description:
            values["description"] = func.coalesce(CatalogItem.description, description)
        if published_at is not None:
            values["published_at"] = func.coalesce(CatalogItem.published_at, published_at)
        if author:
            values["author"] = func.coalesce(CatalogItem.author, author)
        if artwork_url:
            values["artwork_url"] = func.coalesce(CatalogItem.artwork_url, artwork_url)
        if not values:
            return False
        result = await self._session.execute(
            update(CatalogItem).where(CatalogItem.id == item_id).values(**values)
        )
        await self._refresh_if_loaded(item_id)
        return rows_affected(result) > 0

    async def set_source_item_xml(self, item_id: str, xml: str | None) -> None:
        await self._session.execute(
            update(CatalogItem).where(CatalogItem.id == item_id).values(source_item_xml=xml)
        )
        await self._refresh_if_loaded(item_id)

    async def _refresh_if_loaded(self, item_id: str) -> None:
        instance = self._session.identity_map.get((CatalogItem, (item_id,), None))
        if instance is not None:
            await self._session.refresh(instance)


def _metadata_values(entry: SourceListingItem) -> dict[str, Any]:
    """Column values for a new row from a listing item."""
    return {
        "title": _title(entry),
        "description": entry.description,
        "author": entry.author,
        "artwork_url": entry.artwork_url,
        "published_at": entry.published_at,
        "duration_seconds": entry.duration_seconds,
        "source_url": entry.source_url,
        "source_number": entry.source_number,
        "source_season": entry.source_season,
        "source_position": entry.position,
        "tab": entry.tab,
    }


def _title(entry: SourceListingItem) -> str:
    return entry.title.strip() if entry.title and entry.title.strip() else entry.source_key.strip()


def _refresh_params(item_id: str, entry: SourceListingItem, now: datetime) -> dict[str, Any]:
    """Parameters of :data:`_REFRESH_STMT` for one known key."""
    return {
        "b_id": item_id,
        "b_title": entry.title.strip() if entry.title and entry.title.strip() else None,
        "b_description": entry.description,
        "b_author": entry.author,
        "b_artwork_url": entry.artwork_url,
        "b_published_at": entry.published_at,
        "b_duration_seconds": entry.duration_seconds,
        "b_source_url": entry.source_url,
        "b_source_number": entry.source_number,
        "b_source_season": entry.source_season,
        "b_source_position": entry.position,
        "b_tab": entry.tab,
        "b_archivable": entry.archivable,
        "b_now": now,
    }


_T: Table = CatalogItem.__table__  # type: ignore[assignment]
_REFRESH_STMT: Update = (
    _T.update()
    .where(_T.c.id == bindparam("b_id", type_=String))
    .values(
        # Non-blank overwrite: a missing value keeps what the Catalog already knows.
        title=func.coalesce(bindparam("b_title", type_=Text), _T.c.title),
        description=func.coalesce(bindparam("b_description", type_=Text), _T.c.description),
        author=func.coalesce(bindparam("b_author", type_=Text), _T.c.author),
        artwork_url=func.coalesce(bindparam("b_artwork_url", type_=Text), _T.c.artwork_url),
        published_at=func.coalesce(
            bindparam("b_published_at", type_=TZDateTime), _T.c.published_at
        ),
        duration_seconds=func.coalesce(
            bindparam("b_duration_seconds", type_=Integer), _T.c.duration_seconds
        ),
        source_url=func.coalesce(bindparam("b_source_url", type_=Text), _T.c.source_url),
        # Source numbering and position follow the listing as served.
        source_number=bindparam("b_source_number", type_=Integer),
        source_season=bindparam("b_source_season", type_=Integer),
        source_position=bindparam("b_source_position", type_=Integer),
        tab=bindparam("b_tab", type_=Text),
        archivable=bindparam("b_archivable", type_=Boolean),
        listed=True,
        last_listed_at=bindparam("b_now", type_=TZDateTime),
    )
)


__all__ = ["CatalogRepository", "ItemSort", "ListingUpsert"]
