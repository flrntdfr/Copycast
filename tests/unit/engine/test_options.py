from __future__ import annotations

from pathlib import Path

import structlog

from copycast.adapters.engine.options import (
    BASE_OPTIONS,
    DIRECT_FORMAT,
    LISTING_OPTIONS,
    POSTPROCESSORS,
    YTDLP_FORMAT,
    engine_options_for,
    fetch_params,
    listing_params,
    merge_options,
    stored_feed_options,
    strip_owned,
    subtitle_languages,
    with_approximate_dates,
)
from copycast.application.ports import NullEngineLog
from copycast.domain.engine_options import ENGINE_OWNED_OPTIONS
from copycast.domain.enums import FetchKind
from copycast.settings import get_settings


def test_base_options_match_the_plan() -> None:
    assert BASE_OPTIONS["format"] == YTDLP_FORMAT == "bestaudio[ext=m4a]/bestaudio/best"
    assert DIRECT_FORMAT == "bestaudio/best"
    # The whole chain is added by the engine itself (thumbnail, audio, metadata, embed).
    assert POSTPROCESSORS == []
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


def test_listing_params_limit_makes_the_listing_shallow() -> None:
    """A light Refresh's first page: ``lazy_playlist`` stops the walk after ``playlistend``."""
    shallow = listing_params({"proxy": "http://proxy:3128"}, limit=15)
    assert shallow["lazy_playlist"] is True and shallow["playlistend"] == 15
    assert shallow["extract_flat"] is True and shallow["proxy"] == "http://proxy:3128"
    full = listing_params({})
    assert full["lazy_playlist"] is False and "playlistend" not in full


def test_engine_options_for_layers_config_and_feed_in_the_engine_adapter() -> None:
    settings = get_settings(engine={"options": {"ratelimit": 100, "subtitleslangs": ["fr"]}})
    merged = engine_options_for(settings, {"ratelimit": 5}, feed_id="f1", language="de-CH")
    assert merged["ratelimit"] == 5 and merged["subtitleslangs"] == ["fr"]
    assert merged["extractor_args"] == {"youtube": {"lang": ["de-CH"]}}
    defaulted = engine_options_for(get_settings(), None, feed_id="f1", language=None)
    assert defaulted["subtitleslangs"] == ["en", "-live_chat"] and "extractor_args" not in defaulted


def test_allowlisted_feed_options_reach_the_fetch_and_listing_params(tmp_path: Path) -> None:
    feed = {
        "ratelimit": 50_000,
        "sleep_interval_requests": 1.5,
        "format_sort": ["abr"],
        "geo_bypass_country": "CH",
        "age_limit": 18,
        "subtitleslangs": ["de"],
        "extractor_args": {"youtube": {"player_client": ["ios"]}},
    }
    options = engine_options_for(get_settings(), feed, feed_id="f1", language="fr")
    assert options["extractor_args"] == {"youtube": {"player_client": ["ios"], "lang": ["fr"]}}
    params = fetch_params(
        options, kind=FetchKind.ytdlp, item_id="i", home_dir=tmp_path, temp_dir=tmp_path
    )
    for key in ("ratelimit", "sleep_interval_requests", "format_sort", "age_limit"):
        assert params[key] == feed[key]
    assert params["geo_bypass_country"] == "CH" and params["subtitleslangs"] == ["de"]
    listing = listing_params(options)
    assert listing["ratelimit"] == 50_000
    assert listing["extractor_args"]["youtube"] == {"player_client": ["ios"], "lang": ["fr"]}


def test_operator_po_token_survives_a_mirrors_own_extractor_args(tmp_path: Path) -> None:
    """[engine.options] is the only place for po_token and youtubepot-*: a Mirror adds to it."""
    settings = get_settings(
        engine={
            "options": {
                "extractor_args": {
                    "youtube": {"po_token": ["web.gvs+abc"]},
                    "youtubepot-bgutilhttp": {"base_url": ["http://pot:4416"]},
                }
            }
        }
    )
    feed = {"extractor_args": {"youtube": {"player_client": ["ios"]}}}
    options = engine_options_for(settings, feed, feed_id="f1", language="fr")
    params = fetch_params(
        options, kind=FetchKind.ytdlp, item_id="i", home_dir=tmp_path, temp_dir=tmp_path
    )
    assert params["extractor_args"] == {
        "youtube": {"po_token": ["web.gvs+abc"], "player_client": ["ios"], "lang": ["fr"]},
        "youtubepot-bgutilhttp": {"base_url": ["http://pot:4416"]},
    }
    assert listing_params(options)["extractor_args"]["youtube"]["po_token"] == ["web.gvs+abc"]


def test_stored_feed_options_drop_refused_keys_with_a_warning(tmp_path: Path) -> None:
    """A row written before 2.3.0 keeps its keys in Postgres; none of them reaches yt-dlp."""
    stored = {
        "netrc_cmd": "touch /tmp/pwned",
        "usenetrc": True,
        "proxy": "http://attacker:3128",
        "http_headers": {"Ytdl-Request-Proxy": "http://attacker:3128"},
        "extractor_args": {"youtube": {"lang": ["de"], "innertube_host": ["attacker"]}},
        "ratelimit": 5,
    }
    with structlog.testing.capture_logs() as events:
        kept = stored_feed_options("feed-1", stored)
    assert kept == {"ratelimit": 5, "extractor_args": {"youtube": {"lang": ["de"]}}}
    warnings = [e for e in events if e["event"] == "engine_options.refused"]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning" and warnings[0]["feed_id"] == "feed-1"
    assert warnings[0]["keys"] == [
        "extractor_args.youtube.innertube_host",
        "http_headers",
        "netrc_cmd",
        "proxy",
        "usenetrc",
    ]

    # The same row through the engine call path: the operator's own proxy survives,
    # the Feed's refused keys do not.
    settings = get_settings(engine={"options": {"proxy": "http://operator:3128"}})
    with structlog.testing.capture_logs() as events:
        options = engine_options_for(settings, stored, feed_id="feed-1", language=None)
    assert [e["feed_id"] for e in events if e["event"] == "engine_options.refused"] == ["feed-1"]
    params = fetch_params(
        options, kind=FetchKind.ytdlp, item_id="i", home_dir=tmp_path, temp_dir=tmp_path
    )
    for key in ("netrc_cmd", "usenetrc", "http_headers"):
        assert key not in params and key not in listing_params(options)
    assert params["proxy"] == "http://operator:3128"
    assert params["extractor_args"] == {"youtube": {"lang": ["de"]}}


def test_stored_feed_options_log_nothing_for_a_clean_row() -> None:
    with structlog.testing.capture_logs() as events:
        assert stored_feed_options("f", {"ratelimit": 5}) == {"ratelimit": 5}
        assert stored_feed_options("f", None) == {}
    assert events == []


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


def test_with_cookiefile_adds_the_stored_file_unless_configured(tmp_path: Path) -> None:
    from copycast.adapters.engine.options import with_cookiefile

    missing = tmp_path / "cookies.txt"
    assert with_cookiefile({"ratelimit": 1}, missing) == {"ratelimit": 1}
    assert with_cookiefile(None, None) == {}
    missing.write_text("# cookies\n", encoding="utf-8")
    assert with_cookiefile({"ratelimit": 1}, missing) == {
        "ratelimit": 1,
        "cookiefile": str(missing),
    }
    # An explicit [engine.options] cookiefile wins over the stored one.
    assert with_cookiefile({"cookiefile": "/etc/mine.txt"}, missing) == {
        "cookiefile": "/etc/mine.txt"
    }


def test_listing_params_ask_youtube_tabs_for_approximate_dates() -> None:
    params = listing_params({"extractor_args": {"youtube": {"lang": ["fr"]}}})
    assert params["extractor_args"] == {
        "youtube": {"lang": ["fr"]},
        "youtubetab": {"approximate_date": ["true"]},
    }
    # An operator's own choice is kept.
    own = with_approximate_dates(
        {"extractor_args": {"youtubetab": {"approximate_date": ["false"]}}}
    )
    assert own["extractor_args"]["youtubetab"]["approximate_date"] == ["false"]
