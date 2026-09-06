"""The pure helpers behind ``copycast rebuild``: sidecar parsing, MIME, dates, durations."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from copycast.adapters.storage import rebuild as rb
from copycast.adapters.storage.layout import Layout
from copycast.domain.enums import AssetKind

FEED = "0123456789abcdef"
ITEM = "fedcba9876543210"


@pytest.mark.parametrize(
    ("ext", "mime"),
    [
        ("m4a", "audio/mp4"),
        (".MP3", "audio/mpeg"),
        ("opus", "audio/opus"),
        ("webm", "audio/webm"),
        ("jpg", "image/jpeg"),
        ("json", "application/json"),
        ("vtt", "text/vtt"),
        ("zzz", "application/octet-stream"),
    ],
)
def test_mime_for(ext: str, mime: str) -> None:
    assert rb.mime_for(ext) == mime


def test_asset_format() -> None:
    assert rb._asset_format(AssetKind.artwork, "jpg") is None
    assert rb._asset_format(AssetKind.chapters, "anything") == "json"
    assert rb._asset_format(AssetKind.transcript, "VTT") == "vtt"
    assert rb._asset_format(AssetKind.transcript, "txt") == "text"
    assert rb._asset_format(AssetKind.transcript, "pdf") is None


def test_parse_item_xml_identity_and_metadata() -> None:
    raw = (
        b'<item xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        b'<title> Hello </title><guid isPermaLink="false">urn:x:1</guid>'
        b"<link>https://podcast.example/1</link><description>d</description>"
        b"<pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>"
        b"<itunes:duration>1:02:03</itunes:duration>"
        b'<enclosure url="https://podcast.example/1.mp3" type="audio/mpeg" length="1"/></item>'
    )
    parsed = rb._parse_item_xml(raw)
    assert parsed["source_key"] == "urn:x:1"
    assert parsed["title"] == "Hello"
    assert parsed["description"] == "d"
    assert parsed["source_url"] == "https://podcast.example/1"
    assert parsed["published_at"] == datetime(2024, 1, 1, 12, tzinfo=UTC)
    assert parsed["duration_seconds"] == 3723
    assert parsed["xml"].startswith("<item")
    no_guid = rb._parse_item_xml(b'<item><enclosure url=" https://x/2.mp3 "/></item>')
    assert no_guid["source_key"] == "https://x/2.mp3"
    assert no_guid["title"] is None and no_guid["published_at"] is None
    assert rb._parse_item_xml(b"<item><guid></guid></item>")["source_key"] is None
    assert rb._parse_item_xml(b"") == {}
    assert rb._parse_item_xml(b"\x00\x01 not xml at all") == {}


def test_channel_xml_strips_items() -> None:
    raw = (
        b'<rss version="2.0"><channel><title>T</title>'
        b"<item><title>a</title></item><item><title>b</title></item></channel></rss>"
    )
    channel = rb._channel_xml(raw)
    assert channel is not None
    assert "<item>" not in channel and "<title>T</title>" in channel
    assert channel.startswith('<rss version="2.0">')
    assert rb._channel_xml(b"<feed><entry/></feed>") is None
    assert rb._channel_xml(b"") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("  ", None),
        ("45", 45),
        ("1:30", 90),
        ("01:02:03", 3723),
        ("1:02:03.5", 3723),
        ("abc", None),
    ],
)
def test_itunes_duration(value: str | None, expected: int | None) -> None:
    assert rb._itunes_duration(value) == expected


def test_rfc2822_dates() -> None:
    assert rb._rfc2822(None) is None
    assert rb._rfc2822("  ") is None
    assert rb._rfc2822("garbage") is None
    assert rb._rfc2822("Mon, 01 Jan 2024 12:00:00 +0200") == datetime(2024, 1, 1, 10, tzinfo=UTC)
    naive = rb._rfc2822("Mon, 01 Jan 2024 12:00:00 -0000")
    assert naive == datetime(2024, 1, 1, 12, tzinfo=UTC)


def test_info_helpers() -> None:
    assert rb._info_timestamp({"timestamp": 1_700_000_000}) == datetime.fromtimestamp(
        1_700_000_000, tz=UTC
    )
    assert rb._info_timestamp({"release_timestamp": 86400.0}) == datetime(1970, 1, 2, tzinfo=UTC)
    assert rb._info_timestamp({"upload_date": "20240215"}) == datetime(2024, 2, 15, tzinfo=UTC)
    assert rb._info_timestamp({"upload_date": "2024-02-15", "timestamp": True}) is None
    assert rb._info_timestamp({}) is None
    assert rb._info_source_key({"copycast_source_key": " urn:k "}) == "urn:k"
    assert rb._info_source_key({"extractor_key": "Youtube", "id": "abc"}) == "Youtube:abc"
    assert (
        rb._info_source_key(
            {
                "extractor_key": "CopycastRSS",
                "extractor": "copycast:rss",
                "id": "x",
                "original_url": "https://x/1.mp3",
            }
        )
        == "https://x/1.mp3"
    )
    assert rb._info_source_key({"webpage_url": "https://w"}) == "https://w"
    assert rb._info_source_key({}) is None
    assert rb._str(" x ") == "x" and rb._str("  ") is None and rb._str(3) is None
    assert rb._int("12") == 12 and rb._int(3.9) == 3 and rb._int(True) is None
    assert rb._int("x") is None and rb._int(None) is None
    assert rb._chunks([{"a": 1}, {"a": 2}, {"a": 3}], 2) == [[{"a": 1}, {"a": 2}], [{"a": 3}]]


def test_draft_from_sidecars_prefers_item_xml(data_dir: Path) -> None:
    layout = Layout(data_dir)
    layout.ensure_feed_dirs(FEED)
    media = layout.media_path(FEED, ITEM, "mp3")
    media.write_bytes(b"\0" * 4407)
    layout.info_json_path(FEED, ITEM).write_text(
        json.dumps(
            {
                "title": "From info",
                "extractor_key": "Generic",
                "id": "1",
                "timestamp": 1_700_000_000,
                "duration": 30,
                "artist": "Artist",
                "thumbnail": "https://x/t.jpg",
                "episode_number": 4,
                "season_number": 2,
                "webpage_url": "https://x/page",
            }
        )
    )
    layout.item_xml_path(FEED, ITEM).write_text(
        "<item><guid>urn:x:4</guid><title>From XML</title>"
        "<pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item>"
    )
    draft = rb._draft_from_sidecars(layout, FEED, media)
    assert draft["id"] == ITEM
    assert draft["source_key"] == "urn:x:4"
    assert draft["title"] == "From XML"
    assert draft["published_at"] == datetime(2024, 1, 1, tzinfo=UTC)
    assert draft["duration_seconds"] == 30
    assert (draft["author"], draft["artwork_url"]) == ("Artist", "https://x/t.jpg")
    assert (draft["source_number"], draft["source_season"]) == (4, 2)
    assert draft["source_url"] == "https://x/page"
    assert draft["media_path"] == f"media/{ITEM}.mp3"
    assert (draft["media_mime"], draft["media_bytes"]) == ("audio/mpeg", 4407)
    assert draft["source_item_xml"].startswith("<item>")
    assert isinstance(draft["archived_at"], datetime)


def test_draft_from_sidecars_without_sidecars_or_with_broken_info(data_dir: Path) -> None:
    layout = Layout(data_dir)
    layout.ensure_feed_dirs(FEED)
    media = layout.media_path(FEED, ITEM, "m4a")
    media.write_bytes(b"\0")
    layout.info_json_path(FEED, ITEM).write_text("{broken")
    draft = rb._draft_from_sidecars(layout, FEED, media)
    assert draft["source_key"] == f"urn:copycast:{ITEM}"
    assert draft["title"] == ITEM
    assert draft["published_at"] is None and draft["source_item_xml"] is None
