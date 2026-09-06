from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from lxml import etree

from copycast.adapters.sources.http import BodyTooLarge, NotAFeed
from copycast.adapters.sources.rss import (
    NS_ATOM,
    NS_ITUNES,
    NS_PODCAST,
    ParsedFeed,
    channel_xml,
    fetch_feed,
    item_xml,
    parse_date,
    parse_duration,
    parse_feed,
    parse_fragment,
    tag,
)
from copycast.domain.enums import ListingOrder
from tests.support.origin import Origin


@pytest.fixture
def rich(fixtures_dir: Path) -> ParsedFeed:
    return parse_feed((fixtures_dir / "rss/itunes_podcast20.xml").read_bytes())


@pytest.fixture
def edge(fixtures_dir: Path) -> ParsedFeed:
    return parse_feed(
        (fixtures_dir / "rss/edge_cases.xml").read_bytes(), url="https://edge.example/feed.xml"
    )


def test_channel_metadata(rich: ParsedFeed) -> None:
    listing = rich.listing
    assert listing.service == "RSS" and listing.extractor_key is None
    assert listing.title == "Copycast Test Podcast"
    assert listing.description is not None and "<b>fixture</b>" in listing.description
    assert listing.author == "Fixture Media"
    assert listing.artwork_url == "https://podcast.example/artwork.jpg"
    assert listing.webpage_url == "https://podcast.example/"
    assert listing.language == "en-us"
    assert listing.listing_order is ListingOrder.newest_first
    assert listing.raw is None
    assert rich.next_url is None


def test_item_identity_and_order(rich: ParsedFeed) -> None:
    keys = rich.listing.source_keys
    assert keys == [
        "urn:fixture:episode:5",
        "urn:fixture:episode:4",
        "https://podcast.example/episodes/3",
        "urn:fixture:episode:trailer",
        "https://podcast.example/media/2.mp3",
        "urn:fixture:episode:1",
    ]
    assert [item.position for item in rich.listing.items] == list(range(6))
    assert set(rich.items) == set(keys)
    assert all(item.archivable for item in rich.listing.items)


def test_item_fields(rich: ParsedFeed) -> None:
    five, four, three, trailer, two, one = rich.listing.items
    assert five.title == "Episode 5: Chapters, transcripts and all"
    assert five.published_at == datetime(2024, 1, 15, 8, tzinfo=UTC)
    assert five.duration_seconds == 31 * 60 + 5
    assert five.source_number == 5 and five.source_season == 1
    assert five.author == "Alex Host"
    assert five.artwork_url == "https://podcast.example/artwork/5.jpg"
    assert five.enclosure_url == "https://podcast.example/media/5.mp3"
    assert five.enclosure_type == "audio/mpeg"
    assert five.source_url == "https://podcast.example/episodes/5"
    assert five.description is not None and "<em>fifth</em>" in five.description
    assert four.duration_seconds == 1865
    assert four.enclosure_url == "https://podcast.example/media/4.mp3?token=abc"
    assert four.description == "Plain text description with an ampersand & an entity — dash."
    assert three.duration_seconds == 12 * 60 + 34 and three.enclosure_type == "audio/x-m4a"
    assert trailer.source_number is None and trailer.duration_seconds == 45
    assert two.source_number == 2 and two.published_at == datetime(2023, 12, 18, 8, tzinfo=UTC)
    assert one.author is None


def test_edge_cases_follow_the_fixture_comments(edge: ParsedFeed) -> None:
    items = {item.source_key: item for item in edge.listing.items}
    assert edge.listing.title == "Edge Cases & Oddities"
    assert edge.listing.artwork_url == "https://edge.example/artwork.png"
    assert "urn:edge:padded" in items  # 1. trimmed guid
    assert items["urn:edge:dup"].title == "Duplicate guid (first)"  # 2. second dropped
    assert "https://edge.example/media/noguid.mp3" in items  # 3. enclosure identity
    assert items["urn:edge:noenclosure"].archivable is False  # 4. listed, not archivable
    assert items["urn:edge:noenclosure"].enclosure_url is None
    assert not any(item.title == "Nothing to identify" for item in items.values())  # 5. skipped
    assert "https://edge.example/media/emptyguid.mp3" in items  # 6. empty guid
    bare = items["urn:edge:bare"]  # 7.
    assert bare.enclosure_url == "https://edge.example/media/bare"
    assert bare.enclosure_type is None and bare.duration_seconds == 1234
    assert items["urn:edge:baddate"].published_at is None  # 8.
    assert items["urn:edge:nodate"].published_at is None
    assert items["urn:edge:untitled"].title == "urn:edge:untitled"  # 9. placeholder
    relative = items["urn:edge:relative"]  # 10.
    assert relative.enclosure_url == "https://edge.example/media/relative.mp3"
    assert relative.artwork_url == "https://edge.example/images/relative.jpg"
    entities = items["urn:edge:entities"]  # 11.
    assert entities.title == "Entities <b>bold</b> & “quotes”"
    assert entities.source_number is None and entities.source_season is None
    assert items["urn:edge:two"].enclosure_url == "https://edge.example/media/two.mp3"  # 12.
    assert items["urn:edge:odddate"].published_at == datetime(2024, 2, 21, 9, tzinfo=UTC)  # 13.
    assert len(items) == 13


def test_large_fixture_parses_all_items(fixtures_dir: Path) -> None:
    parsed = parse_feed((fixtures_dir / "rss/large_2000.xml").read_bytes())
    assert len(parsed.listing.items) == 2000
    assert parsed.listing.items[0].source_key == "urn:large:episode:2000"
    assert parsed.listing.items[-1].source_key == "urn:large:episode:1"


@pytest.mark.parametrize("name", ["rss/atom.xml", "rss/no_channel.xml"])
def test_non_rss_documents_are_rejected(fixtures_dir: Path, name: str) -> None:
    with pytest.raises(NotAFeed):
        parse_feed((fixtures_dir / name).read_bytes())


def test_garbage_is_rejected() -> None:
    with pytest.raises(NotAFeed):
        parse_feed(b"<html><body>nope</body></html>")
    with pytest.raises(NotAFeed):
        parse_feed(b"not xml at all <<<")
    with pytest.raises(NotAFeed):
        parse_feed(b"   ")
    with pytest.raises(NotAFeed):
        parse_feed(
            b'<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>'
        )


def test_xxe_and_dtd_are_neutralized(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")
    doc = f"""<?xml version="1.0"?>
    <!DOCTYPE rss [<!ENTITY xxe SYSTEM "file://{secret}"> <!ENTITY lol "lol">]>
    <rss version="2.0"><channel><title>&xxe;&lol;</title>
    <item><guid>g</guid><title>&xxe;</title>
    <enclosure url="https://x/a.mp3" type="audio/mpeg"/></item>
    </channel></rss>"""
    try:
        parsed = parse_feed(doc.encode())
    except NotAFeed:
        return
    assert "TOP SECRET" not in (parsed.listing.title or "")
    assert "TOP SECRET" not in parsed.listing.items[0].title
    assert "TOP SECRET" not in item_xml(parsed.items["g"])


def test_size_cap() -> None:
    from copycast.adapters.sources import rss

    padding = b"<!--" + b"x" * (rss.MAX_FEED_BYTES + 1) + b"-->"
    with pytest.raises(BodyTooLarge):
        parse_feed(b"<rss><channel></channel></rss>" + padding)


def test_channel_xml_keeps_rss_wrapper_and_namespaces_without_items(rich: ParsedFeed) -> None:
    text = channel_xml(rich)
    root = parse_fragment(text)
    assert root.tag == "rss" and root.get("version") == "2.0"
    assert root.nsmap["itunes"] == NS_ITUNES and root.nsmap["podcast"] == NS_PODCAST
    channel = root.find("channel")
    assert channel is not None
    assert channel.find("item") is None
    assert channel.findtext("title") == "Copycast Test Podcast"
    assert channel.find(tag(NS_ITUNES, "new-feed-url")) is not None
    assert channel.find(tag(NS_ATOM, "link")) is not None
    # the parsed feed itself is untouched
    assert len(rich.channel.findall("item")) == 6


def test_item_xml_round_trips_with_namespaces(rich: ParsedFeed) -> None:
    text = item_xml(rich.items["urn:fixture:episode:5"])
    element = parse_fragment(text)
    assert element.tag == "item"
    assert element.findtext("guid") == "urn:fixture:episode:5"
    assert len(element.findall(tag(NS_PODCAST, "transcript"))) == 4
    assert element.find(tag(NS_ITUNES, "image")) is not None
    assert "CDATA" not in text or "<em>fifth</em>" in text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Mon, 15 Jan 2024 08:00:00 GMT", datetime(2024, 1, 15, 8, tzinfo=UTC)),
        ("Mon, 08 Jan 2024 08:00:00 +0000", datetime(2024, 1, 8, 8, tzinfo=UTC)),
        ("Wed, 21 Feb 24 10:00:00 +0100", datetime(2024, 2, 21, 9, tzinfo=UTC)),
        ("2024-03-01T10:00:00Z", datetime(2024, 3, 1, 10, tzinfo=UTC)),
        ("2024-03-01T10:00:00+02:00", datetime(2024, 3, 1, 8, tzinfo=UTC)),
        ("2024-03-01 10:00:00", datetime(2024, 3, 1, 10, tzinfo=UTC)),
        ("yesterday-ish", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_date(text: str | None, expected: datetime | None) -> None:
    assert parse_date(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("00:31:05", 1865),
        ("12:34", 754),
        ("1865", 1865),
        ("1234.5", 1234),
        ("1:02:03.9", 3723),
        ("45", 45),
        ("", None),
        ("soon", None),
        ("1:2:3:4", None),
        ("-5", None),
        (None, None),
    ],
)
def test_parse_duration(text: str | None, expected: int | None) -> None:
    assert parse_duration(text) == expected


def test_fetch_feed_conditional_and_paging(origin: Origin) -> None:
    url = origin.url_for("/rss/paged_1.xml")
    first = fetch_feed(url)
    assert not first.not_modified and first.parsed is not None and first.body
    assert first.etag and first.pages == 1
    assert first.parsed.listing.source_keys == ["urn:paged:episode:4", "urn:paged:episode:3"]
    assert first.parsed.next_url == origin.url_for("/rss/paged_2.xml")

    again = fetch_feed(url, etag=first.etag)
    assert again.not_modified and again.parsed is None and again.body is None
    assert origin.requests[-1].if_none_match == first.etag

    paged = fetch_feed(url, follow_next=True)
    assert paged.parsed is not None and paged.pages == 2
    assert paged.parsed.listing.source_keys == [
        "urn:paged:episode:4",
        "urn:paged:episode:3",
        "urn:paged:episode:2",
        "urn:paged:episode:1",
    ]
    assert [i.position for i in paged.parsed.listing.items] == [0, 1, 2, 3]
    assert paged.parsed.items["urn:paged:episode:3"].findtext("title") == "Page one, episode 3"
    assert paged.body == first.body  # the stored body is page one


def test_fetch_feed_stops_after_max_pages_and_on_bad_pages(origin: Origin) -> None:
    origin.script("/rss/paged_2.xml", status=200, body="<html/>", content_type="text/html")
    paged = fetch_feed(origin.url_for("/rss/paged_1.xml"), follow_next=True)
    assert paged.parsed is not None and paged.pages == 1
    assert len(paged.parsed.listing.items) == 2
    limited = fetch_feed(origin.url_for("/rss/paged_1.xml"), follow_next=True, max_pages=1)
    assert limited.pages == 1


def test_fetch_feed_rejects_non_feed_bodies(origin: Origin) -> None:
    with pytest.raises(NotAFeed):
        fetch_feed(origin.url_for("/html/two_feeds.html"))


def test_parser_is_hardened() -> None:
    from copycast.adapters.sources.rss import PARSER

    assert isinstance(PARSER, etree.XMLParser)
