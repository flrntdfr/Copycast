"""Storage: what a Feed keeps under ``feeds/{id}/`` in the Object store (its bucket), one Feed
at a time (the bucket page) or every Feed counted at once (the bucket cards)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Final

from copycast.application.capabilities import capability
from copycast.application.models import (
    FeedObjectKind,
    FeedObjectList,
    FeedObjectRead,
    FeedStorage,
    StorageSummary,
)
from copycast.application.ports import StoredObject
from copycast.application.services.context import LayoutPort, PublicUrls, ServiceContext
from copycast.domain.enums import ArchiveState, AssetKind

KIND_ORDER: Final[tuple[FeedObjectKind, ...]] = (
    "media",
    "artwork",
    "chapters",
    "transcript",
    "attachment",
    "descriptor",
    "source",
    "sidecar",
    "other",
)
"""The listing order of :class:`FeedObjectList` (then by name inside each kind)."""

SIDECAR_SUFFIXES: Final = (".info.json", ".item.xml", ".part", ".ytdl", ".tmp")
"""The worker's bookkeeping next to the media (``adapters.storage.layout._SIDECAR_SUFFIXES``)."""
_KIND_RANK: Final = {kind: rank for rank, kind in enumerate(KIND_ORDER)}
_ASSET_KINDS: Final[dict[AssetKind, FeedObjectKind]] = {
    AssetKind.artwork: "artwork",
    AssetKind.chapters: "chapters",
    AssetKind.transcript: "transcript",
    AssetKind.attachment: "attachment",
}


def classify(layout: LayoutPort, feed_id: str, path: str) -> tuple[FeedObjectKind, str | None]:
    """The kind of the object at ``path`` (relative to the feed prefix) and the item it names."""
    name = path.rsplit("/", 1)[-1]
    if path.startswith("media/"):
        item_id = layout.media_stem(name)
        if item_id is not None:
            return "media", item_id
        if name.endswith(SIDECAR_SUFFIXES):
            # ``{item}.info.json``, ``{item}.m4a.part``: the stem names the Episode they serve.
            stem = name.partition(".")[0]
            return "sidecar", stem if layout.media_stem(f"{stem}.m4a") == stem else None
        return "other", None
    if path.startswith("assets/"):
        parsed = layout.parse_asset_name(name)
        if parsed is None:
            return "other", None
        return _ASSET_KINDS.get(parsed.kind, "other"), parsed.item_id
    if path == layout.descriptor_key(feed_id).removeprefix(layout.feed_prefix(feed_id)):
        return "descriptor", None
    if path.startswith("source/"):
        return "source", None
    return "other", None


PURGED_KINDS: Final[frozenset[FeedObjectKind]] = frozenset(
    {"media", "artwork", "chapters", "transcript", "attachment"}
)
"""What deleting an Episode removes, besides its ``.info.json`` sidecar (``delete_episode``)."""


def purged_with_episode(kind: FeedObjectKind, name: str) -> bool:
    """Whether deleting (or purging) the Episode a file belongs to removes the file."""
    return kind in PURGED_KINDS or (kind == "sidecar" and name.endswith(".info.json"))


def feed_object_reads(
    layout: LayoutPort,
    urls: PublicUrls,
    feed_id: str,
    objects: Sequence[StoredObject],
    titles: Mapping[str, str],
    locks: Mapping[str, bool] | None = None,
) -> list[FeedObjectRead]:
    """Classify, title and sort the objects of one Feed (pure; ``titles`` maps item id -> title,
    ``locks`` the archived Episodes' ids -> Lock). Only a file a Purge would delete carries a
    Lock: the feed's own files, ``.item.xml`` records, leftovers of unarchived items and stray
    files have none."""
    locks = locks or {}
    prefix = layout.feed_prefix(feed_id)
    reads: list[FeedObjectRead] = []
    for obj in objects:
        path = obj.key.removeprefix(prefix)
        kind, item_id = classify(layout, feed_id, path)
        reads.append(
            FeedObjectRead(
                path=path,
                name=obj.name,
                kind=kind,
                size_bytes=obj.size,
                last_modified=obj.last_modified,
                etag=obj.etag,
                url=urls.feed_object_url(feed_id, path),
                item_id=item_id,
                item_title=titles.get(item_id) if item_id is not None else None,
                locked=(
                    locks.get(item_id)
                    if item_id is not None and purged_with_episode(kind, obj.name)
                    else None
                ),
            )
        )
    reads.sort(key=lambda read: (_KIND_RANK[read.kind], read.name))
    return reads


@capability("list_feed_objects", response=FeedObjectList)
async def list_feed_objects(ctx: ServiceContext, feed_id: str) -> FeedObjectList:
    """Every object under ``feeds/{feed_id}/`` with its kind, size and download URL; 404 when
    the Feed is unknown. An Episode's files carry its Catalog id, title and Lock."""
    layout = ctx.layout
    async with ctx.uow_factory() as uow:
        feed_id = (await uow.feeds.require(feed_id)).id
    # The listing can take several round trips to the store: no connection is held meanwhile.
    prefix = layout.feed_prefix(feed_id)
    objects = await asyncio.to_thread(layout.store.list, prefix)
    item_ids = {
        item_id
        for obj in objects
        for _, item_id in (classify(layout, feed_id, obj.key.removeprefix(prefix)),)
        if item_id is not None
    }
    titles: dict[str, str] = {}
    locks: dict[str, bool] = {}
    if item_ids:
        async with ctx.uow_factory() as uow:
            for row in await uow.catalog.get_many(item_ids):
                titles[row.id] = row.title
                # A Purge deletes archived Episodes only: the others' files show no Lock.
                if row.archive_state == ArchiveState.archived:
                    locks[row.id] = row.archive_flag
    reads = feed_object_reads(layout, ctx.urls, feed_id, objects, titles, locks)
    return FeedObjectList(
        feed_id=feed_id,
        objects=reads,
        total_bytes=sum(read.size_bytes for read in reads),
        count=len(reads),
    )


@capability("storage_summary", response=StorageSummary)
async def storage_summary(ctx: ServiceContext) -> StorageSummary:
    """How many files every Feed keeps in its bucket and their total size, from one listing
    of ``feeds/``; a Feed that keeps nothing counts zeros, objects of unknown Feeds are left
    out."""
    layout = ctx.layout
    async with ctx.uow_factory() as uow:
        feed_ids = [feed.id for feed in await uow.feeds.list()]
    # One listing of every Feed's objects; no connection is held meanwhile.
    root = layout.feeds_prefix()
    objects = await asyncio.to_thread(layout.store.list, root)
    counts = dict.fromkeys(feed_ids, 0)
    sizes = dict.fromkeys(feed_ids, 0)
    for obj in objects:
        feed_id = obj.key.removeprefix(root).split("/", 1)[0]
        if feed_id in counts:
            counts[feed_id] += 1
            sizes[feed_id] += obj.size
    return StorageSummary(
        feeds=[
            FeedStorage(feed_id=feed_id, file_count=counts[feed_id], total_bytes=sizes[feed_id])
            for feed_id in feed_ids
        ],
        file_count=sum(counts.values()),
        total_bytes=sum(sizes.values()),
    )


__all__ = [
    "KIND_ORDER",
    "PURGED_KINDS",
    "SIDECAR_SUFFIXES",
    "classify",
    "feed_object_reads",
    "list_feed_objects",
    "purged_with_episode",
    "storage_summary",
]
