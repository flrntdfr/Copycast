from __future__ import annotations

import json

import httpx
import pytest

from copycast.adapters.sources.directories import (
    LOOKUP_URL,
    apple_show_id,
    is_directory_link,
    resolve_directory_link,
    spotify_show_title,
    spotify_show_url,
)
from copycast.adapters.sources.http import SourceError
from copycast.adapters.sources.itunes_search import SEARCH_URL, clear_cache


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://podcasts.apple.com/us/podcast/accidental-tech-podcast/id617416468", "617416468"),
        ("podcasts.apple.com/fr/podcast/id617416468?i=1000", "617416468"),
        ("https://podcasts.apple.com/us/podcast/some-show", None),
        ("https://music.apple.com/us/album/id123456", None),
    ],
)
def test_apple_show_id(url: str, expected: str | None) -> None:
    assert apple_show_id(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://open.spotify.com/show/6kAsbP8pxwaU2kPibKTuHE?si=x",
            "https://open.spotify.com/show/6kAsbP8pxwaU2kPibKTuHE",
        ),
        ("open.spotify.com/intl-fr/show/abc", "https://open.spotify.com/show/abc"),
        ("https://open.spotify.com/episode/xyz", None),
        ("https://example.com/show/abc", None),
    ],
)
def test_spotify_show_url(url: str, expected: str | None) -> None:
    assert spotify_show_url(url) == expected
    assert is_directory_link(url) is (expected is not None)


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


def test_apple_link_resolves_through_the_lookup_api() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert request.url.host == "itunes.apple.com" and request.url.params["id"] == "617416468"
        return httpx.Response(
            200, json={"results": [{"feedUrl": "https://atp.fm/rss", "collectionName": "ATP"}]}
        )

    with _client(httpx.MockTransport(handle)) as client:
        assert resolve_directory_link(
            "https://podcasts.apple.com/us/podcast/atp/id617416468", client=client
        ) == ["https://atp.fm/rss"]
    assert seen and seen[0].startswith(LOOKUP_URL)

    with _client(httpx.MockTransport(lambda _: httpx.Response(200, json={"results": []}))) as c:
        assert resolve_directory_link("https://podcasts.apple.com/us/podcast/x/id1", client=c) == []
    with (
        _client(httpx.MockTransport(lambda _: httpx.Response(503))) as c,
        pytest.raises(SourceError),
    ):
        resolve_directory_link("https://podcasts.apple.com/us/podcast/x/id1", client=c)


def test_spotify_link_resolves_by_show_name() -> None:
    clear_cache()
    page = (
        '<html><head><meta property="og:title" '
        'content="Accidental Tech Podcast | Podcast on Spotify"></head></html>'
    )
    hits = {
        "resultCount": 2,
        "results": [
            {
                "collectionName": "accidental tech podcast",
                "feedUrl": "https://atp.fm/rss",
                "kind": "podcast",
            },
            {
                "collectionName": "ATP Fan Show",
                "feedUrl": "https://fans.example/rss",
                "kind": "podcast",
            },
        ],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "open.spotify.com":
            return httpx.Response(200, text=page)
        assert str(request.url).startswith(SEARCH_URL)
        assert request.url.params["term"].casefold() == "accidental tech podcast"
        return httpx.Response(200, text=json.dumps(hits))

    with _client(httpx.MockTransport(handle)) as client:
        assert spotify_show_title("https://open.spotify.com/show/abc", client=client) == (
            "Accidental Tech Podcast"
        )
        # An exact (case-insensitive) name match wins outright.
        assert resolve_directory_link("https://open.spotify.com/show/abc", client=client) == [
            "https://atp.fm/rss"
        ]
    clear_cache()
    loose = {**hits, "results": [dict(hits["results"][1], collectionName="Something Else")]}

    def no_exact(request: httpx.Request) -> httpx.Response:
        if request.url.host == "open.spotify.com":
            return httpx.Response(200, text=page)
        return httpx.Response(200, text=json.dumps(loose))

    with _client(httpx.MockTransport(no_exact)) as client:
        # Otherwise every hit is a candidate for the person to pick from.
        assert resolve_directory_link("https://open.spotify.com/show/abc", client=client) == [
            "https://fans.example/rss"
        ]
    assert resolve_directory_link("https://example.com/feed.xml") is None
