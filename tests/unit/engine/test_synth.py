from __future__ import annotations

from datetime import UTC, datetime

import pytest

from copycast.adapters.engine.synth import (
    EXTRACTOR,
    EXTRACTOR_KEY,
    build,
    clean_mime,
    from_listing_item,
    media_type,
    mime_for_ext,
    url_ext,
)
from copycast.application.ports import SynthItem
from tests.support.factories import listing_item


def test_build_carries_every_field_the_plan_names() -> None:
    item = SynthItem(
        id="abc",
        title="Episode 5",
        url="https://podcast.example/media/5.mp3?token=x",
        mime="audio/mpeg; charset=binary",
        description="desc",
        timestamp=1705305600,
        duration=1865.0,
        artist="Alex Host",
        album="Copycast Test Podcast",
        episode_number=5,
        season_number=1,
        thumbnail_url="https://podcast.example/artwork/5.jpg",
        webpage_url="https://podcast.example/episodes/5",
    )
    info = build(item)
    assert info["_type"] == "video"
    assert info["id"] == "abc" and info["title"] == "Episode 5"
    assert info["url"] == item.url and info["webpage_url"] == item.webpage_url
    assert info["extractor"] == EXTRACTOR == "copycast:rss"
    assert info["extractor_key"] == EXTRACTOR_KEY
    assert info["ext"] == "mp3" and info["acodec"] == "mp3" and info["vcodec"] == "none"
    assert info["protocol"] == "https"
    assert info["description"] == "desc" and info["timestamp"] == 1705305600
    assert info["duration"] == 1865.0
    assert info["artist"] == "Alex Host" and info["uploader"] == "Alex Host"
    assert info["album"] == "Copycast Test Podcast"
    assert info["episode_number"] == 5 and info["season_number"] == 1
    assert info["thumbnails"] == [{"url": item.thumbnail_url, "id": "0"}]


def test_build_omits_unknown_fields_and_guesses_ext_from_url() -> None:
    info = build(SynthItem(id="x", title="t", url="http://h.example/a/b.m4a"))
    assert info["ext"] == "m4a" and info["acodec"] == "aac"
    assert info["webpage_url"] == "http://h.example/a/b.m4a"
    for key in ("description", "timestamp", "duration", "thumbnails", "episode_number"):
        assert key not in info
    bare = build(SynthItem(id="x", title="t", url="https://edge.example/media/bare"))
    assert "ext" not in bare and "acodec" not in bare


@pytest.mark.parametrize(
    ("mime", "url", "expected"),
    [
        ("audio/mpeg", None, ("mp3", "mp3")),
        ("audio/x-m4a", None, ("m4a", "aac")),
        ("audio/mp4", "https://x/y.mp3", ("m4a", "aac")),
        ("AUDIO/OGG; codecs=opus", None, ("ogg", "vorbis")),
        ("audio/opus", None, ("opus", "opus")),
        ("application/octet-stream", "https://x/y.flac", ("flac", "flac")),
        (None, "https://x/y.MP3?dl=1", ("mp3", "mp3")),
        (None, "https://x/y", (None, None)),
        ("text/html", "https://x/y.pdf", (None, None)),
    ],
)
def test_media_type_from_mime_then_url(
    mime: str | None, url: str | None, expected: tuple[str | None, str | None]
) -> None:
    assert media_type(mime, url) == expected


def test_mime_helpers() -> None:
    assert clean_mime(" Audio/MPEG ; x=y") == "audio/mpeg"
    assert clean_mime("") is None and clean_mime(None) is None
    assert url_ext("https://x/a.b.M4A") == "m4a"
    assert url_ext("https://x/noext") is None
    assert mime_for_ext("m4a") == "audio/mp4"
    assert mime_for_ext(".mp3") == "audio/mpeg"
    assert mime_for_ext("weird") == "application/octet-stream"


def test_from_listing_item_maps_catalog_metadata() -> None:
    item = listing_item(
        5,
        published_at=datetime(2024, 1, 15, 8, tzinfo=UTC),
        source_number=5,
        source_season=1,
        author=None,
        artwork_url=None,
        enclosure_type="audio/x-m4a",
        enclosure_url="https://podcast.example/media/5.m4a",
    )
    synth = from_listing_item(
        item,
        item_id="0123456789abcdef",
        feed_title="Show",
        feed_author="Host",
        feed_artwork_url="https://podcast.example/artwork.jpg",
    )
    assert synth.id == "0123456789abcdef"
    assert synth.url == item.enclosure_url and synth.mime == "audio/x-m4a"
    assert synth.ext == "m4a" and synth.acodec == "aac"
    assert synth.timestamp == 1705305600 and synth.duration == 300.0
    assert synth.artist == "Host" and synth.album == "Show"
    assert synth.episode_number == 5 and synth.season_number == 1
    assert synth.thumbnail_url == "https://podcast.example/artwork.jpg"
    assert synth.webpage_url == item.source_url
    assert synth.extractor == "copycast:rss"


def test_from_listing_item_requires_an_enclosure() -> None:
    with pytest.raises(ValueError):
        from_listing_item(listing_item(1, enclosure_url=None), item_id="x")
