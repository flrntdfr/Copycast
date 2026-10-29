"""Storage: what a Feed keeps under ``feeds/{id}/`` in the Object store (the bucket page)."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Final

from copycast.application.capabilities import capability
from copycast.application.models import FeedObjectKind, FeedObjectList, FeedObjectRead
from copycast.application.ports import StoredObject
from copycast.application.services.context import LayoutPort, PublicUrls, ServiceContext
from copycast.domain.enums import AssetKind

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
        return ("sidecar" if name.endswith(SIDECAR_SUFFIXES) else "other"), None
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


def feed_object_reads(
    layout: LayoutPort,
    urls: PublicUrls,
    feed_id: str,
    objects: Sequence[StoredObject],
    titles: dict[str, str],
) -> list[FeedObjectRead]:
    """Classify, title and sort the objects of one Feed (pure; ``titles`` maps item id -> title)."""
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
            )
        )
    reads.sort(key=lambda read: (_KIND_RANK[read.kind], read.name))
    return reads


@capability("list_feed_objects", response=FeedObjectList)
async def list_feed_objects(ctx: ServiceContext, feed_id: str) -> FeedObjectList:
    """Every object under ``feeds/{feed_id}/`` with its kind, size and download URL; 404 when
    the Feed is unknown. Media and asset names carry their Catalog item's id and title."""
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
    if item_ids:
        async with ctx.uow_factory() as uow:
            for row in await uow.catalog.get_many(item_ids):
                titles[row.id] = row.title
    reads = feed_object_reads(layout, ctx.urls, feed_id, objects, titles)
    return FeedObjectList(
        feed_id=feed_id,
        objects=reads,
        total_bytes=sum(read.size_bytes for read in reads),
        count=len(reads),
    )


__all__ = ["KIND_ORDER", "SIDECAR_SUFFIXES", "classify", "feed_object_reads", "list_feed_objects"]
