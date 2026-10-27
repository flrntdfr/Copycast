"""Layout: version guard, key helpers, traversal refusal, asset-name parsing, store helpers."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from copycast.adapters.storage.layout import (
    LAYOUT_VERSION,
    LAYOUT_VERSION_FILE,
    AssetFileName,
    Layout,
    LayoutMismatch,
    UnsafePath,
    is_media_name,
)
from copycast.adapters.storage.objects import ObjectStore, forget_memory_store, open_store
from copycast.domain.enums import AssetKind, AssetProvenance
from copycast.settings import StorageSettings

FEED = "0123456789abcdef"
ITEM = "fedcba9876543210"


@pytest.fixture
def empty() -> Iterator[ObjectStore]:
    yield open_store(StorageSettings(url="memory://layout-empty"))
    forget_memory_store("layout-empty")


def test_ensure_version_writes_when_fresh(empty: ObjectStore) -> None:
    layout = Layout(empty)
    assert layout.read_version() is None
    assert layout.ensure_version() == LAYOUT_VERSION
    assert empty.get_text(LAYOUT_VERSION_FILE) == "2\n"
    assert layout.ensure_version() == LAYOUT_VERSION  # idempotent


def test_ensure_version_refuses_mismatch_and_unversioned_feeds(empty: ObjectStore) -> None:
    layout = Layout(empty)
    empty.put_bytes(LAYOUT_VERSION_FILE, "1\n")
    with pytest.raises(LayoutMismatch, match="layout version '1'"):
        layout.ensure_version()
    empty.delete(LAYOUT_VERSION_FILE)
    empty.put_bytes(layout.descriptor_key(FEED), "{}")
    with pytest.raises(LayoutMismatch, match="contains feeds but no LAYOUT_VERSION"):
        layout.ensure_version()


def test_keys_follow_the_plan(layout: Layout) -> None:
    base = f"feeds/{FEED}/"
    assert layout.feed_prefix(FEED) == base
    assert layout.descriptor_key(FEED) == base + "feed.json"
    assert layout.source_xml_key(FEED) == base + "source/feed.xml"
    assert layout.source_listing_key(FEED) == base + "source/listing.json"
    assert layout.media_key(FEED, ITEM, ".m4a") == base + f"media/{ITEM}.m4a"
    assert layout.info_json_key(FEED, ITEM) == base + f"media/{ITEM}.info.json"
    assert layout.item_xml_key(FEED, ITEM) == base + f"media/{ITEM}.item.xml"
    assert layout.feed_artwork_key(FEED, "jpg") == base + "assets/feed.artwork.jpg"
    assert layout.item_artwork_key(FEED, ITEM, "png") == base + f"assets/{ITEM}.artwork.png"
    assert layout.chapters_key(FEED, ITEM) == base + f"assets/{ITEM}.chapters.json"
    assert (
        layout.transcript_key(FEED, ITEM, "en", AssetProvenance.generated, "vtt")
        == base + f"assets/{ITEM}.transcript.en.generated.vtt"
    )
    assert (
        layout.attachment_key(FEED, ITEM, "0123456789ab", "pdf")
        == base + f"assets/{ITEM}.attachment.0123456789ab.pdf"
    )
    assert layout.asset_key(FEED, "feed.artwork.jpg") == layout.feed_artwork_key(FEED, "jpg")
    assert layout.cookies_key() == "engine/cookies.txt"
    assert layout.defaults_key() == "engine/defaults.json"


def test_unsafe_inputs_are_refused(layout: Layout) -> None:
    with pytest.raises(UnsafePath):
        layout.feed_prefix("../etc")
    with pytest.raises(UnsafePath):
        layout.feed_prefix("Upper")
    with pytest.raises(UnsafePath):
        layout.media_key(FEED, "not-an-item-id", "m4a")
    with pytest.raises(UnsafePath):
        layout.media_key(FEED, ITEM, "m4a/../../x")
    with pytest.raises(UnsafePath):
        layout.transcript_key(FEED, ITEM, "en/../x", "mirrored", "vtt")
    with pytest.raises(UnsafePath):
        layout.attachment_key(FEED, ITEM, "short", "pdf")
    with pytest.raises(UnsafePath):
        layout.resolve(FEED, "../other/feed.json")
    with pytest.raises(UnsafePath):
        layout.resolve(FEED, "/etc/passwd")
    for bad in ("", "../x", "a/b", ".hidden", "..", "a\\\\b"):
        with pytest.raises(UnsafePath):
            Layout.safe_basename(bad)
    assert Layout.safe_basename("feed.artwork.jpg") == "feed.artwork.jpg"


def test_relative_and_resolve_round_trip(layout: Layout) -> None:
    media = layout.media_key(FEED, ITEM, "m4a")
    assert layout.relative(FEED, media) == f"media/{ITEM}.m4a"
    assert layout.resolve(FEED, f"media/{ITEM}.m4a") == media
    with pytest.raises(UnsafePath):
        layout.relative(FEED, f"feeds/aaaaaaaaaaaaaaaa/media/{ITEM}.m4a")
    with pytest.raises(UnsafePath):
        layout.relative(FEED, f"feeds/{FEED}/../aaaaaaaaaaaaaaaa/x")


def test_bucket_helpers(layout: Layout, store: ObjectStore) -> None:
    assert layout.feed_ids() == []
    store.put_bytes(layout.descriptor_key(FEED), "{}")
    store.put_bytes("feeds/Not-A-Feed/feed.json", "{}")
    store.put_bytes("feeds/stray-file", "x")
    assert layout.feed_ids() == [FEED]
    assert layout.has_feed(FEED) and not layout.has_feed("aaaaaaaaaaaaaaaa")
    assert layout.find_media(FEED, ITEM) is None
    store.put_bytes(layout.info_json_key(FEED, ITEM), "{}")
    store.put_bytes(layout.item_xml_key(FEED, ITEM), "<item/>")
    assert layout.find_media(FEED, ITEM) is None  # sidecars are not media
    media = layout.media_key(FEED, ITEM, "m4a")
    store.put_bytes(media, b"x")
    store.put_bytes(layout.media_prefix(FEED) + ".hidden.m4a", b"x")
    store.put_bytes(layout.media_prefix(FEED) + "notes.txt", "x")
    found = layout.find_media(FEED, ITEM)
    assert found is not None and found.key == media and found.size == 1
    assert [o.key for o in layout.media_objects(FEED)] == [media]
    assert Layout.media_stem(f"{ITEM}.m4a") == ITEM
    assert Layout.media_stem(f"{ITEM}.info.json") is None
    assert Layout.media_stem("zzz.m4a") is None

    art = layout.item_artwork_key(FEED, ITEM, "jpg")
    store.put_bytes(art, b"x")
    store.put_bytes(layout.assets_prefix(FEED) + ".feed.json.abcd.tmp", b"x")
    assert [o.key for o in layout.asset_objects(FEED)] == [art]
    assert layout.asset_objects("aaaaaaaaaaaaaaaa") == []
    assert layout.media_objects("aaaaaaaaaaaaaaaa") == []
    assert layout.find_media("aaaaaaaaaaaaaaaa", ITEM) is None

    layout.write_json(layout.defaults_key(), {"language": "fr"})
    assert layout.read_json(layout.defaults_key()) == {"language": "fr"}
    store.put_bytes(layout.defaults_key(), "not json")
    assert layout.read_json(layout.defaults_key()) is None
    assert layout.read_json("engine/absent.json") is None

    assert layout.remove_feed(FEED) is True
    assert layout.remove_feed(FEED) is False
    assert store.exists(layout.defaults_key())


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("feed.artwork.jpg", AssetFileName(AssetKind.artwork, None, "jpg")),
        (f"{ITEM}.artwork.png", AssetFileName(AssetKind.artwork, ITEM, "png")),
        (f"{ITEM}.chapters.json", AssetFileName(AssetKind.chapters, ITEM, "json")),
        (
            f"{ITEM}.transcript.pt-BR.generated.srt",
            AssetFileName(
                AssetKind.transcript,
                ITEM,
                "srt",
                language="pt-BR",
                provenance=AssetProvenance.generated,
            ),
        ),
        (
            f"{ITEM}.attachment.0123456789ab.pdf",
            AssetFileName(AssetKind.attachment, ITEM, "pdf", slot="0123456789ab"),
        ),
        (f"{ITEM}.attachment.short.pdf", None),
        ("README.txt", None),
        (f"{ITEM}.transcript.en.unknown.vtt", None),
        ("short.artwork.jpg", None),
    ],
)
def test_parse_asset_name(name: str, expected: AssetFileName | None) -> None:
    assert Layout.parse_asset_name(name) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (f"{ITEM}.m4a", True),
        (f"{ITEM}.opus", True),
        (f"{ITEM}.info.json", False),
        (f"{ITEM}.item.xml", False),
        (f"{ITEM}.m4a.part", False),
        (f"{ITEM}.m4a.ytdl", False),
        (f".{ITEM}.m4a", False),
        ("readme.m4a", False),
        (ITEM, False),
        (f"{ITEM}.toolongextension", False),
    ],
)
def test_is_media_name(name: str, expected: bool) -> None:
    assert is_media_name(name) is expected
