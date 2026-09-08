from __future__ import annotations

from datetime import UTC, datetime

import pytest

from copycast.adapters.sources.http import Fetched, SourceError
from copycast.adapters.sources.youtube_feed import (
    FeedEntry,
    channel_feed_url,
    enrich_from_youtube_feed,
    enrich_listing,
    parse_channel_feed,
    video_id_of,
)
from tests.support.factories import listing, listing_item

ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
  <title>Underscore_</title>
  <entry>
    <id>yt:video:vid00000042</id>
    <yt:videoId>vid00000042</yt:videoId>
    <title>Newest</title>
    <published>2026-09-01T10:00:00+00:00</published>
    <media:group>
      <media:title>Newest</media:title>
      <media:description>Full notes of the newest video.</media:description>
    </media:group>
  </entry>
  <entry>
    <yt:videoId>vid00000010</yt:videoId>
    <published>2026-08-20T08:30:00Z</published>
    <media:group><media:description>  </media:description></media:group>
  </entry>
  <entry><title>no id</title></entry>
</feed>
"""


def test_channel_feed_url_by_channel_then_playlist() -> None:
    assert (
        channel_feed_url({"extractor": "youtube:tab", "channel_id": "UCabc", "id": "UCabc"})
        == "https://www.youtube.com/feeds/videos.xml?channel_id=UCabc"
    )
    assert (
        channel_feed_url({"extractor": "youtube:tab", "id": "PLxyz"})
        == "https://www.youtube.com/feeds/videos.xml?playlist_id=PLxyz"
    )
    assert channel_feed_url({"extractor": "soundcloud:user", "channel_id": "UCabc"}) is None
    assert channel_feed_url({"extractor": "youtube:tab", "id": "@handle"}) is None
    assert channel_feed_url({}) is None


def test_parse_channel_feed_reads_ids_dates_and_descriptions() -> None:
    entries = parse_channel_feed(ATOM)
    assert set(entries) == {"vid00000042", "vid00000010"}
    assert entries["vid00000042"] == FeedEntry(
        published_at=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
        description="Full notes of the newest video.",
    )
    assert entries["vid00000010"] == FeedEntry(
        published_at=datetime(2026, 8, 20, 8, 30, tzinfo=UTC), description=None
    )
    assert parse_channel_feed(b"<not xml") == {}
    assert parse_channel_feed(b"<html><body>Sorry</body></html>") == {}


def test_video_id_from_url_then_key() -> None:
    assert video_id_of(listing_item(1, source_url="https://www.youtube.com/watch?v=abc123")) == (
        "abc123"
    )
    assert video_id_of(listing_item(1, source_url="https://youtu.be/xyz789")) == "xyz789"
    assert video_id_of(listing_item(1, key="Youtube:vid00000042", source_url=None)) == (
        "vid00000042"
    )


def test_enrich_listing_sets_exact_dates_and_fills_missing_descriptions() -> None:
    approximate = datetime(2026, 8, 1, tzinfo=UTC)
    source = listing(
        2,
        service="YouTube",
        with_dates=False,
        raw={"extractor": "youtube:tab", "channel_id": "UCabc"},
        items=[
            listing_item(
                1,
                key="Youtube:vid00000042",
                source_url="https://www.youtube.com/watch?v=vid00000042",
                description=None,
                published_at=approximate,
            ),
            listing_item(
                2,
                key="Youtube:vid00000010",
                source_url="https://www.youtube.com/watch?v=vid00000010",
                description="Kept",
                published_at=None,
            ),
            listing_item(
                3, key="Youtube:other", source_url="https://www.youtube.com/watch?v=other"
            ),
        ],
    )
    enriched = enrich_listing(source, parse_channel_feed(ATOM))
    newest, older, untouched = enriched.items
    assert newest.published_at == datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    assert newest.description == "Full notes of the newest video."
    assert older.published_at == datetime(2026, 8, 20, 8, 30, tzinfo=UTC)
    assert older.description == "Kept"
    assert untouched == source.items[2]
    assert enrich_listing(source, {}) is source


def test_enrich_from_youtube_feed_uses_the_fetcher_and_reports_failures() -> None:
    source = listing(
        1,
        service="YouTube",
        raw={"extractor": "youtube:tab", "channel_id": "UCabc"},
        items=[
            listing_item(
                1,
                key="Youtube:vid00000042",
                source_url="https://www.youtube.com/watch?v=vid00000042",
                description=None,
            )
        ],
    )
    seen: list[str] = []

    def fetcher(url: str) -> Fetched:
        seen.append(url)
        return Fetched(url=url, status=200, body=ATOM)

    enriched, matched = enrich_from_youtube_feed(source, fetcher=fetcher)
    assert seen == ["https://www.youtube.com/feeds/videos.xml?channel_id=UCabc"]
    assert matched == 1 and enriched.items[0].description == "Full notes of the newest video."

    # Not a YouTube tab: nothing fetched.
    plain = listing(1, raw={"extractor": "soundcloud:user"})
    assert enrich_from_youtube_feed(plain, fetcher=fetcher) == (plain, 0)
    assert len(seen) == 1

    def failing(url: str) -> Fetched:
        raise SourceError(f"boom {url}")

    with pytest.raises(SourceError):
        enrich_from_youtube_feed(source, fetcher=failing)
