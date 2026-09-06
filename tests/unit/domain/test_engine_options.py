from __future__ import annotations

import pytest

from copycast.domain.engine_options import (
    ENGINE_OWNED_OPTIONS,
    GLOBAL_ONLY_OPTIONS,
    EngineOptionRejected,
    EngineOptions,
)

PLAN_OWNED = {
    "outtmpl",
    "outtmpl_na_placeholder",
    "paths",
    "download_archive",
    "progress_hooks",
    "postprocessor_hooks",
    "post_hooks",
    "logger",
    "postprocessors",
    "format",
    "extract_flat",
    "writeinfojson",
    "writethumbnail",
    "writesubtitles",
    "skip_download",
    "simulate",
    "quiet",
    "noprogress",
    "daterange",
    "playlist_items",
    "noplaylist",
    "exec",
    "exec_cmd",
    "external_downloader",
    "external_downloader_args",
    "ffmpeg_location",
    "cookiesfrombrowser",
    "load_info_filename",
    "batchfile",
    "daemonize",
}


def test_option_sets_match_the_plan() -> None:
    assert set(ENGINE_OWNED_OPTIONS) == PLAN_OWNED
    assert set(GLOBAL_ONLY_OPTIONS) == {"cookiefile", "proxy", "geo_verification_proxy"}
    assert not ENGINE_OWNED_OPTIONS & GLOBAL_ONLY_OPTIONS


def test_global_scope_accepts_operator_options() -> None:
    data = {"proxy": "socks5://127.0.0.1:1080", "cookiefile": "/c.txt", "ratelimit": 1_000_000}
    result = EngineOptions.validate(data, scope="global")
    assert result == data
    assert result is not data


@pytest.mark.parametrize("key", sorted(PLAN_OWNED))
def test_every_owned_option_is_rejected_in_both_scopes(key: str) -> None:
    for scope in ("global", "feed"):
        with pytest.raises(EngineOptionRejected) as excinfo:
            EngineOptions.validate({key: 1}, scope=scope)  # type: ignore[arg-type]
        assert excinfo.value.key == key
        assert excinfo.value.keys == [key]
        assert excinfo.value.scope == scope


@pytest.mark.parametrize("key", sorted(GLOBAL_ONLY_OPTIONS))
def test_global_only_options_are_rejected_for_feeds_only(key: str) -> None:
    assert EngineOptions.validate({key: "x"}, scope="global") == {key: "x"}
    with pytest.raises(EngineOptionRejected):
        EngineOptions.validate({key: "x"}, scope="feed")


def test_rejection_lists_every_offending_key_sorted() -> None:
    with pytest.raises(EngineOptionRejected) as excinfo:
        EngineOptions.validate({"quiet": True, "format": "x", "proxy": "y", "ok": 1}, scope="feed")
    err = excinfo.value
    assert err.keys == ["format", "proxy", "quiet"]
    assert err.key == "format"
    assert err.slug == "engine-option-rejected"
    assert err.status == 422
    assert err.extras == {"keys": ["format", "proxy", "quiet"], "scope": "feed"}
    assert "format, proxy, quiet" in str(err)
    assert "engine_options" in str(err)


def test_global_message_points_at_the_config_section() -> None:
    with pytest.raises(EngineOptionRejected, match=r"\[engine\.options\]"):
        EngineOptions.validate({"outtmpl": "x"}, scope="global")


def test_none_and_empty() -> None:
    assert EngineOptions.validate(None) == {}
    assert EngineOptions.validate({}) == {}
    assert EngineOptions.rejected_keys({}, "feed") == []


def test_merge_order_config_then_feed_then_base_last() -> None:
    merged = EngineOptions.merge(
        {"ratelimit": 1, "proxy": "p", "retries": 10},
        {"ratelimit": 2, "subtitleslangs": ["de"]},
        {"retries": 3, "format": "bestaudio"},
    )
    assert merged == {
        "ratelimit": 2,
        "proxy": "p",
        "retries": 3,
        "subtitleslangs": ["de"],
        "format": "bestaudio",
    }


def test_merge_tolerates_missing_layers() -> None:
    assert EngineOptions.merge(None, None) == {}
    assert EngineOptions.merge({"a": 1}, None, None) == {"a": 1}
