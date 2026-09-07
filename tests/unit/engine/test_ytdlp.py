"""Engine pieces that need no network: version info, hooks, output collection."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadCancelled

from copycast.adapters.engine import build_engine, engine_info
from copycast.adapters.engine.ytdlp import (
    YtDlpEngine,
    _HookState,
    _postprocessor_hook,
    _progress_hook,
    collect_outputs,
    release_date,
)
from copycast.application.ports import CancelToken, FetchSpec, PermanentError, Progress
from copycast.domain.enums import EnginePhase, FetchKind
from copycast.settings import Settings


def test_engine_info_reads_yt_dlp_version_and_ffmpeg() -> None:
    info = engine_info()
    assert info.name == "yt-dlp"
    assert info.version.count(".") >= 2
    assert info.channel
    assert info.release_date == release_date(info.version)
    assert info.ffmpeg_version is None or info.ffmpeg_version[0].isdigit()


def test_build_engine_returns_the_port_implementation(settings: Settings) -> None:
    engine = build_engine(settings)
    assert isinstance(engine, YtDlpEngine)
    assert engine.version() == engine_info()


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("2026.08.19", date(2026, 8, 19)),
        ("2026.08.19.232658", date(2026, 8, 19)),
        ("2024.1.5", date(2024, 1, 5)),
        ("1.0", None),
        ("x.y.z", None),
    ],
)
def test_release_date(version: str, expected: date | None) -> None:
    assert release_date(version) == expected


def test_progress_hook_maps_download_status_and_checks_the_token() -> None:
    seen: list[Progress] = []
    token = CancelToken()
    state = _HookState()
    hook = _progress_hook(token, seen.append, state)
    hook(
        {
            "status": "downloading",
            "downloaded_bytes": 10,
            "total_bytes_estimate": 100,
            "speed": 5.0,
            "eta": 9,
        }
    )
    hook({"status": "finished", "total_bytes": 100})
    hook({"status": "error"})
    assert seen[0] == Progress(EnginePhase.downloading, 10, 100, 5.0, 9)
    assert seen[0].percent == 10.0
    assert seen[1] == Progress(EnginePhase.postprocessing, 100, 100, None, None)
    assert len(seen) == 2
    assert state.downloaded is True
    token.cancel()
    with pytest.raises(DownloadCancelled):
        hook({"status": "downloading", "downloaded_bytes": 20})


def test_postprocessor_hook_is_silent_before_the_download_finished() -> None:
    seen: list[Progress] = []
    token = CancelToken()
    state = _HookState()
    hook = _postprocessor_hook(token, seen.append, state)
    hook({"status": "started", "postprocessor": "ThumbnailsConvertor"})
    assert seen == []
    state.downloaded = True
    hook({"status": "started", "postprocessor": "Metadata"})
    hook({"status": "finished", "postprocessor": "Metadata"})
    assert seen == [Progress(EnginePhase.postprocessing)]
    token.cancel()
    with pytest.raises(DownloadCancelled):
        hook({"status": "processing"})


def _spec(tmp_path: Path, item_id: str = "abc123") -> FetchSpec:
    home, temp = tmp_path / "media", tmp_path / "tmp"
    home.mkdir()
    temp.mkdir()
    return FetchSpec(FetchKind.ytdlp, "https://x.example/v", item_id, home, temp)


def test_collect_outputs_finds_audio_sidecars_and_chapters(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    audio = spec.home_dir / "abc123.m4a"
    audio.write_bytes(b"x" * 10)
    (spec.home_dir / "abc123.info.json").write_text("{}")
    (spec.home_dir / "abc123.jpg").write_bytes(b"jpg")
    (spec.home_dir / "abc123.en.vtt").write_text("WEBVTT")
    (spec.home_dir / "abc123.de-DE.srt").write_text("1")
    (spec.home_dir / "other.m4a").write_bytes(b"no")
    (spec.temp_dir / "abc123.m4a.part").write_bytes(b"leftover")
    info = {
        "duration": 754.4,
        "chapters": [{"start_time": 0, "end_time": 1, "title": "Intro"}, "junk"],
        "requested_downloads": [{"filepath": str(spec.temp_dir / "abc123.m4a")}],
    }
    result = collect_outputs(spec, info, engine_version="2026.08.19")
    assert result.audio_path == audio
    assert result.ext == "m4a" and result.mime == "audio/mp4" and result.size_bytes == 10
    assert result.duration_seconds == 754
    assert result.info_json_path == spec.home_dir / "abc123.info.json"
    assert result.artwork_path == spec.home_dir / "abc123.jpg"
    assert result.subtitle_paths == {
        "en": spec.home_dir / "abc123.en.vtt",
        "de-DE": spec.home_dir / "abc123.de-DE.srt",
    }
    assert result.chapters == [{"start_time": 0, "end_time": 1, "title": "Intro"}]
    assert result.engine_version == "2026.08.19"


def test_collect_outputs_uses_the_reported_filepath_when_present(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    (spec.home_dir / "abc123.opus").write_bytes(b"x")
    (spec.home_dir / "abc123.webm").write_bytes(b"y")
    info = {"filepath": str(spec.home_dir / "abc123.opus")}
    result = collect_outputs(spec, info, engine_version="v")
    assert result.audio_path.name == "abc123.opus" and result.mime == "audio/opus"
    assert result.duration_seconds is None and result.chapters == []


def test_collect_outputs_without_audio_is_permanent(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    (spec.home_dir / "abc123.info.json").write_text(json.dumps({}))
    with pytest.raises(PermanentError):
        collect_outputs(spec, {}, engine_version="v")


def test_direct_fetch_without_synth_is_refused(tmp_path: Path) -> None:
    engine = YtDlpEngine()
    spec = FetchSpec(FetchKind.direct, "https://x", "id", tmp_path / "m", tmp_path / "t")
    with pytest.raises(PermanentError):
        engine.fetch_item(spec, {}, CancelToken(), lambda _: None, _Log())


def test_cancelled_token_short_circuits_before_any_call(tmp_path: Path) -> None:
    from copycast.application.ports import Cancelled

    engine = YtDlpEngine()
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        engine.list_source("https://x.example", {}, token, _Log())
    spec = FetchSpec(FetchKind.ytdlp, "https://x", "id", tmp_path / "m", tmp_path / "t")
    with pytest.raises(Cancelled):
        engine.fetch_item(spec, {}, token, lambda _: None, _Log())


class _Log:
    def debug(self, message: str) -> None:
        return None

    def info(self, message: str) -> None:
        return None

    def warning(self, message: str) -> None:
        return None

    def error(self, message: str) -> None:
        return None


# --------------------------------------------------------------------------- thumbnails and cookies

JPEG_HEAD = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 32
PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WEBP_HEAD = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 32


def test_sniff_image_ext(tmp_path: Path) -> None:
    from copycast.adapters.engine.ytdlp import sniff_image_ext

    for name, head, expected in (
        ("a.png", JPEG_HEAD, "jpg"),
        ("b.jpg", PNG_HEAD, "png"),
        ("c.png", WEBP_HEAD, "webp"),
        ("d.gif", b"GIF89a" + b"\x00" * 8, "gif"),
        ("e.png", b"<!doctype html>", None),
        ("f.png", b"", None),
    ):
        path = tmp_path / name
        path.write_bytes(head)
        assert sniff_image_ext(path) == expected, name
    assert sniff_image_ext(tmp_path / "missing.png") is None


def test_fix_thumbnail_extensions_renames_by_bytes_and_keeps_files_to_move(tmp_path: Path) -> None:
    from copycast.adapters.engine.ytdlp import fix_thumbnail_extensions

    lying = tmp_path / "item.png"  # a JPEG behind a .png URL (imgix auto=format)
    lying.write_bytes(JPEG_HEAD)
    honest = tmp_path / "other.jpg"
    honest.write_bytes(JPEG_HEAD)
    info = {
        "thumbnails": [
            {"id": "0", "filepath": str(lying)},
            {"id": "1", "filepath": str(honest)},
            {"id": "2"},
        ],
        "__files_to_move": {str(lying): "/home/item.png", str(honest): "/home/other.jpg"},
    }
    said: list[str] = []
    assert fix_thumbnail_extensions(info, said.append) == 1
    fixed = tmp_path / "item.jpg"
    assert fixed.is_file() and not lying.exists()
    assert info["thumbnails"][0]["filepath"] == str(fixed)
    assert info["thumbnails"][1]["filepath"] == str(honest)
    assert info["__files_to_move"] == {str(fixed): "/home/item.jpg", str(honest): "/home/other.jpg"}
    assert said == [f'Correcting thumbnail "{lying}" extension to jpg']
    assert fix_thumbnail_extensions(info, said.append) == 0


def test_cookie_scope_copies_the_file_and_writes_changes_back(tmp_path: Path) -> None:
    from copycast.adapters.engine.ytdlp import cookie_scope

    stored = tmp_path / "engine" / "cookies.txt"
    stored.parent.mkdir()
    stored.write_text("# v1\n", encoding="utf-8")

    with cookie_scope(stored, {"ratelimit": 1}) as options:
        scratch = Path(options["cookiefile"])
        assert scratch != stored and scratch.parent == stored.parent
        assert scratch.read_text(encoding="utf-8") == "# v1\n"
        assert options["ratelimit"] == 1
        scratch.write_text("# v2 rotated by yt-dlp\n", encoding="utf-8")
    assert not scratch.exists()
    assert stored.read_text(encoding="utf-8") == "# v2 rotated by yt-dlp\n"
    assert sorted(p.name for p in stored.parent.iterdir()) == ["cookies.txt"]

    # Unchanged: the stored file is left alone (no rewrite, no leftovers).
    with cookie_scope(stored, {}) as options:
        scratch = Path(options["cookiefile"])
    assert not scratch.exists() and stored.read_text(encoding="utf-8").startswith("# v2")

    # No stored file, or an explicit cookiefile: options pass through untouched.
    with cookie_scope(tmp_path / "absent.txt", {"a": 1}) as options:
        assert options == {"a": 1}
    with cookie_scope(stored, {"cookiefile": "/etc/mine.txt"}) as options:
        assert options == {"cookiefile": "/etc/mine.txt"}
    with cookie_scope(None, {"a": 1}) as options:
        assert options == {"a": 1}


def test_engine_reads_the_cookie_path_from_the_settings(settings: Settings) -> None:
    engine = build_engine(settings)
    assert engine._cookies_path == settings.data_dir / "engine" / "cookies.txt"
    assert YtDlpEngine()._cookies_path is None


@pytest.mark.parametrize(
    ("ext", "codec", "expected"),
    [
        ("mp3", None, "best"),
        ("m4a", None, "best"),
        ("M4A", None, "best"),
        ("mp4", "aac", "best"),
        ("webm", "aac", "best"),
        ("webm", "opus", "mp3"),
        ("opus", "opus", "mp3"),
        ("ogg", "vorbis", "mp3"),
        ("flac", "flac", "mp3"),
        ("wav", "pcm_s16le", "mp3"),
        ("aac", "aac", "mp3"),
        ("bin", None, "mp3"),
    ],
)
def test_audio_target(ext: str, codec: str | None, expected: str) -> None:
    from copycast.adapters.engine.ytdlp import audio_target

    assert audio_target(ext, codec) == expected
