"""``delete_episode`` against in-memory ports: what it touches, in which order, and when."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest

from copycast.application.services.episodes import DeletedEpisode, delete_episode
from copycast.domain.enums import ArchiveState, DeleteReason, FeedKind
from copycast.domain.exceptions import NotFound


@dataclass
class Item:
    id: str
    feed_id: str
    archive_state: str
    media_path: str | None
    media_bytes: int | None


@dataclass
class FeedRow:
    id: str
    kind: str


@dataclass
class AssetRow:
    id: str
    local_path: str | None
    size_bytes: int | None


@dataclass
class FakeCatalog:
    items: dict[str, Item]
    tombstoned: list[tuple[str, bool]] = field(default_factory=list[tuple[str, bool]])

    async def get(self, item_id: str) -> Item | None:
        return self.items.get(item_id)

    async def tombstone(self, item_id: str, *, listed: bool, at: datetime | None = None) -> bool:
        self.tombstoned.append((item_id, listed))
        return item_id in self.items


@dataclass
class FakeFeeds:
    feeds: dict[str, FeedRow]
    recounted: list[str] = field(default_factory=list[str])

    async def get(self, feed_id: str) -> FeedRow | None:
        return self.feeds.get(feed_id)

    async def recount_storage(self, feed_id: str) -> int:
        self.recounted.append(feed_id)
        return 0


@dataclass
class FakeAssets:
    by_item: dict[str, list[AssetRow]]

    async def delete_for_item(self, item_id: str) -> Sequence[AssetRow]:
        return self.by_item.pop(item_id, [])


@dataclass
class FakeStore:
    objects: set[str] = field(default_factory=set[str])
    deletes: int = 0

    def delete(self, keys: str | Iterable[str]) -> None:
        self.deletes += 1
        for key in [keys] if isinstance(keys, str) else list(keys):
            self.objects.discard(key)


@dataclass
class FakeLayout:
    store: FakeStore = field(default_factory=FakeStore)

    def resolve(self, feed_id: str, relative: str) -> str:
        return f"feeds/{feed_id}/{relative}"

    def info_json_key(self, feed_id: str, item_id: str) -> str:
        return f"feeds/{feed_id}/media/{item_id}.info.json"


@dataclass
class FakeUow:
    catalog: FakeCatalog
    feeds: FakeFeeds
    assets: FakeAssets
    layout: FakeLayout
    intents: list[str] = field(default_factory=list[str])
    hooks: list[Callable[[], Any]] = field(default_factory=list[Callable[[], Any]])

    async def update_intent(self, feed_id: str, *, debounce: bool = False) -> tuple[int, int]:
        self.intents.append(feed_id)
        return (len(self.intents) + 1, len(self.intents) + 1)

    def after_commit(self, hook: Callable[[], Any]) -> None:
        self.hooks.append(hook)

    async def commit(self) -> None:
        for hook in self.hooks:
            outcome = hook()
            if inspect.isawaitable(outcome):
                await outcome


def _uow(kind: FeedKind, archived: bool = True) -> tuple[FakeUow, dict[str, str]]:
    files = {
        "media": "feeds/f1/media/i1.m4a",
        "info": "feeds/f1/media/i1.info.json",
        "xml": "feeds/f1/media/i1.item.xml",
        "art": "feeds/f1/assets/i1.artwork.jpg",
    }
    item = Item(
        "i1",
        "f1",
        ArchiveState.archived.value if archived else ArchiveState.available.value,
        "media/i1.m4a" if archived else None,
        1234 if archived else None,
    )
    uow = FakeUow(
        catalog=FakeCatalog({"i1": item}),
        feeds=FakeFeeds({"f1": FeedRow("f1", kind.value)}),
        assets=FakeAssets(
            {"i1": [AssetRow("a1", "assets/i1.artwork.jpg", 10), AssetRow("a2", None, None)]}
        ),
        layout=FakeLayout(FakeStore(set(files.values()))),
    )
    return uow, files


async def test_mirror_deletion_tombstones_listed_and_deletes_after_commit() -> None:
    uow, files = _uow(FeedKind.mirror)
    result = await delete_episode(uow, "i1")
    assert isinstance(result, DeletedEpisode)
    assert result.reason is DeleteReason.user
    assert result.was_archived is True
    assert result.bytes_freed == 1244
    assert (result.intent_version, result.revision) == (2, 2)
    assert set(result.files) == {files["media"], files["info"], files["art"]}
    assert uow.catalog.tombstoned == [("i1", True)]
    assert uow.feeds.recounted == ["f1"] and uow.intents == ["f1"]
    stored = uow.layout.store.objects
    assert stored == set(files.values())  # nothing touched before commit
    await uow.commit()
    assert stored == {files["xml"]}
    assert uow.layout.store.deletes == 1
    await uow.commit()  # deleting again is harmless


async def test_bucket_deletion_hides_the_row() -> None:
    uow, _ = _uow(FeedKind.bucket)
    result = await delete_episode(uow, "i1", DeleteReason.prune)
    assert result.reason is DeleteReason.prune
    assert uow.catalog.tombstoned == [("i1", False)]


async def test_never_archived_item_is_still_tombstoned() -> None:
    uow, files = _uow(FeedKind.mirror, archived=False)
    result = await delete_episode(uow, "i1")
    assert result.was_archived is False and result.bytes_freed == 10
    assert files["media"] not in result.files
    await uow.commit()
    assert files["media"] in uow.layout.store.objects  # the row never pointed at it


async def test_not_found() -> None:
    uow, _ = _uow(FeedKind.mirror)
    with pytest.raises(NotFound, match="item"):
        await delete_episode(uow, "missing")
    uow.feeds.feeds.clear()
    with pytest.raises(NotFound, match="feed"):
        await delete_episode(uow, "i1")
