"""Classifying and ordering a Feed's objects (pure; the Layout over a memory store)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from copycast.application.ports import StoredObject
from copycast.application.services.storage import KIND_ORDER, classify, feed_object_reads

FEED = "0123456789abcdef0123456789abcdef"
ITEM = "fedcba9876543210"
NOW = datetime(2026, 10, 29, tzinfo=UTC)


class Urls:
    def feed_object_url(self, feed_id: str, path: str) -> str:
        return f"http://t/api/feeds/{feed_id}/objects/{path}"


def obj(layout: Any, path: str, size: int = 1) -> StoredObject:
    return StoredObject(key=layout.feed_prefix(FEED) + path, size=size, last_modified=NOW)


def test_classify_every_kind(layout: Any) -> None:
    assert classify(layout, FEED, f"media/{ITEM}.m4a") == ("media", ITEM)
    assert classify(layout, FEED, f"media/{ITEM}.info.json") == ("sidecar", ITEM)
    assert classify(layout, FEED, f"media/{ITEM}.item.xml") == ("sidecar", ITEM)
    assert classify(layout, FEED, f"media/{ITEM}.m4a.part") == ("sidecar", ITEM)
    assert classify(layout, FEED, f"media/{ITEM}.m4a.ytdl") == ("sidecar", ITEM)
    assert classify(layout, FEED, f"media/{ITEM}.m4a.tmp") == ("sidecar", ITEM)
    assert classify(layout, FEED, "media/stray.info.json") == ("sidecar", None)
    assert classify(layout, FEED, "media/stray.txt") == ("other", None)
    assert classify(layout, FEED, "assets/feed.artwork.jpg") == ("artwork", None)
    assert classify(layout, FEED, f"assets/{ITEM}.artwork.png") == ("artwork", ITEM)
    assert classify(layout, FEED, f"assets/{ITEM}.chapters.json") == ("chapters", ITEM)
    assert classify(layout, FEED, f"assets/{ITEM}.transcript.en.mirrored.vtt") == (
        "transcript",
        ITEM,
    )
    assert classify(layout, FEED, f"assets/{ITEM}.attachment.0123456789ab.pdf") == (
        "attachment",
        ITEM,
    )
    assert classify(layout, FEED, "assets/notes.txt") == ("other", None)
    assert classify(layout, FEED, "feed.json") == ("descriptor", None)
    assert classify(layout, FEED, "source/feed.xml") == ("source", None)
    assert classify(layout, FEED, "source/listing.json") == ("source", None)
    assert classify(layout, FEED, "README") == ("other", None)


def test_reads_are_titled_sorted_by_kind_then_name_and_linked(layout: Any) -> None:
    objects = [
        obj(layout, "source/feed.xml", 5),
        obj(layout, f"media/{ITEM}.info.json", 2),
        obj(layout, "feed.json", 3),
        obj(layout, f"assets/{ITEM}.chapters.json", 4),
        obj(layout, f"media/{ITEM}.m4a", 100),
        obj(layout, "assets/feed.artwork.jpg", 6),
        obj(layout, "media/0000000000000000.m4a", 50),
    ]
    reads = feed_object_reads(layout, Urls(), FEED, objects, {ITEM: "Episode 1"})
    assert [read.kind for read in reads] == [
        "media",
        "media",
        "artwork",
        "chapters",
        "descriptor",
        "source",
        "sidecar",
    ]
    assert [read.name for read in reads][:2] == ["0000000000000000.m4a", f"{ITEM}.m4a"]
    media = reads[1]
    assert media.item_id == ITEM and media.item_title == "Episode 1"
    assert media.path == f"media/{ITEM}.m4a" and media.size_bytes == 100
    assert media.url == f"http://t/api/feeds/{FEED}/objects/media/{ITEM}.m4a"
    assert reads[0].item_title is None  # no Catalog row for that id
    assert reads[3].item_id == ITEM and reads[2].item_id is None
    assert set(KIND_ORDER) >= {read.kind for read in reads}


def test_an_episodes_files_carry_its_lock_and_the_feeds_own_none(layout: Any) -> None:
    objects = [
        obj(layout, f"media/{ITEM}.m4a"),
        obj(layout, f"media/{ITEM}.info.json"),
        obj(layout, f"assets/{ITEM}.chapters.json"),
        obj(layout, f"media/{ITEM}.item.xml"),
        obj(layout, f"media/{ITEM}.m4a.part"),
        obj(layout, "media/0000000000000000.m4a"),
        obj(layout, "assets/feed.artwork.jpg"),
        obj(layout, "feed.json"),
    ]
    reads = feed_object_reads(layout, Urls(), FEED, objects, {ITEM: "Episode 1"}, {ITEM: True})
    locks = {read.path: read.locked for read in reads}
    assert locks == {
        f"media/{ITEM}.m4a": True,
        f"media/{ITEM}.info.json": True,
        f"assets/{ITEM}.chapters.json": True,
        f"media/{ITEM}.item.xml": None,  # the Catalog's record: a purge keeps it
        f"media/{ITEM}.m4a.part": None,  # a purge never deletes a leftover download
        "media/0000000000000000.m4a": None,  # no Catalog row: nothing to lock
        "assets/feed.artwork.jpg": None,
        "feed.json": None,
    }
    unlocked = feed_object_reads(layout, Urls(), FEED, objects[:1], {}, {ITEM: False})
    assert unlocked[0].locked is False
    assert feed_object_reads(layout, Urls(), FEED, objects[:1], {})[0].locked is None
