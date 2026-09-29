from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from copycast.adapters.sources.listing import (
    best_thumbnail,
    entry_date,
    flatten,
    is_playlist_like,
    live_status_of,
    normalize,
    service_name,
    source_key_of,
)
from copycast.domain.enums import ListingOrder, LiveStatus


def _load(fixtures_dir: Path, name: str) -> dict[str, Any]:
    return json.loads((fixtures_dir / "ytdlp" / name).read_text())


def test_channel_tabs_are_newest_first_without_source_numbers(fixtures_dir: Path) -> None:
    info = _load(fixtures_dir, "channel_tabs.json")
    listing = normalize(info, "https://www.youtube.com/@fixturechannel/videos")
    assert listing.service == "YouTube"
    assert listing.extractor_key == "YoutubeTab"
    assert listing.title == "Fixture Channel - Videos"
    assert listing.author == "Fixture Channel"
    assert listing.artwork_url == "https://yt3.example/avatar=s900"
    assert listing.webpage_url == "https://www.youtube.com/@fixturechannel/videos"
    assert listing.listing_order is ListingOrder.newest_first
    assert listing.raw == info
    keys = listing.source_keys
    assert keys == [
        "Youtube:vid00000004",
        "Youtube:vid00000003",
        "Youtube:vid00000002",
        "Youtube:vid00000001",
    ]
    four, three, two, one = listing.items
    assert [i.position for i in listing.items] == [0, 1, 2, 3]
    assert all(i.source_number is None for i in listing.items)
    assert all(i.tab == "videos" for i in listing.items)
    assert four.published_at == datetime.fromtimestamp(1710000000, tz=UTC)
    assert three.published_at == datetime.fromtimestamp(1709500000, tz=UTC)  # release_timestamp
    assert two.published_at == datetime(2024, 2, 15, tzinfo=UTC)  # upload_date only
    assert one.duration_seconds == 3599
    assert four.artwork_url == "https://i.ytimg.example/vi/vid00000004/hqdefault.jpg"
    assert two.artwork_url is None
    assert four.source_url == "https://www.youtube.com/watch?v=vid00000004"
    assert four.author == "Fixture Channel"
    assert three.description is None
    assert four.enclosure_url is None and four.archivable is True
    # The fixture's third video was streamed: recorded, so archivable; the rest say nothing.
    assert three.live_status is LiveStatus.was_live and three.archivable is True
    assert four.live_status is None and one.live_status is None


def _stream(ident: str, live_status: object) -> dict[str, Any]:
    return {
        "_type": "url",
        "ie_key": "Youtube",
        "id": ident,
        "url": f"https://www.youtube.com/watch?v={ident}",
        "title": f"Stream {ident}",
        "live_status": live_status,
    }


def test_upcoming_and_live_streams_are_listed_but_not_archivable() -> None:
    """A flat tab listing flags streams; Copycast lists them and waits for the recording."""
    info = {
        "_type": "playlist",
        "extractor": "youtube:tab",
        "extractor_key": "YoutubeTab",
        "id": "UCstreams",
        "title": "Streams",
        "entries": [
            _stream("up", "is_upcoming"),
            _stream("live", "is_live"),
            _stream("ended", "post_live"),
            _stream("done", "was_live"),
            _stream("plain", "not_live"),
            _stream("none", None),
            _stream("odd", "premiering"),  # a value this build does not know
        ],
    }
    listing = normalize(info, "https://www.youtube.com/@streams/streams")
    by_id = {item.source_key.split(":", 1)[1]: item for item in listing.items}
    assert by_id["up"].live_status is LiveStatus.is_upcoming and not by_id["up"].archivable
    assert by_id["live"].live_status is LiveStatus.is_live and not by_id["live"].archivable
    assert by_id["ended"].live_status is LiveStatus.post_live and by_id["ended"].archivable
    assert by_id["done"].live_status is LiveStatus.was_live and by_id["done"].archivable
    assert by_id["plain"].live_status is LiveStatus.not_live and by_id["plain"].archivable
    assert by_id["none"].live_status is None and by_id["none"].archivable
    assert by_id["odd"].live_status is None and by_id["odd"].archivable
    assert all(item.tab == "streams" for item in listing.items)


def test_live_status_of_reads_only_known_strings() -> None:
    assert live_status_of({"live_status": "was_live"}) is LiveStatus.was_live
    assert live_status_of({"live_status": None}) is None
    assert live_status_of({}) is None
    assert live_status_of({"live_status": 1}) is None
    assert live_status_of({"live_status": "later"}) is None


def test_playlist_is_oldest_first_with_playlist_index_as_source_number(fixtures_dir: Path) -> None:
    info = _load(fixtures_dir, "playlist_flat.json")
    listing = normalize(
        info, "https://www.youtube.com/playlist?list=PLfixture0123456789abcdefghijklmnop"
    )
    assert listing.listing_order is ListingOrder.oldest_first
    assert listing.title == "Fixture Playlist"
    assert [i.source_number for i in listing.items] == [1, 2, 3]
    assert listing.source_keys == [
        "Youtube:vid00000010",
        "Youtube:vid00000011",
        "Youtube:vid00000012",
    ]
    assert all(i.tab is None for i in listing.items)


def test_single_video_is_one_leaf(fixtures_dir: Path) -> None:
    info = _load(fixtures_dir, "single_video.json")
    listing = normalize(info, "https://www.youtube.com/watch?v=vid00000042")
    assert listing.service == "YouTube" and listing.extractor_key == "Youtube"
    assert listing.listing_order is ListingOrder.newest_first
    assert len(listing.items) == 1
    item = listing.items[0]
    assert item.source_key == "Youtube:vid00000042"
    assert item.title == "A single fixture video"
    assert item.duration_seconds == 754
    assert item.published_at == datetime.fromtimestamp(1706745600, tz=UTC)
    assert item.artwork_url == "https://i.ytimg.example/vi/vid00000042/maxresdefault.jpg"
    assert listing.language == "en"


def test_nested_playlists_flatten_and_dedupe() -> None:
    info = {
        "_type": "playlist",
        "id": "UCabc",
        "extractor": "youtube:tab",
        "extractor_key": "YoutubeTab",
        "title": "Channel",
        "entries": [
            {
                "_type": "playlist",
                "id": "UUabc",
                "extractor": "youtube:tab",
                "entries": [
                    {
                        "_type": "url",
                        "ie_key": "Youtube",
                        "id": "a",
                        "url": "https://y/a",
                        "title": "A",
                    },
                    {"_type": "url", "ie_key": "Youtube", "id": "b", "url": "https://y/b"},
                ],
            },
            {"_type": "url", "ie_key": "Youtube", "id": "a", "url": "https://y/a", "title": "dup"},
            {"_type": "url", "id": "c", "url": "https://y/c"},
            {"id": None, "url": None},
            "junk",
        ],
    }
    listing = normalize(info, "https://www.youtube.com/@abc")
    assert listing.source_keys == ["Youtube:a", "Youtube:b", "YoutubeTab:c"]
    assert listing.items[1].title == "b"
    assert listing.items[0].title == "A"
    assert [e.get("id") for e in flatten(info)] == ["a", "b", "a", "c", None]


@pytest.mark.parametrize(
    ("extractor", "expected"),
    [
        ("youtube:tab", "YouTube"),
        ("youtube", "YouTube"),
        ("soundcloud:set", "SoundCloud"),
        ("vimeo:showcase", "Vimeo"),
        ("twitch:videos", "Twitch"),
        ("Bandcamp:album", "Bandcamp"),
        ("BiliBili", "BiliBili"),
        ("generic", "Generic"),
        ("copycast:rss", "RSS"),
        ("", "Unknown"),
        (None, "Unknown"),
    ],
)
def test_service_name(extractor: str | None, expected: str) -> None:
    assert service_name(extractor) == expected


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        ({"extractor": "youtube:tab", "id": "PLxyz"}, True),
        ({"extractor": "youtube:tab", "id": "OLAK5uy_x"}, True),
        ({"extractor": "youtube:tab", "id": "RDabc"}, True),
        ({"extractor": "youtube:tab", "id": "UCabc"}, False),
        ({"extractor": "youtube:tab", "id": "@handle"}, False),
        ({"extractor": "soundcloud:set", "id": "x"}, True),
        ({"extractor": "soundcloud:user", "id": "x"}, False),
        ({"extractor": "bandcamp:album", "id": "x"}, True),
        ({"extractor": "vimeo:showcase", "id": "x"}, True),
        ({"extractor": "twitch:playlist", "id": "x"}, True),
        ({"extractor": "generic", "id": "x"}, False),
        ({}, False),
    ],
)
def test_is_playlist_like(info: dict[str, Any], expected: bool) -> None:
    assert is_playlist_like(info) is expected


def test_entry_date_precedence() -> None:
    assert entry_date(
        {"timestamp": 1, "release_timestamp": 2, "upload_date": "20240101"}
    ) == datetime.fromtimestamp(1, tz=UTC)
    assert entry_date({"timestamp": None, "release_timestamp": 2}) == datetime.fromtimestamp(
        2, tz=UTC
    )
    assert entry_date({"upload_date": "20240215"}) == datetime(2024, 2, 15, tzinfo=UTC)
    assert entry_date({"upload_date": "2024-02-15"}) is None
    assert entry_date({"upload_date": "20241399"}) is None
    assert entry_date({"timestamp": True}) is None
    assert entry_date({}) is None


def test_best_thumbnail_and_source_key() -> None:
    entry = {
        "thumbnail": "https://t/single.jpg",
        "thumbnails": [
            {"url": "https://t/small.jpg", "width": 100, "preference": 0},
            {"url": "https://t/big.jpg", "width": 900, "preference": 0},
            {"url": "https://t/banner.jpg", "width": 2000, "preference": -10},
            {"url": ""},
            "junk",
        ],
    }
    assert best_thumbnail(entry) == "https://t/big.jpg"
    assert (
        best_thumbnail({"thumbnail": "https://t/single.jpg", "thumbnails": []})
        == "https://t/single.jpg"
    )
    assert best_thumbnail({}) is None
    assert source_key_of({"ie_key": "Youtube", "id": "x"}) == "Youtube:x"
    assert source_key_of({"id": "x"}, "Parent") == "Parent:x"
    assert source_key_of({"id": "x"}) is None
    assert source_key_of({"ie_key": "Y", "url": "https://u"}) == "Y:https://u"


def test_flat_youtube_tab_dates_are_approximate() -> None:
    from copycast.adapters.sources.listing import is_approximate_date, normalize

    flat = {
        "_type": "url",
        "ie_key": "Youtube",
        "id": "a",
        "title": "A",
        "timestamp": 1_700_000_000,
    }
    exact = {**flat, "upload_date": "20231114"}
    assert is_approximate_date(flat) and not is_approximate_date(exact)
    assert not is_approximate_date({"_type": "video", "extractor_key": "Youtube", "timestamp": 1})
    info = {
        "_type": "playlist",
        "extractor": "youtube:tab",
        "extractor_key": "YoutubeTab",
        "id": "UCabc",
        "entries": [flat, {**exact, "id": "b"}, {**flat, "id": "c", "timestamp": None}],
    }
    items = normalize(info, "https://www.youtube.com/@x/videos").items
    assert [i.published_at_exact for i in items] == [False, True, True]
    assert items[2].published_at is None
