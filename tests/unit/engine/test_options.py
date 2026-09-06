from __future__ import annotations

from pathlib import Path

from copycast.adapters.engine.options import (
    BASE_OPTIONS,
    DIRECT_FORMAT,
    LISTING_OPTIONS,
    POSTPROCESSORS,
    YTDLP_FORMAT,
    fetch_params,
    listing_params,
    merge_options,
    strip_owned,
    subtitle_languages,
)
from copycast.application.ports import NullEngineLog
from copycast.domain.engine_options import ENGINE_OWNED_OPTIONS
from copycast.domain.enums import FetchKind


def test_base_options_match_the_plan() -> None:
    assert BASE_OPTIONS["format"] == YTDLP_FORMAT == "bestaudio[ext=m4a]/bestaudio/best"
    assert DIRECT_FORMAT == "bestaudio/best"
    keys = [pp["key"] for pp in POSTPROCESSORS]
    assert keys == [
        "FFmpegThumbnailsConvertor",
        "FFmpegExtractAudio",
        "FFmpegMetadata",
        "EmbedThumbnail",
    ]
    assert POSTPROCESSORS[0]["when"] == "before_dl" and POSTPROCESSORS[0]["format"] == "jpg"
    assert POSTPROCESSORS[1]["preferredcodec"] == "best"
    assert POSTPROCESSORS[2] == {
        "key": "FFmpegMetadata",
        "add_metadata": True,
        "add_chapters": True,
        "add_infojson": False,
    }
    assert POSTPROCESSORS[3]["already_have_thumbnail"] is True
    for key in ("writethumbnail", "writeinfojson", "clean_infojson", "writesubtitles"):
        assert BASE_OPTIONS[key] is True
    assert BASE_OPTIONS["writeautomaticsub"] is False
    assert BASE_OPTIONS["subtitlesformat"] == "vtt/srt/best"
    assert BASE_OPTIONS["noplaylist"] is True and BASE_OPTIONS["continuedl"] is True
    assert BASE_OPTIONS["retries"] == 3 and BASE_OPTIONS["socket_timeout"] == 30
    assert BASE_OPTIONS["noprogress"] is True and BASE_OPTIONS["quiet"] is True


def test_listing_options_are_flat_and_download_nothing() -> None:
    assert LISTING_OPTIONS["extract_flat"] is True
    assert LISTING_OPTIONS["lazy_playlist"] is False
    assert LISTING_OPTIONS["ignoreerrors"] == "only_download"
    assert LISTING_OPTIONS["skip_download"] is True
    params = listing_params({"proxy": "http://proxy:3128", "outtmpl": "evil"})
    assert params["proxy"] == "http://proxy:3128"
    assert "outtmpl" not in params
    assert params["postprocessors"] == []
    assert params["writeinfojson"] is False


def test_strip_owned_drops_every_engine_owned_key() -> None:
    options = {key: 1 for key in ENGINE_OWNED_OPTIONS} | {"proxy": "p", "ratelimit": 5}
    assert strip_owned(options) == {"proxy": "p", "ratelimit": 5}
    assert strip_owned(None) == {}


def test_subtitle_languages_add_feed_language_then_english_then_live_chat_exclusion() -> None:
    assert subtitle_languages(None) == ["en", "-live_chat"]
    assert subtitle_languages("de") == ["de", "en", "-live_chat"]
    assert subtitle_languages("en-US") == ["en-us", "en", "-live_chat"]
    assert subtitle_languages("pt_BR") == ["pt-br", "pt", "en", "-live_chat"]
    assert subtitle_languages("  ") == ["en", "-live_chat"]


def test_merge_order_config_then_feed_then_base_wins() -> None:
    merged = merge_options(
        {"ratelimit": 1, "proxy": "global", "format": "worst"},
        {"ratelimit": 2, "subtitleslangs": ["fr"], "quiet": False},
        language="de",
    )
    assert merged["ratelimit"] == 2
    assert merged["proxy"] == "global"
    assert merged["format"] == YTDLP_FORMAT
    assert merged["quiet"] is True
    assert merged["subtitleslangs"] == ["fr"]
    assert merge_options({}, {}, language="de")["subtitleslangs"] == ["de", "en", "-live_chat"]


def test_fetch_params_set_outtmpl_paths_format_and_logger(tmp_path: Path) -> None:
    log = NullEngineLog()
    home, temp = tmp_path / "media", tmp_path / "tmp"
    direct = fetch_params(
        {"outtmpl": "x", "ratelimit": 9},
        kind=FetchKind.direct,
        item_id="abc",
        home_dir=home,
        temp_dir=temp,
        log=log,
    )
    assert direct["format"] == DIRECT_FORMAT
    assert direct["outtmpl"] == {"default": "abc.%(ext)s"}
    assert direct["paths"] == {"home": str(home), "temp": str(temp)}
    assert direct["logger"] is log
    assert direct["ratelimit"] == 9
    ytdlp = fetch_params({}, kind=FetchKind.ytdlp, item_id="abc", home_dir=home, temp_dir=temp)
    assert ytdlp["format"] == YTDLP_FORMAT
    assert "logger" not in ytdlp
    assert ytdlp["postprocessors"] == POSTPROCESSORS
