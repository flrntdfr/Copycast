from __future__ import annotations

import pytest

from copycast.adapters.sources.probe import (
    PROBE_TTL_SECONDS,
    ProbeCache,
    ProbeEntry,
    probe,
    rss_candidate,
)
from copycast.application.ports import PermanentError, TransientError
from copycast.domain.enums import SourceKind
from copycast.domain.exceptions import Unsupported
from tests.support.factories import listing
from tests.support.fake_engine import FakeEngine
from tests.support.origin import Origin


def test_rss_url_is_one_candidate_with_the_listing_cached(
    origin: Origin, engine: FakeEngine
) -> None:
    cache = ProbeCache()
    url = origin.url_for("/rss/itunes_podcast20.xml")
    result = probe(url, engine=engine, cache=cache)
    assert result.input_url == url
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.source_kind is SourceKind.rss
    assert candidate.source_url == url
    assert candidate.service == "RSS"
    assert candidate.title == "Copycast Test Podcast"
    assert candidate.author == "Fixture Media"
    assert candidate.artwork_url == "https://podcast.example/artwork.jpg"
    assert candidate.item_count == 6
    assert candidate.candidate_token
    entry = cache.get_by_token(candidate.candidate_token)
    assert entry is not None and entry.listing.title == "Copycast Test Podcast"
    assert entry.feed is not None and entry.feed.parsed is not None and entry.feed.body
    assert cache.get_by_url(url + "/") is entry
    assert cache.get(token="nope", url=url) is entry
    assert engine.records.listings == []


def test_html_page_yields_every_verified_feed_deduped(origin: Origin, engine: FakeEngine) -> None:
    url = origin.url_for("/html/two_feeds.html")
    result = probe(url, engine=engine)
    urls = [c.source_url for c in result.candidates]
    assert urls == [origin.url_for("/rss/itunes_podcast20.xml")]  # bonus.example is unreachable
    assert result.candidates[0].source_kind is SourceKind.rss
    assert origin.hits("/rss/itunes_podcast20.xml") == 1
    assert origin.hits("/rss/atom.xml") == 0


def test_html_verification_is_capped(origin: Origin, engine: FakeEngine) -> None:
    links = "".join(
        f'<link rel="alternate" type="application/rss+xml" href="/rss/itunes_podcast20.xml?n={n}">'
        for n in range(8)
    )
    origin.script("/page", body=f"<html><head>{links}</head></html>", content_type="text/html")
    result = probe(origin.url_for("/page"), engine=engine, verify_limit=3)
    assert len(result.candidates) == 3
    assert origin.hits("/rss/itunes_podcast20.xml") == 3


def test_page_without_feeds_falls_back_to_the_engine(origin: Origin, engine: FakeEngine) -> None:
    url = origin.url_for("/page")
    origin.script("/page", body="<html><body>no feeds</body></html>", content_type="text/html")
    engine.script_listing(url, listing(2, service="SoundCloud", title="A set"))
    result = probe(url, engine=engine)
    assert [c.source_kind for c in result.candidates] == [SourceKind.ytdlp]
    assert result.candidates[0].title == "A set"
    assert result.candidates[0].service == "SoundCloud"
    assert result.candidates[0].item_count == 2
    assert engine.records.listed_urls == [url]


def test_unreachable_host_tries_both_schemes_then_the_engine(engine: FakeEngine) -> None:
    engine.script_listing("https://127.0.0.1:9/x", listing(1))
    result = probe("127.0.0.1:9/x", engine=engine)
    assert result.input_url == "127.0.0.1:9/x"
    assert result.candidates[0].source_url == "https://127.0.0.1:9/x"
    assert engine.records.listed_urls == ["https://127.0.0.1:9/x"]


def test_nothing_fits_is_unsupported(engine: FakeEngine) -> None:
    engine.fail_next(PermanentError("Unsupported URL"))
    engine.fail_next(TransientError("timeout"))
    with pytest.raises(Unsupported) as excinfo:
        probe("http://127.0.0.1:9/nothing", engine=engine)
    assert "Unsupported URL" in str(excinfo.value)
    assert excinfo.value.slug == "source-unsupported"
    with pytest.raises(Unsupported):
        probe("   ", engine=engine)


def test_media_url_is_refused_with_an_inbox_hint(origin: Origin, engine: FakeEngine) -> None:
    with pytest.raises(Unsupported) as excinfo:
        probe(origin.url_for("/media/tiny.mp3"), engine=engine)
    assert "Inbox" in str(excinfo.value)


def test_bare_youtube_channel_normalizes_to_videos_tab(engine: FakeEngine) -> None:
    videos = "https://www.youtube.com/@fixturechannel/videos"
    engine.script_listing(videos, listing(4, service="YouTube", title="Fixture Channel - Videos"))
    result = probe("https://www.youtube.com/@fixturechannel", engine=engine)
    assert len(result.candidates) == 1
    assert result.candidates[0].source_url == videos
    assert result.candidates[0].source_kind is SourceKind.ytdlp
    assert engine.records.listed_urls == [videos]
    shorts = "https://www.youtube.com/@fixturechannel/shorts"
    engine.script_listing(shorts, listing(1, service="YouTube"))
    assert probe(shorts, engine=engine).candidates[0].source_url == shorts


def test_engine_options_and_cancel_token_reach_the_engine(engine: FakeEngine) -> None:
    from copycast.application.ports import CancelToken

    url = "https://soundcloud.com/someone/sets/x"
    engine.script_listing(url, listing(1))
    token = CancelToken()
    probe(url, engine=engine, options={"proxy": "http://p"}, cancel=token)
    assert engine.records.listings[0].options == {"proxy": "http://p"}


def test_probe_cache_expires_and_keys_by_normalized_url() -> None:
    now = [1000.0]
    cache = ProbeCache(ttl=PROBE_TTL_SECONDS, clock=lambda: now[0])
    candidate = rss_candidate("https://WWW.Podcast.example/feed.xml?utm_source=x&b=1", listing(1))
    cache.put(ProbeEntry(candidate, listing(1)))
    assert len(cache) == 1
    assert cache.get_by_url("http://podcast.example/feed.xml/?b=1") is not None
    assert cache.get_by_token(candidate.candidate_token) is not None
    assert cache.get_by_token(None) is None and cache.get_by_url(None) is None
    now[0] += PROBE_TTL_SECONDS + 1
    assert cache.get_by_token(candidate.candidate_token) is None
    assert cache.get_by_url(candidate.source_url) is None
    cache.purge()
    assert len(cache) == 0


def test_apple_podcasts_link_resolves_to_its_feed(origin: Origin, engine: FakeEngine) -> None:
    import httpx

    feed_url = origin.url_for("/rss/itunes_podcast20.xml")

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "itunes.apple.com":
            return httpx.Response(200, json={"results": [{"feedUrl": feed_url}]})
        return httpx.Response(200, content=httpx.get(str(request.url)).content)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = probe(
            "https://podcasts.apple.com/us/podcast/copycast-test/id617416468",
            engine=engine,
            client=client,
        )
    assert [c.source_url for c in result.candidates] == [feed_url]
    assert result.candidates[0].source_kind is SourceKind.rss
    assert result.candidates[0].title == "Copycast Test Podcast"
    assert engine.records.listings == []

    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"results": []}))
        ) as client,
        pytest.raises(Unsupported, match="could not be resolved"),
    ):
        probe("https://podcasts.apple.com/us/podcast/x/id1", engine=engine, client=client)
