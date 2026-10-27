"""Episode deletion: the one code path that removes archived media (user action or prune).

``delete_episode`` unlinks the media file, its ``.info.json``, ``tmp/``
leftovers and the item's assets, keeps ``.item.xml`` (the Source's original
entry), tombstones the row so it is never re-archived automatically, then
recounts storage and bumps the feed's intent (which re-exports ``feed.json``).

The unit of work is described structurally: the application layer never
imports the Postgres or storage adapters.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from copycast.domain.enums import ArchiveState, DeleteReason, FeedKind
from copycast.domain.exceptions import NotFound

# --------------------------------------------------------------------------- ports


class ItemRow(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def feed_id(self) -> str: ...
    @property
    def archive_state(self) -> str: ...
    @property
    def media_path(self) -> str | None: ...
    @property
    def media_bytes(self) -> int | None: ...


class FeedRow(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def kind(self) -> str: ...


class AssetRow(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def local_path(self) -> str | None: ...
    @property
    def size_bytes(self) -> int | None: ...


class CatalogPort(Protocol):
    async def get(self, item_id: str) -> ItemRow | None: ...
    async def tombstone(
        self, item_id: str, *, listed: bool, at: datetime | None = None
    ) -> bool: ...


class FeedPort(Protocol):
    async def get(self, feed_id: str) -> FeedRow | None: ...
    async def recount_storage(self, feed_id: str) -> int: ...


class AssetPort(Protocol):
    async def delete_for_item(self, item_id: str) -> Sequence[AssetRow]: ...


class StorePort(Protocol):
    def delete(self, keys: str | Iterable[str]) -> None: ...


class LayoutPort(Protocol):
    @property
    def store(self) -> StorePort: ...
    def resolve(self, feed_id: str, relative: str) -> str: ...
    def info_json_key(self, feed_id: str, item_id: str) -> str: ...


class EpisodeUnitOfWork(Protocol):
    """The slice of the unit of work :func:`delete_episode` needs."""

    @property
    def catalog(self) -> CatalogPort: ...
    @property
    def feeds(self) -> FeedPort: ...
    @property
    def assets(self) -> AssetPort: ...
    @property
    def layout(self) -> LayoutPort: ...
    async def update_intent(self, feed_id: str, *, debounce: bool = False) -> tuple[int, int]: ...
    def after_commit(self, hook: Callable[[], Any]) -> None: ...


# --------------------------------------------------------------------------- result


@dataclass(frozen=True, slots=True)
class DeletedEpisode:
    item_id: str
    feed_id: str
    reason: DeleteReason
    bytes_freed: int
    was_archived: bool
    files: list[str] = field(default_factory=list[str])
    revision: int = 0
    intent_version: int = 0


# --------------------------------------------------------------------------- service


async def delete_episode(
    uow: EpisodeUnitOfWork,
    item_id: str,
    reason: DeleteReason = DeleteReason.user,
) -> DeletedEpisode:
    """Tombstone one Catalog item and remove its files once the transaction commits.

    Deleting an item that was never archived still tombstones it (a user
    saying "do not archive this"). Raises :class:`NotFound` for unknown ids.
    """
    item = await uow.catalog.get(item_id)
    if item is None:
        raise NotFound("item", item_id)
    feed = await uow.feeds.get(item.feed_id)
    if feed is None:
        raise NotFound("feed", item.feed_id)
    feed_id = feed.id
    was_archived = item.archive_state == ArchiveState.archived

    files: list[str] = []
    bytes_freed = 0
    if item.media_path:
        files.append(uow.layout.resolve(feed_id, item.media_path))
        bytes_freed += item.media_bytes or 0
    files.append(uow.layout.info_json_key(feed_id, item_id))

    for asset in await uow.assets.delete_for_item(item_id):
        if asset.local_path:
            files.append(uow.layout.resolve(feed_id, asset.local_path))
            bytes_freed += asset.size_bytes or 0

    # Bucket deletions hide the row; Mirror tombstones stay visible as Available.
    listed = feed.kind != FeedKind.bucket
    await uow.catalog.tombstone(item_id, listed=listed)
    await uow.feeds.recount_storage(feed_id)
    intent_version, revision = await uow.update_intent(feed_id)

    layout = uow.layout

    async def _delete_objects() -> None:
        await asyncio.to_thread(layout.store.delete, files)

    uow.after_commit(_delete_objects)
    return DeletedEpisode(
        item_id=item_id,
        feed_id=feed_id,
        reason=reason,
        bytes_freed=bytes_freed,
        was_archived=was_archived,
        files=files,
        revision=revision,
        intent_version=intent_version,
    )


__all__ = [
    "AssetPort",
    "AssetRow",
    "CatalogPort",
    "DeletedEpisode",
    "EpisodeUnitOfWork",
    "FeedPort",
    "FeedRow",
    "ItemRow",
    "LayoutPort",
    "delete_episode",
]
