"""``delete_episode`` against in-memory ports: what it touches, in which order, and when."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
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
class FakeLayout:
    root: Path
    tmp_removed: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    def resolve(self, feed_id: str, relative: str) -> Path:
        return self.root / feed_id / relative

    def info_json_path(self, feed_id: str, item_id: str) -> Path:
        return self.root / feed_id / "media" / f"{item_id}.info.json"

    def remove_tmp_leftovers(self, feed_id: str, item_id: str) -> int:
        self.tmp_removed.append((feed_id, item_id))
        return 0


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

    def commit(self) -> None:
        for hook in self.hooks:
            hook()


def _uow(tmp_path: Path, kind: FeedKind, archived: bool = True) -> tuple[FakeUow, dict[str, Path]]:
    feed_dir = tmp_path / "f1"
    files = {
        "media": feed_dir / "media" / "i1.m4a",
        "info": feed_dir / "media" / "i1.info.json",
        "xml": feed_dir / "media" / "i1.item.xml",
        "art": feed_dir / "assets" / "i1.artwork.jpg",
    }
    for path in files.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
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
        layout=FakeLayout(tmp_path),
    )
    return uow, files


async def test_mirror_deletion_tombstones_listed_and_unlinks_after_commit(tmp_path: Path) -> None:
    uow, files = _uow(tmp_path, FeedKind.mirror)
    result = await delete_episode(uow, "i1")
    assert isinstance(result, DeletedEpisode)
    assert result.reason is DeleteReason.user
    assert result.was_archived is True
    assert result.bytes_freed == 1244
    assert (result.intent_version, result.revision) == (2, 2)
    assert set(result.files) == {files["media"], files["info"], files["art"]}
    assert uow.catalog.tombstoned == [("i1", True)]
    assert uow.feeds.recounted == ["f1"] and uow.intents == ["f1"]
    assert all(path.exists() for path in files.values())  # nothing touched before commit
    uow.commit()
    assert not files["media"].exists() and not files["info"].exists() and not files["art"].exists()
    assert files["xml"].exists()
    assert uow.layout.tmp_removed == [("f1", "i1")]
    uow.commit()  # unlinking again is harmless


async def test_inbox_deletion_hides_the_row(tmp_path: Path) -> None:
    uow, _ = _uow(tmp_path, FeedKind.inbox)
    result = await delete_episode(uow, "i1", DeleteReason.prune)
    assert result.reason is DeleteReason.prune
    assert uow.catalog.tombstoned == [("i1", False)]


async def test_never_archived_item_is_still_tombstoned(tmp_path: Path) -> None:
    uow, files = _uow(tmp_path, FeedKind.mirror, archived=False)
    result = await delete_episode(uow, "i1")
    assert result.was_archived is False and result.bytes_freed == 10
    assert files["media"] not in result.files
    uow.commit()
    assert files["media"].exists()  # not ours to remove: the row never pointed at it


async def test_not_found(tmp_path: Path) -> None:
    uow, _ = _uow(tmp_path, FeedKind.mirror)
    with pytest.raises(NotFound, match="item"):
        await delete_episode(uow, "missing")
    uow.feeds.feeds.clear()
    with pytest.raises(NotFound, match="feed"):
        await delete_episode(uow, "i1")
