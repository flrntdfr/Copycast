from __future__ import annotations

from copycast.adapters.feeds.urls import asset_url, feed_url, media_url
from tests.support.paths import ASSET_URL, FEED_URL, MEDIA_URL

BASE = "http://testserver"


def test_urls_match_the_test_paths() -> None:
    assert feed_url(BASE, "abc") == BASE + FEED_URL("abc")
    assert media_url(BASE, "abc", "0123456789abcdef", "m4a") == BASE + MEDIA_URL(
        "abc", "0123456789abcdef", "m4a"
    )
    assert asset_url(BASE, "abc", "assets/feed.artwork.jpg") == BASE + ASSET_URL(
        "abc", "feed.artwork.jpg"
    )


def test_base_url_trailing_slash_and_ext_dot_are_tolerated() -> None:
    assert feed_url(BASE + "/", "abc") == f"{BASE}/feeds/abc.xml"
    assert media_url(BASE + "/", "abc", "x", ".mp3") == f"{BASE}/feeds/abc/media/x.mp3"
    assert (
        asset_url(BASE, "abc", "x.transcript.en.mirrored.vtt")
        == f"{BASE}/feeds/abc/assets/x.transcript.en.mirrored.vtt"
    )


def test_path_components_are_quoted() -> None:
    assert asset_url(BASE, "a b", "we ird.jpg") == f"{BASE}/feeds/a%20b/assets/we%20ird.jpg"
    assert media_url(BASE, "id", "it/em", "m4a") == f"{BASE}/feeds/id/media/it%2Fem.m4a"
