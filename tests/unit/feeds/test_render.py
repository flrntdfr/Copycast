"""Golden Mirror Feed tests.

Each scenario renders a feed from the read model and compares it, canonicalized
(C14N 2.0, volatile dates blanked), with ``tests/fixtures/golden/<name>.xml``.
Regenerate the golden files with ``make golden-regen`` (``COPYCAST_GOLDEN_REGEN=1``).
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from lxml import etree

from copycast.adapters.feeds.render import (
    CONTENT_TYPE,
    GENERATOR,
    AssetView,
    FeedView,
    ItemView,
    RenderedFeed,
    placeholder_length,
    render_feed,
    rfc2822,
)
from copycast.adapters.sources.rss import (
    NS_ATOM,
    NS_ITUNES,
    NS_PODCAST,
    ParsedFeed,
    channel_xml,
    item_xml,
    parse_feed,
    parse_fragment,
    tag,
)
from copycast.domain.credentials import artwork_mac
from copycast.domain.enums import (
    ArchiveState,
    AssetFormat,
    AssetKind,
    AssetProvenance,
    AssetState,
    BackfillMode,
    FeedKind,
    SourceKind,
)
from tests.support.xml import assert_xml_equal, canonicalize

BASE_URL = "http://testserver"
NOW = datetime(2024, 3, 1, 12, 0, tzinfo=UTC)
GOLDEN_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "golden"
FEED_ID = "0123456789abcdef"
ART_SECRET = b"\x07" * 32


def check_golden(name: str, rendered: RenderedFeed) -> None:
    path = GOLDEN_DIR / f"{name}.xml"
    if os.environ.get("COPYCAST_GOLDEN_REGEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonicalize(rendered.body, strip_volatile=True), encoding="utf-8")
    if not path.exists():
        pytest.fail(f"golden file {path} missing; run `make golden-regen`")
    assert_xml_equal(rendered.body, path.read_bytes())


# --------------------------------------------------------------------------- builders


def transcript(
    language: str,
    fmt: AssetFormat,
    provenance: AssetProvenance = AssetProvenance.mirrored,
    item: str = "i5",
) -> AssetView:
    return AssetView(
        kind=AssetKind.transcript,
        local_path=f"assets/{item}.transcript.{language}.{provenance.value}.{fmt.value}",
        provenance=provenance,
        language=language,
        format=fmt,
    )


def art_id(item: str | None) -> str:
    """A stand-in asset id for the artwork of ``item`` (``None``: the feed's)."""
    return hashlib.sha256(f"artwork\n{item or ''}".encode()).hexdigest()[:16]


def art_url(item: str | None, ext: str = "jpg") -> str:
    """The public ``/art/`` URL the feed publishes for :func:`artwork` of ``item``."""
    asset_id = art_id(item)
    return f"{BASE_URL}/art/{asset_id}-{artwork_mac(ART_SECRET, asset_id)}.{ext}"


def artwork(item: str | None, ext: str = "jpg") -> AssetView:
    stem = item or "feed"
    return AssetView(
        kind=AssetKind.artwork,
        id=art_id(item),
        local_path=f"assets/{stem}.artwork.{ext}",
        mime=f"image/{'jpeg' if ext == 'jpg' else ext}",
    )


def chapters(
    item: str,
    provenance: AssetProvenance = AssetProvenance.mirrored,
    state: AssetState = AssetState.archived,
) -> AssetView:
    return AssetView(
        kind=AssetKind.chapters,
        local_path=f"assets/{item}.chapters.json",
        provenance=provenance,
        state=state,
        format=AssetFormat.json,
    )


def item(
    ident: str,
    ordinal: int,
    title: str = "Episode",
    *,
    state: ArchiveState = ArchiveState.archived,
    listed: bool = True,
    published_at: datetime | None = None,
    first_seen_at: datetime | None = None,
    media_bytes: int = 5155,
    media_ext: str = "mp3",
    media_mime: str = "audio/mpeg",
    public_ext: str | None = None,
    source_item_xml: str | None = None,
    assets: tuple[AssetView, ...] = (),
    **extra: object,
) -> ItemView:
    """``public_ext`` defaults to the archived container (else mp3): the goldens' URLs."""
    return ItemView(
        id=ident,
        ordinal=ordinal,
        title=title,
        archive_state=state,
        listed=listed,
        published_at=published_at,
        first_seen_at=first_seen_at or (published_at or NOW) - timedelta(days=1),
        media_ext=media_ext,
        media_mime=media_mime,
        media_bytes=media_bytes,
        public_ext=public_ext or media_ext or "mp3",
        source_item_xml=source_item_xml,
        assets=assets,
        **extra,  # type: ignore[arg-type]
    )


@pytest.fixture
def rich(fixtures_dir: Path) -> ParsedFeed:
    return parse_feed((fixtures_dir / "rss/itunes_podcast20.xml").read_bytes())


def rss_feed(rich: ParsedFeed, *, assets: tuple[AssetView, ...] = ()) -> FeedView:
    listing = rich.listing
    return FeedView(
        id=FEED_ID,
        kind=FeedKind.mirror,
        title=listing.title or "",
        revision=7,
        description=listing.description,
        author=listing.author,
        artwork_url=listing.artwork_url,
        language=listing.language,
        link=listing.webpage_url,
        source_kind=SourceKind.rss,
        source_channel_xml=channel_xml(rich),
        last_modified=NOW,
        assets=assets,
    )


def rss_items(rich: ParsedFeed, keys: list[str], **per_key: dict[str, object]) -> list[ItemView]:
    views: list[ItemView] = []
    listing = {i.source_key: i for i in rich.listing.items}
    ordinals = {key: n for n, key in enumerate(reversed(rich.listing.source_keys), start=1)}
    for key in keys:
        source = listing[key]
        overrides = per_key.get(key, {})
        views.append(
            item(
                f"i{ordinals[key]}",
                ordinals[key],
                source.title,
                published_at=source.published_at,
                source_item_xml=item_xml(rich.items[key]),
                source_number=source.source_number,
                source_season=source.source_season,
                duration_seconds=source.duration_seconds,
                source_url=source.source_url,
                **overrides,  # type: ignore[arg-type]
            )
        )
    return views


def channel_of(rendered: RenderedFeed):
    root = parse_fragment(rendered.body)
    channel = root.find("channel")
    assert channel is not None
    return root, channel


# --------------------------------------------------------------------------- golden scenarios


def test_rss_preserved(rich: ParsedFeed) -> None:
    feed = rss_feed(rich, assets=(artwork(None),))
    items = rss_items(
        rich,
        [
            "urn:fixture:episode:5",
            "urn:fixture:episode:4",
            "https://podcast.example/episodes/3",
            "urn:fixture:episode:1",
        ],
        **{
            "urn:fixture:episode:5": {
                "assets": (
                    chapters("i6"),
                    transcript("en", AssetFormat.vtt, item="i6"),
                    transcript("de", AssetFormat.srt, item="i6"),
                    artwork("i6"),
                )
            },
            "urn:fixture:episode:1": {"assets": (chapters("i1", state=AssetState.failed),)},
        },
    )
    items.append(item("i2", 2, "Episode 2", state=ArchiveState.available, published_at=NOW))
    rendered = render_feed(feed, items, base_url=BASE_URL, artwork_secret=ART_SECRET, now=NOW)
    assert rendered.revision == 7 and rendered.last_modified == NOW
    assert rendered.content_type == CONTENT_TYPE
    root, channel = channel_of(rendered)
    assert root.get("version") == "2.0"
    assert root.nsmap["itunes"] == NS_ITUNES and root.nsmap["podcast"] == NS_PODCAST
    assert channel.findtext("title") == "Copycast Test Podcast"
    assert channel.findtext("generator") == GENERATOR
    assert channel.findtext("lastBuildDate") == rfc2822(NOW)
    assert channel.find(tag(NS_ITUNES, "new-feed-url")) is None
    self_links = [
        link for link in channel.findall(tag(NS_ATOM, "link")) if link.get("rel") == "self"
    ]
    assert len(self_links) == 1 and self_links[0].get("href") == f"{BASE_URL}/feeds/{FEED_ID}.xml"
    image = channel.find(tag(NS_ITUNES, "image"))
    assert image is not None and image.get("href") == art_url(None)
    assert channel.findtext("copyright") == "© 2024 Fixture Media"
    rendered_items = channel.findall("item")
    assert [i.findtext("guid") for i in rendered_items] == [
        "urn:fixture:episode:5",
        "urn:fixture:episode:4",
        "https://podcast.example/episodes/3",
        "urn:fixture:episode:1",
    ]
    five = rendered_items[0]
    guid = five.find("guid")
    assert guid is not None and guid.get("isPermaLink") == "false"
    enclosure = five.findall("enclosure")
    assert len(enclosure) == 1
    assert enclosure[0].get("url") == f"{BASE_URL}/feeds/{FEED_ID}/media/i6.mp3"
    assert enclosure[0].get("length") == "5155" and enclosure[0].get("type") == "audio/mpeg"
    chapter_tags = five.findall(tag(NS_PODCAST, "chapters"))
    assert (
        len(chapter_tags) == 1
        and chapter_tags[0].get("url") == f"{BASE_URL}/feeds/{FEED_ID}/assets/i6.chapters.json"
    )
    transcripts = five.findall(tag(NS_PODCAST, "transcript"))
    assert [(t.get("language"), t.get("type"), t.get("rel")) for t in transcripts] == [
        ("de", "application/x-subrip", "captions"),
        ("en", "text/vtt", "captions"),
    ]
    assert all(
        t.get("url", "").startswith(f"{BASE_URL}/feeds/{FEED_ID}/assets/") for t in transcripts
    )
    five_image = five.find(tag(NS_ITUNES, "image"))
    assert five_image is not None and five_image.get("href") == art_url("i6")
    assert five.find(tag(NS_PODCAST, "person")) is not None
    one = rendered_items[3]
    one_chapters = one.find(tag(NS_PODCAST, "chapters"))
    assert (
        one_chapters is not None
        and one_chapters.get("url") == "https://podcast.example/chapters/1.json"
    )
    four = rendered_items[1]
    four_transcript = four.find(tag(NS_PODCAST, "transcript"))
    assert (
        four_transcript is not None
        and four_transcript.get("url") == "https://podcast.example/transcripts/4.json"
    )
    check_golden("rss_preserved", rendered)


def test_ytdlp_synthesized() -> None:
    feed = FeedView(
        id=FEED_ID,
        kind=FeedKind.mirror,
        title="Fixture Channel - Videos",
        revision=3,
        description="A channel used by the Copycast test suite.",
        author="Fixture Channel",
        artwork_url="https://yt3.example/avatar=s900",
        link="https://www.youtube.com/@fixturechannel/videos",
        source_kind=SourceKind.ytdlp,
        last_modified=NOW,
    )
    items = [
        item(
            "v4",
            4,
            "Fixture video four",
            media_ext="m4a",
            media_mime="audio/mp4",
            media_bytes=12200000,
            published_at=datetime.fromtimestamp(1710000000, tz=UTC),
            duration_seconds=1830,
            source_url="https://www.youtube.com/watch?v=vid00000004",
            author="Fixture Channel",
            artwork_url="https://i.ytimg.example/vi/vid00000004/hqdefault.jpg",
            description="The newest upload.",
            assets=(chapters("v4"), transcript("en", AssetFormat.vtt, item="v4")),
        ),
        item(
            "v3",
            3,
            "Fixture video three (premiere)",
            media_ext="m4a",
            media_mime="audio/mp4",
            published_at=datetime.fromtimestamp(1709500000, tz=UTC),
            duration_seconds=600,
            source_url="https://www.youtube.com/watch?v=vid00000003",
            assets=(artwork("v3"),),
        ),
        item(
            "v2",
            2,
            "Fixture video two",
            media_ext="opus",
            media_mime="audio/opus",
            published_at=datetime(2024, 2, 15, tzinfo=UTC),
            duration_seconds=245,
            source_url=None,
        ),
        item(
            "v1",
            1,
            "Fixture video one",
            state=ArchiveState.wanted,
            published_at=datetime.fromtimestamp(1700000000, tz=UTC),
        ),
    ]
    rendered = render_feed(feed, items, base_url=BASE_URL, artwork_secret=ART_SECRET, now=NOW)
    _, channel = channel_of(rendered)
    assert channel.findtext("title") == "Fixture Channel - Videos"
    assert channel.findtext("link") == "https://www.youtube.com/@fixturechannel/videos"
    assert channel.findtext("language") == "en"
    assert channel.findtext(tag(NS_ITUNES, "author")) == "Fixture Channel"
    assert channel.findtext(tag(NS_ITUNES, "type")) == "episodic"
    assert channel.findtext(tag(NS_ITUNES, "explicit")) == "false"
    assert channel.findtext("generator") == GENERATOR
    rendered_items = channel.findall("item")
    assert [i.findtext("title") for i in rendered_items] == [
        "Fixture video four",
        "Fixture video three (premiere)",
        "Fixture video two",
    ]
    four = rendered_items[0]
    assert four.findtext("guid") == "https://www.youtube.com/watch?v=vid00000004"
    assert four.findtext("pubDate") == "Sat, 09 Mar 2024 16:00:00 GMT"
    assert four.findtext(tag(NS_ITUNES, "duration")) == "1830"
    assert four.findtext(tag(NS_ITUNES, "episode")) == "4"
    assert four.findtext(tag(NS_ITUNES, "episodeType")) == "full"
    assert four.findtext(tag(NS_ITUNES, "author")) == "Fixture Channel"
    enclosure = four.find("enclosure")
    assert enclosure is not None
    assert enclosure.get("url") == f"{BASE_URL}/feeds/{FEED_ID}/media/v4.m4a"
    assert enclosure.get("type") == "audio/mp4" and enclosure.get("length") == "12200000"
    assert four.find(tag(NS_PODCAST, "chapters")) is not None
    assert four.find(tag(NS_PODCAST, "transcript")) is not None
    two = rendered_items[2]
    assert two.findtext("guid") == "urn:copycast:v2"
    assert two.find("link") is None
    three_image = rendered_items[1].find(tag(NS_ITUNES, "image"))
    assert three_image is not None and three_image.get("href") == art_url("v3")
    check_golden("ytdlp_synthesized", rendered)


def test_bucket() -> None:
    feed = FeedView(
        id="copycast-abc234", kind=FeedKind.bucket, title="Copycast", revision=2, last_modified=NOW
    )
    items = [
        item(
            "r1",
            1,
            "A single fixture video",
            media_ext="m4a",
            media_mime="audio/mp4",
            published_at=datetime(2024, 2, 1, tzinfo=UTC),
            first_seen_at=datetime(2024, 2, 20, 9, 30, tzinfo=UTC),
            source_url="https://www.youtube.com/watch?v=vid00000042",
            duration_seconds=754,
        ),
        item(
            "r2",
            2,
            "Part 1: Getting started",
            media_ext="m4a",
            media_mime="audio/mp4",
            published_at=datetime(2024, 1, 1, tzinfo=UTC),
            first_seen_at=datetime(2024, 2, 21, 9, 30, tzinfo=UTC),
            source_url="https://www.youtube.com/watch?v=vid00000010",
            source_number=1,
        ),
    ]
    rendered = render_feed(feed, items, base_url=BASE_URL, artwork_secret=ART_SECRET, now=NOW)
    _, channel = channel_of(rendered)
    assert channel.findtext("title") == "Copycast"
    assert channel.findtext("link") == f"{BASE_URL}/"
    assert channel.findtext("description") == "Episodes saved to the Copycast Bucket with Copycast."
    assert channel.findtext(tag(NS_ITUNES, "author")) == "Copycast"
    logo = channel.find(tag(NS_ITUNES, "image"))
    assert logo is not None and logo.get("href") == f"{BASE_URL}/feeds/copycast-artwork.png"
    self_link = channel.find(tag(NS_ATOM, "link"))
    assert (
        self_link is not None and self_link.get("href") == f"{BASE_URL}/feeds/copycast-abc234.xml"
    )
    pubdates = [i.findtext("pubDate") for i in channel.findall("item")]
    assert pubdates == ["Tue, 20 Feb 2024 09:30:00 GMT", "Wed, 21 Feb 2024 09:30:00 GMT"]
    assert [i.findtext(tag(NS_ITUNES, "episode")) for i in channel.findall("item")] == ["1", "1"]
    check_golden("bucket", rendered)


def test_transcripts_mirrored_wins() -> None:
    feed = FeedView(
        id=FEED_ID, kind=FeedKind.mirror, title="T", source_kind=SourceKind.ytdlp, last_modified=NOW
    )
    assets = (
        transcript("en", AssetFormat.vtt, AssetProvenance.generated, item="t1"),
        transcript("en", AssetFormat.vtt, AssetProvenance.mirrored, item="t1"),
        transcript("de", AssetFormat.vtt, AssetProvenance.generated, item="t1"),
        transcript("en", AssetFormat.json, AssetProvenance.mirrored, item="t1"),
        AssetView(
            kind=AssetKind.transcript,
            local_path="assets/t1.transcript.fr.mirrored.vtt",
            language="fr",
            format=AssetFormat.vtt,
            state=AssetState.failed,
        ),
        AssetView(
            kind=AssetKind.transcript, local_path=None, language="es", format=AssetFormat.vtt
        ),
    )
    rendered = render_feed(
        feed,
        [item("t1", 1, "T1", published_at=NOW, assets=assets)],
        base_url=BASE_URL,
        artwork_secret=ART_SECRET,
        now=NOW,
    )
    _, channel = channel_of(rendered)
    transcripts = channel.findall(f"item/{tag(NS_PODCAST, 'transcript')}")
    assert [
        (t.get("language"), t.get("type"), t.get("rel"), t.get("url", "").rsplit("/", 1)[1])
        for t in transcripts
    ] == [
        ("de", "text/vtt", "captions", "t1.transcript.de.generated.vtt"),
        ("en", "application/json", None, "t1.transcript.en.mirrored.json"),
        ("en", "text/vtt", "captions", "t1.transcript.en.mirrored.vtt"),
    ]
    check_golden("transcripts_mirrored_wins", rendered)


def test_delisted_kept(rich: ParsedFeed) -> None:
    feed = rss_feed(rich)
    items = rss_items(
        rich,
        ["urn:fixture:episode:5", "urn:fixture:episode:4"],
        **{"urn:fixture:episode:4": {"listed": False}},
    )
    rendered = render_feed(feed, items, base_url=BASE_URL, artwork_secret=ART_SECRET, now=NOW)
    _, channel = channel_of(rendered)
    assert [i.findtext("guid") for i in channel.findall("item")] == [
        "urn:fixture:episode:5",
        "urn:fixture:episode:4",
    ]
    check_golden("delisted_kept", rendered)


def test_tombstone_excluded(rich: ParsedFeed) -> None:
    feed = rss_feed(rich)
    items = rss_items(
        rich,
        ["urn:fixture:episode:5", "urn:fixture:episode:4", "https://podcast.example/episodes/3"],
        **{
            "urn:fixture:episode:4": {"state": ArchiveState.deleted, "listed": False},
            "https://podcast.example/episodes/3": {"state": ArchiveState.deleted},
        },
    )
    items += [
        item("w", 9, "wanted", state=ArchiveState.wanted, published_at=NOW),
        item("a", 10, "archiving", state=ArchiveState.archiving, published_at=NOW),
        item("f", 11, "failed", state=ArchiveState.failed, published_at=NOW),
    ]
    rendered = render_feed(feed, items, base_url=BASE_URL, artwork_secret=ART_SECRET, now=NOW)
    _, channel = channel_of(rendered)
    assert [i.findtext("guid") for i in channel.findall("item")] == ["urn:fixture:episode:5"]
    check_golden("tombstone_excluded", rendered)


def test_enclosure_length_real(rich: ParsedFeed) -> None:
    feed = rss_feed(rich)
    items = rss_items(
        rich,
        ["https://podcast.example/episodes/3"],
        **{
            "https://podcast.example/episodes/3": {
                "media_bytes": 987654,
                "media_ext": "m4a",
                "media_mime": "audio/mp4",
            }
        },
    )
    rendered = render_feed(feed, items, base_url=BASE_URL, artwork_secret=ART_SECRET, now=NOW)
    _, channel = channel_of(rendered)
    enclosure = channel.find("item/enclosure")
    assert enclosure is not None
    assert enclosure.get("length") == "987654"
    assert enclosure.get("type") == "audio/mp4"
    assert enclosure.get("url") == f"{BASE_URL}/feeds/{FEED_ID}/media/i4.m4a"
    assert b'length="4407"' not in rendered.body
    check_golden("enclosure_length_real", rendered)


def test_artwork_local(rich: ParsedFeed) -> None:
    feed = rss_feed(rich, assets=(artwork(None, "png"),))
    items = rss_items(
        rich,
        ["urn:fixture:episode:5", "urn:fixture:episode:4", "urn:fixture:episode:trailer"],
        **{
            "urn:fixture:episode:5": {"assets": (artwork("i6"),)},
            "urn:fixture:episode:trailer": {"assets": (artwork("i3", "webp"),)},
        },
    )
    rendered = render_feed(feed, items, base_url=BASE_URL, artwork_secret=ART_SECRET, now=NOW)
    _, channel = channel_of(rendered)
    feed_image = channel.find(tag(NS_ITUNES, "image"))
    assert feed_image is not None and feed_image.get("href") == art_url(None, "png")
    hrefs = [
        i.find(tag(NS_ITUNES, "image")).get("href")
        for i in channel.findall("item")
        if i.find(tag(NS_ITUNES, "image")) is not None
    ]  # type: ignore[union-attr]
    assert hrefs == [
        art_url("i6"),
        "https://podcast.example/artwork/4.jpg",
        art_url("i3", "webp"),
    ]
    check_golden("artwork_local", rendered)


# --------------------------------------------------------------------------- behaviour


def test_automatic_feed_lists_unarchived_items_under_their_stable_url() -> None:
    feed = FeedView(
        id=FEED_ID,
        kind=FeedKind.mirror,
        title="On demand",
        source_kind=SourceKind.ytdlp,
        backfill_mode=BackfillMode.automatic,
    )
    items = [
        # Listed before 1.3 under the ".mp3" placeholder, archived as m4a: the URL apps
        # hold stays, the type is the file's.
        item(
            "aaaaaaaaaaaaaaaa",
            1,
            "Archived",
            published_at=NOW,
            media_ext="m4a",
            media_mime="audio/mp4",
            public_ext="mp3",
        ),
        item(
            "bbbbbbbbbbbbbbbb",
            2,
            "Pending",
            state=ArchiveState.available,
            media_ext=None,  # type: ignore[arg-type]
            media_mime=None,  # type: ignore[arg-type]
            media_bytes=0,
            public_ext="m4a",
            published_at=NOW - timedelta(days=1),
        ),
        item(
            "cccccccccccccccc",
            3,
            "Gone",
            state=ArchiveState.deleted,
            media_ext=None,  # type: ignore[arg-type]
            media_mime=None,  # type: ignore[arg-type]
            media_bytes=0,
            public_ext="m4a",
            published_at=NOW - timedelta(days=2),
        ),
        item("dddddddddddddddd", 4, "No media", state=ArchiveState.available, archivable=False),
    ]
    rendered = render_feed(feed, items, base_url=BASE_URL, artwork_secret=ART_SECRET, now=NOW)
    enclosures = etree.fromstring(rendered.body).findall(".//item/enclosure")
    # The archived one, the pending one and the Tombstone (downloaded again on request);
    # every URL ends with the row's public_ext and the type follows it until the file
    # exists; unarchived items claim a plausible size (128 kbit/s) since apps reject 0 bytes.
    media = f"{BASE_URL}/feeds/{FEED_ID}/media"
    assert [(e.get("url"), e.get("type"), e.get("length")) for e in enclosures] == [
        (f"{media}/aaaaaaaaaaaaaaaa.mp3", "audio/mp4", "5155"),
        (f"{media}/bbbbbbbbbbbbbbbb.m4a", "audio/mp4", "16000"),
        (f"{media}/cccccccccccccccc.m4a", "audio/mp4", "16000"),
    ]
    assert placeholder_length(None) == 16_000 and placeholder_length(3600) == 57_600_000
    # An RSS Source's unarchived item is advertised as its enclosure's type predicted.
    rss = render_feed(
        FeedView(
            id=FEED_ID,
            kind=FeedKind.mirror,
            title="RSS",
            source_kind=SourceKind.rss,
            backfill_mode=BackfillMode.automatic,
        ),
        [
            item(
                "eeeeeeeeeeeeeeee",
                5,
                "Pending",
                state=ArchiveState.available,
                media_ext=None,  # type: ignore[arg-type]
                media_mime=None,  # type: ignore[arg-type]
                media_bytes=0,
                public_ext="ogg",
                duration_seconds=10,
            )
        ],
        base_url=BASE_URL,
        artwork_secret=ART_SECRET,
        now=NOW,
    )
    (pending,) = etree.fromstring(rss.body).findall(".//item/enclosure")
    assert (pending.get("url"), pending.get("type"), pending.get("length")) == (
        f"{media}/eeeeeeeeeeeeeeee.ogg",
        "audio/ogg",
        "160000",
    )
    # The same items under any other mode: only the archived one.
    plain = render_feed(
        FeedView(id=FEED_ID, kind=FeedKind.mirror, title="x"),
        items,
        base_url=BASE_URL,
        artwork_secret=ART_SECRET,
        now=NOW,
    )
    assert len(etree.fromstring(plain.body).findall(".//item")) == 1


def test_order_is_published_desc_nulls_last_then_ordinal_desc() -> None:
    feed = FeedView(id="f", kind=FeedKind.bucket, title="F")
    items = [
        item("a", 1, "a", published_at=NOW - timedelta(days=2)),
        item("b", 2, "b", published_at=None),
        item("c", 3, "c", published_at=NOW),
        item("d", 4, "d", published_at=NOW),
        item("e", 5, "e", published_at=None),
    ]
    rendered = render_feed(feed, items, base_url=BASE_URL, artwork_secret=ART_SECRET, now=NOW)
    _, channel = channel_of(rendered)
    assert [i.findtext("title") for i in channel.findall("item")] == ["d", "c", "a", "e", "b"]


def test_synthesized_channel_falls_back_when_the_source_xml_is_unusable() -> None:
    feed = FeedView(
        id="f",
        kind=FeedKind.mirror,
        title="Broken",
        source_kind=SourceKind.rss,
        source_channel_xml="<rss><nochannel/></rss>",
        language="fr",
    )
    rendered = render_feed(
        feed,
        [item("x", 1, "x", published_at=NOW, source_item_xml="<not-an-item/>")],
        base_url=BASE_URL,
        artwork_secret=ART_SECRET,
        now=NOW,
    )
    _, channel = channel_of(rendered)
    assert channel.findtext("title") == "Broken" and channel.findtext("language") == "fr"
    assert channel.find("item/enclosure") is not None
    garbage = FeedView(
        id="f",
        kind=FeedKind.mirror,
        title="G",
        source_kind=SourceKind.rss,
        source_channel_xml="<<<",
    )
    assert render_feed(garbage, [], base_url=BASE_URL, artwork_secret=ART_SECRET, now=NOW).body


def test_channel_only_source_xml_and_conflicting_prefixes_are_handled() -> None:
    channel_xml_text = (
        '<channel xmlns:atom="urn:other" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<title>Chan</title><link>https://c.example/</link><description>d</description>"
        '<atom:link rel="self" href="https://elsewhere"/><itunes:image href="https://c.example/a.jpg"/>'
        "<generator>Old</generator><lastBuildDate>never</lastBuildDate></channel>"
    )
    feed = FeedView(
        id="f",
        kind=FeedKind.mirror,
        title="Chan",
        source_kind=SourceKind.rss,
        source_channel_xml=channel_xml_text,
        last_modified=NOW,
    )
    rendered = render_feed(feed, [], base_url=BASE_URL, artwork_secret=ART_SECRET, now=NOW)
    root, channel = channel_of(rendered)
    assert root.tag == "rss" and root.get("version") == "2.0"
    assert channel.findtext("generator") == GENERATOR
    assert channel.findtext("lastBuildDate") == rfc2822(NOW)
    self_links = channel.findall(tag(NS_ATOM, "link"))
    assert [link.get("href") for link in self_links] == [f"{BASE_URL}/feeds/f.xml"]
    assert channel.find("{urn:other}link").get("href") == "https://elsewhere"  # type: ignore[union-attr]
    assert etree.QName(self_links[0]).namespace == NS_ATOM


def test_body_is_utf8_xml_with_declaration(rich: ParsedFeed) -> None:
    rendered = render_feed(
        rss_feed(rich),
        rss_items(rich, ["urn:fixture:episode:4"]),
        base_url=BASE_URL,
        artwork_secret=ART_SECRET,
        now=NOW,
    )
    assert rendered.body.startswith(b"<?xml version='1.0' encoding='UTF-8'?>")
    assert "—".encode() in rendered.body
    assert rendered.last_modified.tzinfo is UTC


def test_rfc2822_is_always_gmt() -> None:
    assert (
        rfc2822(datetime(2024, 1, 15, 8, 0, 0, 123, tzinfo=UTC)) == "Mon, 15 Jan 2024 08:00:00 GMT"
    )
    assert rfc2822(datetime(2024, 1, 15, 9)) == "Mon, 15 Jan 2024 09:00:00 GMT"
    from datetime import timezone

    assert (
        rfc2822(datetime(2024, 1, 15, 9, tzinfo=timezone(timedelta(hours=1))))
        == "Mon, 15 Jan 2024 08:00:00 GMT"
    )


def test_show_notes_point_at_the_mirrored_attachments() -> None:
    attachment = AssetView(
        kind=AssetKind.attachment,
        local_path="assets/aaaaaaaaaaaaaaaa.attachment.0123456789ab.jpg",
        mime="image/jpeg",
        remote_url="https://cdn.example/pic.jpg",
    )
    missing = AssetView(
        kind=AssetKind.attachment,
        local_path=None,
        state=AssetState.failed,
        remote_url="https://cdn.example/gone.pdf",
    )
    notes = '<p><img src="https://cdn.example/pic.jpg"> <a href="https://cdn.example/gone.pdf">x</a></p>'
    feed = FeedView(id=FEED_ID, kind=FeedKind.mirror, title="Notes", source_kind=SourceKind.ytdlp)
    items = [
        item(
            "aaaaaaaaaaaaaaaa",
            1,
            "With pictures",
            published_at=NOW,
            description=notes,
            assets=(attachment, missing),
        )
    ]
    rendered = render_feed(feed, items, base_url=BASE_URL, artwork_secret=ART_SECRET, now=NOW)
    description = etree.fromstring(rendered.body).findtext(".//item/description") or ""
    local = f"{BASE_URL}/feeds/{FEED_ID}/assets/aaaaaaaaaaaaaaaa.attachment.0123456789ab.jpg"
    assert local in description
    assert "https://cdn.example/pic.jpg" not in description
    assert "https://cdn.example/gone.pdf" in description  # not mirrored: the original stays


def test_approximate_dates_are_said_in_the_show_notes() -> None:
    from copycast.adapters.feeds.render import approximate_phrase

    assert approximate_phrase(NOW - timedelta(days=22), NOW) == "3 weeks ago"
    assert approximate_phrase(NOW - timedelta(days=400), NOW) == "1 year ago"
    assert approximate_phrase(NOW - timedelta(hours=5), NOW) == "5 hours ago"
    assert approximate_phrase(NOW, NOW) == "today"
    feed = FeedView(id=FEED_ID, kind=FeedKind.mirror, title="Rough", source_kind=SourceKind.ytdlp)
    items = [
        item(
            "aaaaaaaaaaaaaaaa",
            1,
            "Roughly dated",
            state=ArchiveState.available,
            media_ext=None,  # type: ignore[arg-type]
            media_mime=None,  # type: ignore[arg-type]
            media_bytes=0,
            published_at=NOW - timedelta(days=22),
            published_at_approximate=True,
            description="Notes.",
        ),
        item("bbbbbbbbbbbbbbbb", 2, "Exactly dated", published_at=NOW, description="Notes."),
    ]
    feed = FeedView(
        id=FEED_ID,
        kind=FeedKind.mirror,
        title="Rough",
        source_kind=SourceKind.ytdlp,
        backfill_mode=BackfillMode.automatic,
    )
    rendered = render_feed(feed, items, base_url=BASE_URL, artwork_secret=ART_SECRET, now=NOW)
    descriptions = [
        e.findtext("description") or "" for e in etree.fromstring(rendered.body).findall(".//item")
    ]
    assert descriptions[0].startswith("Notes.")
    note = "Published about 3 weeks ago (approximate date from YouTube; exact once downloaded)."
    assert descriptions[1].startswith(f"{note}\n\nNotes.")


# --------------------------------------------------------------------------- 2.3 hardening


def _blocks(channel: Any) -> list[tuple[str | None, str | None]]:
    return [
        (etree.QName(child).namespace, child.text)
        for child in channel
        if isinstance(child.tag, str) and etree.QName(child).localname == "block"
    ]


def test_every_feed_asks_directories_not_to_list_it(rich: ParsedFeed) -> None:
    """Synthesized and preserved channels alike carry one itunes:block and one podcast:block,
    with the podcast namespace declared."""
    synthesized = render_feed(
        FeedView(id=FEED_ID, kind=FeedKind.mirror, title="Tube", source_kind=SourceKind.ytdlp),
        [item("v1", 1, "v", published_at=NOW)],
        base_url=BASE_URL,
        artwork_secret=ART_SECRET,
        now=NOW,
    )
    bucket = render_feed(
        FeedView(id="copycast-abc234", kind=FeedKind.bucket, title="Copycast"),
        [],
        base_url=BASE_URL,
        artwork_secret=ART_SECRET,
        now=NOW,
    )
    preserved = render_feed(
        rss_feed(rich), [], base_url=BASE_URL, artwork_secret=ART_SECRET, now=NOW
    )
    for rendered in (synthesized, bucket, preserved):
        root, channel = channel_of(rendered)
        assert root.nsmap["podcast"] == NS_PODCAST
        assert _blocks(channel) == [(NS_ITUNES, "Yes"), (NS_PODCAST, "yes")]


def test_a_sources_own_block_tags_are_replaced_never_duplicated() -> None:
    """A Source that says ``No`` (or blocks under the legacy namespace URIs) gets Copycast's
    blocks instead: one of each, never two."""
    channel_xml_text = (
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
        'xmlns:podcast="https://podcastindex.org/namespace/1.0" '
        'xmlns:old="https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/1.0.md" '
        'xmlns:caps="http://www.itunes.com/DTDs/PodCast-1.0.dtd"><channel>'
        "<title>Open</title><link>https://o.example/</link><description>d</description>"
        "<itunes:block>No</itunes:block><podcast:block>no</podcast:block>"
        '<podcast:block id="spotify">no</podcast:block><old:block>no</old:block>'
        "<caps:block>No</caps:block><other>kept</other></channel></rss>"
    )
    feed = FeedView(
        id="f",
        kind=FeedKind.mirror,
        title="Open",
        source_kind=SourceKind.rss,
        source_channel_xml=channel_xml_text,
        last_modified=NOW,
    )
    rendered = render_feed(feed, [], base_url=BASE_URL, artwork_secret=ART_SECRET, now=NOW)
    _, channel = channel_of(rendered)
    assert _blocks(channel) == [(NS_ITUNES, "Yes"), (NS_PODCAST, "yes")]
    assert channel.findtext("other") == "kept"


def test_artwork_moves_to_art_and_nothing_else_moves(rich: ParsedFeed) -> None:
    """Only image URLs change in 2.3: enclosures, chapters, transcripts and attachments keep
    the exact URLs 2.2 published, with and without a media key, so no podcast app downloads
    an Episode again. Artwork is the same /art/ URL in both auth modes."""
    attachment = AssetView(
        kind=AssetKind.attachment,
        local_path="assets/i6.attachment.0123456789ab.pdf",
        remote_url="https://podcast.example/notes.pdf",
    )
    assets = (
        chapters("i6"),
        transcript("en", AssetFormat.vtt, item="i6"),
        attachment,
        artwork("i6"),
    )
    key = "k" * 22
    expected = {
        None: f"{BASE_URL}/feeds/{FEED_ID}",
        key: f"{BASE_URL}/feeds/{FEED_ID}/k/{key}",
    }
    images: set[str] = set()
    for media_key, root_url in expected.items():
        feed = FeedView(
            id=FEED_ID,
            kind=FeedKind.mirror,
            title="Tube",
            source_kind=SourceKind.ytdlp,
            assets=(artwork(None),),
            media_key=media_key,
        )
        rendered = render_feed(
            feed,
            [
                item(
                    "i6",
                    6,
                    "Six",
                    published_at=NOW,
                    media_ext="m4a",
                    public_ext="mp3",
                    description="see https://podcast.example/notes.pdf",
                    assets=assets,
                )
            ],
            base_url=BASE_URL,
            artwork_secret=ART_SECRET,
            now=NOW,
        )
        _, channel = channel_of(rendered)
        (element,) = channel.findall("item")
        enclosure = element.find("enclosure")
        assert enclosure is not None and enclosure.get("url") == f"{root_url}/media/i6.mp3"
        chapter = element.find(tag(NS_PODCAST, "chapters"))
        assert chapter is not None
        assert chapter.get("url") == f"{root_url}/assets/i6.chapters.json"
        captions = element.find(tag(NS_PODCAST, "transcript"))
        assert captions is not None
        assert captions.get("url") == f"{root_url}/assets/i6.transcript.en.mirrored.vtt"
        assert f"{root_url}/assets/i6.attachment.0123456789ab.pdf" in (
            element.findtext("description") or ""
        )
        item_image = element.find(tag(NS_ITUNES, "image"))
        feed_image = channel.find(tag(NS_ITUNES, "image"))
        assert item_image is not None and feed_image is not None
        assert item_image.get("href") == art_url("i6")
        assert feed_image.get("href") == art_url(None)
        images.update({item_image.get("href", ""), feed_image.get("href", "")})
        # Neither the feed id nor the item id names the image any more.
        assert f"{FEED_ID}/assets/feed.artwork" not in rendered.body.decode()
        assert "i6.artwork" not in rendered.body.decode()
    assert images == {art_url("i6"), art_url(None)}


def test_the_artwork_token_depends_on_the_instance_secret() -> None:
    feed = FeedView(
        id=FEED_ID,
        kind=FeedKind.mirror,
        title="Tube",
        source_kind=SourceKind.ytdlp,
        assets=(artwork(None),),
    )
    one = render_feed(feed, [], base_url=BASE_URL, artwork_secret=ART_SECRET, now=NOW)
    other = render_feed(feed, [], base_url=BASE_URL, artwork_secret=b"\x08" * 32, now=NOW)
    image = channel_of(one)[1].find(tag(NS_ITUNES, "image"))
    moved = channel_of(other)[1].find(tag(NS_ITUNES, "image"))
    assert image is not None and moved is not None
    assert image.get("href") == art_url(None) != moved.get("href")
    assert str(moved.get("href")).startswith(f"{BASE_URL}/art/{art_id(None)}-")


def test_artwork_without_an_asset_id_is_not_published_locally() -> None:
    """The /art/ token is the asset id: a view without one falls back to the Source's image."""
    nameless = AssetView(kind=AssetKind.artwork, local_path="assets/feed.artwork.jpg")
    feed = FeedView(
        id=FEED_ID,
        kind=FeedKind.mirror,
        title="Tube",
        source_kind=SourceKind.ytdlp,
        artwork_url="https://cdn.example/cover.jpg",
        assets=(nameless,),
    )
    rendered = render_feed(feed, [], base_url=BASE_URL, artwork_secret=ART_SECRET, now=NOW)
    image = channel_of(rendered)[1].find(tag(NS_ITUNES, "image"))
    assert image is not None and image.get("href") == "https://cdn.example/cover.jpg"
