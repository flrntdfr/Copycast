"""normalize_source_url carries the Java Urls.dedupKey table verbatim."""

from __future__ import annotations

import pytest

from copycast.domain.urls import (
    has_scheme,
    normalize_source_url,
    normalize_youtube_channel,
    scheme_candidates,
    youtube_tab,
)

CANONICAL = "atp.fm/rss"


@pytest.mark.parametrize(
    "url",
    [
        "https://atp.fm/rss",
        "http://atp.fm/rss",
        "https://www.atp.fm/rss",
        "https://ATP.FM/rss",
        "https://atp.fm/rss/",
        "https://atp.fm/rss///",
        "  https://atp.fm/rss  ",
        "https://user:pw@atp.fm:443/rss",
        "https://atp.fm/rss#fragment",
        "https://atp.fm/rss?",
        "https://atp.fm/rss?&&",
    ],
)
def test_collapses_scheme_www_trailing_slash_host_case_port_userinfo_fragment(url: str) -> None:
    assert normalize_source_url(url) == CANONICAL


def test_drops_tracking_params_but_keeps_meaningful_ones() -> None:
    assert normalize_source_url("https://atp.fm/rss?utm_source=x&fbclid=y") == CANONICAL
    assert normalize_source_url("https://pod.example/rss?token=a") != normalize_source_url(
        "https://pod.example/rss?token=b"
    )
    assert (
        normalize_source_url("https://pod.example/rss?token=a&utm_medium=email")
        == "pod.example/rss?token=a"
    )


@pytest.mark.parametrize(
    "name",
    [
        "utm_campaign",
        "UTM_SOURCE",
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "ref",
        "ref_src",
    ],
)
def test_every_tracking_parameter_is_dropped(name: str) -> None:
    assert (
        normalize_source_url(f"https://pod.example/rss?{name}=1&keep=2") == "pod.example/rss?keep=2"
    )


def test_query_parameter_order_is_canonical() -> None:
    assert normalize_source_url("https://pod.example/rss?a=1&b=2") == normalize_source_url(
        "https://pod.example/rss?b=2&a=1"
    )
    assert normalize_source_url("https://pod.example/rss?b=2&a=1") == "pod.example/rss?a=1&b=2"


def test_query_values_are_kept_raw() -> None:
    assert (
        normalize_source_url("https://pod.example/rss?q=a%20b&flag")
        == "pod.example/rss?flag&q=a%20b"
    )


def test_distinct_feeds_stay_distinct() -> None:
    assert normalize_source_url("https://atp.fm/rss") != normalize_source_url("https://atp.fm/rss2")
    assert normalize_source_url("https://a.example/rss") != normalize_source_url(
        "https://b.example/rss"
    )


def test_path_case_is_preserved() -> None:
    assert normalize_source_url("https://atp.fm/RSS") == "atp.fm/RSS"
    assert normalize_source_url("https://atp.fm/RSS") != CANONICAL


def test_root_path_is_preserved() -> None:
    assert normalize_source_url("https://atp.fm/") == "atp.fm"
    assert normalize_source_url("https://atp.fm") == "atp.fm"


def test_only_one_leading_www_is_removed() -> None:
    assert normalize_source_url("https://www.www.example.com/") == "www.example.com"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Not A URL", "not a url"),
        ("  MIXED case no host  ", "mixed case no host"),
        ("mailto:Someone@Example.com", "mailto:someone@example.com"),
        ("http://[bad", "http://[bad"),
        ("", ""),
    ],
)
def test_inputs_without_a_host_fall_back_to_lowercased_input(raw: str, expected: str) -> None:
    assert normalize_source_url(raw) == expected


def test_none_is_empty() -> None:
    assert normalize_source_url(None) == ""


def test_scheme_candidates() -> None:
    assert scheme_candidates("https://a.example/x") == ["https://a.example/x"]
    assert scheme_candidates(" a.example/x ") == ["https://a.example/x", "http://a.example/x"]
    assert scheme_candidates("//a.example") == ["https://a.example", "http://a.example"]
    assert has_scheme("ftp://x") and not has_scheme("x.example")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/@handle", "https://www.youtube.com/@handle/videos"),
        ("https://www.youtube.com/@handle/", "https://www.youtube.com/@handle/videos"),
        ("https://youtube.com/channel/UCabc", "https://youtube.com/channel/UCabc/videos"),
        ("https://m.youtube.com/c/Name?x=1", "https://m.youtube.com/c/Name/videos"),
        ("https://www.youtube.com/user/Name#top", "https://www.youtube.com/user/Name/videos"),
        ("https://www.youtube.com/@handle/shorts", "https://www.youtube.com/@handle/shorts"),
        ("https://www.youtube.com/@handle/streams", "https://www.youtube.com/@handle/streams"),
        ("https://www.youtube.com/watch?v=abc", "https://www.youtube.com/watch?v=abc"),
        ("https://www.youtube.com/playlist?list=PL1", "https://www.youtube.com/playlist?list=PL1"),
        ("https://soundcloud.com/@handle", "https://soundcloud.com/@handle"),
        ("youtube.com/@handle", "youtube.com/@handle"),
    ],
)
def test_bare_youtube_channel_normalizes_to_videos_tab_only(url: str, expected: str) -> None:
    assert normalize_youtube_channel(url) == expected


def test_youtube_tab() -> None:
    assert youtube_tab("https://www.youtube.com/@h/videos") == "videos"
    assert youtube_tab("https://www.youtube.com/@h/shorts/") == "shorts"
    assert youtube_tab("https://www.youtube.com/@h") is None
    assert youtube_tab("https://vimeo.com/@h/videos") is None
