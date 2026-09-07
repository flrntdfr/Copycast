"""Layout: version guard, path helpers, traversal refusal, asset-name parsing, disk helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from copycast.adapters.storage.layout import (
    LAYOUT_VERSION,
    LAYOUT_VERSION_FILE,
    AssetFileName,
    Layout,
    LayoutMismatch,
    UnsafePath,
    is_media_file,
)
from copycast.domain.enums import AssetKind, AssetProvenance

FEED = "0123456789abcdef"
ITEM = "fedcba9876543210"


def test_ensure_version_writes_when_fresh(tmp_path: Path) -> None:
    layout = Layout(tmp_path / "data")
    assert layout.read_version() is None
    assert layout.ensure_version() == LAYOUT_VERSION
    assert (tmp_path / "data" / LAYOUT_VERSION_FILE).read_text() == "1\n"
    assert layout.feeds_dir.is_dir()
    assert layout.ensure_version() == LAYOUT_VERSION  # idempotent


def test_ensure_version_refuses_mismatch_and_unversioned_feeds(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    layout.version_file.write_text("7\n")
    with pytest.raises(LayoutMismatch, match="layout version '7'"):
        layout.ensure_version()
    layout.version_file.unlink()
    (layout.feeds_dir / FEED).mkdir(parents=True)
    with pytest.raises(LayoutMismatch, match="contains feeds but no LAYOUT_VERSION"):
        layout.ensure_version()


def test_paths_follow_the_plan(data_dir: Path) -> None:
    layout = Layout(data_dir)
    base = data_dir / "feeds" / FEED
    assert layout.feed_dir(FEED) == base
    assert layout.descriptor_path(FEED) == base / "feed.json"
    assert layout.source_xml_path(FEED) == base / "source" / "feed.xml"
    assert layout.source_listing_path(FEED) == base / "source" / "listing.json"
    assert layout.media_path(FEED, ITEM, ".m4a") == base / "media" / f"{ITEM}.m4a"
    assert layout.info_json_path(FEED, ITEM) == base / "media" / f"{ITEM}.info.json"
    assert layout.item_xml_path(FEED, ITEM) == base / "media" / f"{ITEM}.item.xml"
    assert layout.tmp_dir(FEED) == base / "tmp"
    assert layout.feed_artwork_path(FEED, "jpg") == base / "assets" / "feed.artwork.jpg"
    assert layout.item_artwork_path(FEED, ITEM, "png") == base / "assets" / f"{ITEM}.artwork.png"
    assert layout.chapters_path(FEED, ITEM) == base / "assets" / f"{ITEM}.chapters.json"
    assert (
        layout.transcript_path(FEED, ITEM, "en", AssetProvenance.generated, "vtt")
        == base / "assets" / f"{ITEM}.transcript.en.generated.vtt"
    )
    assert layout.asset_path(FEED, "artwork", None, "jpg") == layout.feed_artwork_path(FEED, "jpg")
    assert layout.asset_path(FEED, AssetKind.artwork, ITEM, "jpg") == layout.item_artwork_path(
        FEED, ITEM, "jpg"
    )
    assert layout.asset_path(FEED, AssetKind.chapters, ITEM, "json") == layout.chapters_path(
        FEED, ITEM
    )
    assert layout.asset_path(
        FEED, AssetKind.transcript, ITEM, "srt", language="de"
    ) == layout.transcript_path(FEED, ITEM, "de", "mirrored", "srt")


def test_unsafe_inputs_are_refused(data_dir: Path) -> None:
    layout = Layout(data_dir)
    with pytest.raises(UnsafePath):
        layout.feed_dir("../etc")
    with pytest.raises(UnsafePath):
        layout.feed_dir("Upper")
    with pytest.raises(UnsafePath):
        layout.media_path(FEED, "not-an-item-id", "m4a")
    with pytest.raises(UnsafePath):
        layout.media_path(FEED, ITEM, "m4a/../../x")
    with pytest.raises(UnsafePath):
        layout.transcript_path(FEED, ITEM, "en/../x", "mirrored", "vtt")
    with pytest.raises(UnsafePath):
        layout.asset_path(FEED, AssetKind.chapters, None, "json")
    with pytest.raises(UnsafePath):
        layout.asset_path(FEED, AssetKind.transcript, ITEM, "vtt")
    with pytest.raises(UnsafePath):
        layout.resolve(FEED, "../other/feed.json")
    with pytest.raises(UnsafePath):
        layout.resolve(FEED, "/etc/passwd")
    for bad in ("", "../x", "a/b", ".hidden"):
        with pytest.raises(UnsafePath):
            Layout.safe_basename(bad)
    assert Layout.safe_basename("feed.artwork.jpg") == "feed.artwork.jpg"


def test_relative_and_resolve_round_trip(data_dir: Path) -> None:
    layout = Layout(data_dir)
    layout.ensure_feed_dirs(FEED)
    media = layout.media_path(FEED, ITEM, "m4a")
    assert layout.relative(FEED, media) == f"media/{ITEM}.m4a"
    assert layout.resolve(FEED, f"media/{ITEM}.m4a") == media
    with pytest.raises(UnsafePath):
        layout.relative(FEED, data_dir / "feeds" / "other" / "x")


def test_disk_helpers(data_dir: Path) -> None:
    layout = Layout(data_dir)
    assert layout.feed_ids_on_disk() == []
    layout.ensure_feed_dirs(FEED)
    (layout.feeds_dir / "Not-A-Feed").mkdir()
    (layout.feeds_dir / "stray-file").write_text("x")
    assert layout.feed_ids_on_disk() == [FEED]
    assert layout.has_feed(FEED) and not layout.has_feed("aaaaaaaaaaaaaaaa")
    assert layout.find_media(FEED, ITEM) is None
    layout.info_json_path(FEED, ITEM).write_text("{}")
    layout.item_xml_path(FEED, ITEM).write_text("<item/>")
    assert layout.find_media(FEED, ITEM) is None  # sidecars are not media
    media = layout.media_path(FEED, ITEM, "m4a")
    media.write_bytes(b"x")
    (layout.media_dir(FEED) / ".hidden.m4a").write_bytes(b"x")
    (layout.media_dir(FEED) / "notes.txt").write_text("x")
    assert layout.find_media(FEED, ITEM) == media
    assert list(layout.media_files(FEED)) == [media]
    assert Layout.media_stem(media) == ITEM
    assert Layout.media_stem(layout.info_json_path(FEED, ITEM)) is None
    assert Layout.media_stem(Path("zzz.m4a")) is None

    (layout.tmp_dir(FEED) / f"{ITEM}.m4a.part").write_bytes(b"x")
    (layout.tmp_dir(FEED) / f"{ITEM}.m4a.ytdl").write_bytes(b"x")
    (layout.tmp_dir(FEED) / "other.part").write_bytes(b"x")
    assert layout.remove_tmp_leftovers(FEED, ITEM) == 2
    assert layout.remove_tmp_leftovers(FEED, ITEM) == 0
    assert layout.remove_tmp_leftovers("aaaaaaaaaaaaaaaa", ITEM) == 0

    art = layout.item_artwork_path(FEED, ITEM, "jpg")
    art.write_bytes(b"x")
    (layout.assets_dir(FEED) / ".feed.json.abcd.tmp").write_bytes(b"x")
    assert list(layout.asset_files(FEED)) == [art]
    assert list(layout.asset_files("aaaaaaaaaaaaaaaa")) == []
    assert list(layout.media_files("aaaaaaaaaaaaaaaa")) == []
    assert layout.find_media("aaaaaaaaaaaaaaaa", ITEM) is None

    assert layout.remove_feed(FEED) is True
    assert layout.remove_feed(FEED) is False


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
def test_is_media_file(name: str, expected: bool) -> None:
    assert is_media_file(Path(name)) is expected
