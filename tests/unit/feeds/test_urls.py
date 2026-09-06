from __future__ import annotations

from copycast.adapters.feeds.urls import asset_url, feed_url, media_url, with_credentials
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


def test_feed_url_carries_a_basic_pair_in_the_netloc() -> None:
    assert feed_url(BASE, "abc", username="k7mpq2xz", password="s3cret") == (
        "http://k7mpq2xz:s3cret@testserver/feeds/abc.xml"
    )
    # Half a pair is no pair.
    assert feed_url(BASE, "abc", username="k7mpq2xz") == f"{BASE}/feeds/abc.xml"
    assert feed_url("https://copycast.example:8443/", "abc", username="u", password="p") == (
        "https://u:p@copycast.example:8443/feeds/abc.xml"
    )


def test_with_credentials_quotes_and_keeps_ipv6_hosts() -> None:
    assert with_credentials("http://[::1]:8080/feeds/a.xml?x=1", "u@x", "p:q") == (
        "http://u%40x:p%3Aq@[::1]:8080/feeds/a.xml?x=1"
    )
