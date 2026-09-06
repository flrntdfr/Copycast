from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from copycast.adapters.sources.cache import TtlCache
from copycast.adapters.sources.http import SourceRejected, SourceUnreachable
from copycast.adapters.sources.itunes_search import (
    SEARCH_URL,
    cache_key,
    parse_results,
    search_podcasts,
)
from copycast.application.models import PodcastSearchResult


@pytest.fixture
def payload(fixtures_dir: Path) -> dict[str, object]:
    return json.loads((fixtures_dir / "itunes/search.json").read_text())


def _cache() -> TtlCache[tuple[str, int], list[PodcastSearchResult]]:
    return TtlCache(600)


@respx.mock
def test_search_maps_results_and_drops_entries_without_feed_url(payload: dict[str, object]) -> None:
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))
    results = search_podcasts("  Copycast   test ", limit=10, cache=_cache())
    assert route.called
    request = route.calls.last.request
    params = dict(httpx.QueryParams(request.url.query))
    assert params == {
        "media": "podcast",
        "entity": "podcast",
        "limit": "10",
        "term": "copycast test",
    }
    assert [r.title for r in results] == ["Copycast Test Podcast", "Large Fixture Podcast"]
    first = results[0]
    assert first.author == "Fixture Media"
    assert first.feed_url == "https://podcast.example/feed.xml"
    assert first.artwork_url == "https://is1-ssl.mzstatic.example/image/thumb/fixture/600x600bb.jpg"
    assert first.itunes_id == 617416468
    assert first.episode_count == 6
    assert first.genre == "Technology"
    assert first.latest_release_at == datetime(2024, 1, 15, 8, tzinfo=UTC)


@respx.mock
def test_search_is_cached_for_ten_minutes(payload: dict[str, object]) -> None:
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))
    cache = _cache()
    search_podcasts("copycast", cache=cache)
    search_podcasts("Copycast", cache=cache)
    assert route.call_count == 1
    search_podcasts("copycast", limit=5, cache=cache)
    assert route.call_count == 2
    assert cache_key("  A  b ", 3) == ("a b", 3)


@respx.mock
def test_limit_is_clamped_and_applied(payload: dict[str, object]) -> None:
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))
    results = search_podcasts("x", limit=1, cache=_cache())
    assert len(results) == 1
    assert dict(httpx.QueryParams(route.calls.last.request.url.query))["limit"] == "1"
    search_podcasts("y", limit=500, cache=_cache())
    assert dict(httpx.QueryParams(route.calls.last.request.url.query))["limit"] == "50"


@respx.mock
def test_errors_are_source_errors() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(SourceUnreachable):
        search_podcasts("x", cache=_cache())
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(403))
    with pytest.raises(SourceRejected):
        search_podcasts("x", cache=_cache())
    respx.get(SEARCH_URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    with pytest.raises(SourceUnreachable):
        search_podcasts("x", cache=_cache())
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, content=b"not json"))
    with pytest.raises(SourceUnreachable):
        search_podcasts("x", cache=_cache())


def test_blank_query_returns_nothing_without_a_request() -> None:
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(SEARCH_URL)
        assert search_podcasts("   ", cache=_cache()) == []
        assert not route.called


def test_parse_results_is_lenient() -> None:
    assert parse_results("junk") == []
    assert parse_results({"results": "junk"}) == []
    assert parse_results({"results": [1, {"feedUrl": " "}]}) == []
    only = parse_results(
        {"results": [{"feedUrl": "https://f", "trackName": "T", "genres": ["G"], "trackId": 7}]}
    )
    assert only[0].title == "T" and only[0].genre == "G" and only[0].itunes_id == 7
    assert only[0].latest_release_at is None and only[0].episode_count is None


@respx.mock
def test_explicit_client_is_used(payload: dict[str, object]) -> None:
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=payload))
    with httpx.Client() as client:
        results = search_podcasts("x", client=client, cache=_cache())
    assert route.called and len(results) == 2
