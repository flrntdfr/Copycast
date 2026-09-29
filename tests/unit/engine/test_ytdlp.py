"""Engine pieces that need no network: version info, hooks, the live filter, outputs."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import yt_dlp
from yt_dlp.utils import DownloadCancelled, DownloadError, ExtractorError

from copycast.adapters.engine import build_engine, engine_info
from copycast.adapters.engine.ytdlp import (
    NOT_READY_MESSAGES,
    NotYetAvailable,
    YtDlpEngine,
    _HookState,
    _postprocessor_hook,
    _progress_hook,
    collect_outputs,
    fetch_params_for,
    live_match_filter,
    release_date,
)
from copycast.application.ports import (
    Cancelled,
    CancelToken,
    FetchSpec,
    NotReady,
    PermanentError,
    Progress,
)
from copycast.domain.enums import EnginePhase, FetchKind, LiveStatus
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


# --------------------------------------------------------------------------- live streams


@pytest.mark.parametrize(
    "status", [LiveStatus.is_upcoming, LiveStatus.is_live, LiveStatus.post_live]
)
def test_live_match_filter_refuses_a_stream_without_a_recording(status: LiveStatus) -> None:
    state = _HookState()
    match_filter = live_match_filter(state)
    with pytest.raises(NotYetAvailable, match=NOT_READY_MESSAGES[status].split(":")[0]):
        match_filter({"id": "x", "live_status": status.value}, incomplete=False)
    assert state.not_ready is status
    assert state.starts_in is None
    assert issubclass(NotYetAvailable, DownloadCancelled), "yt-dlp re-raises it, never swallows it"


@pytest.mark.parametrize("value", ["was_live", "not_live", None, "premiering"])
def test_live_match_filter_lets_recordings_and_plain_uploads_through(value: object) -> None:
    state = _HookState()
    match_filter = live_match_filter(state)
    assert match_filter({"id": "x", "live_status": value}) is None
    assert match_filter({"id": "x", "live_status": value}, incomplete=True) is None
    assert state.not_ready is None


def test_live_match_filter_estimates_an_upcoming_streams_start() -> None:
    state = _HookState()
    match_filter = live_match_filter(state)
    start = time.time() + 3 * 3600
    with pytest.raises(NotYetAvailable):
        match_filter({"live_status": "is_upcoming", "release_timestamp": start})
    assert state.not_ready is LiveStatus.is_upcoming
    assert state.starts_in is not None
    assert timedelta(hours=2, minutes=59) < state.starts_in <= timedelta(hours=3)
    # A start in the past (the streamer is late) or none at all estimates nothing.
    for info in (
        {"live_status": "is_upcoming", "release_timestamp": start - 4 * 3600},
        {"live_status": "is_upcoming"},
        {"live_status": "is_upcoming", "release_timestamp": True},
    ):
        state = _HookState()
        with pytest.raises(NotYetAvailable):
            live_match_filter(state)(info)
        assert state.starts_in is None


def test_fetch_params_carry_the_live_filter_for_engine_fetches_only(tmp_path: Path) -> None:
    state = _HookState()
    hook = _progress_hook(CancelToken(), lambda _: None, state)
    pp_hook = _postprocessor_hook(CancelToken(), lambda _: None, state)
    spec = _spec(tmp_path)
    params = fetch_params_for(
        spec,
        {"ratelimit": 1},
        log=_Log(),
        progress_hook=hook,
        postprocessor_hook=pp_hook,
        state=state,
    )
    assert params["progress_hooks"] == [hook] and params["postprocessor_hooks"] == [pp_hook]
    assert params["ratelimit"] == 1 and params["outtmpl"] == {"default": "abc123.%(ext)s"}
    with pytest.raises(NotYetAvailable):
        params["match_filter"]({"live_status": "is_live"}, incomplete=False)
    assert state.not_ready is LiveStatus.is_live

    from copycast.application.ports import SynthItem

    direct = FetchSpec(
        FetchKind.direct,
        "https://x.example/a.mp3",
        "abc123",
        spec.home_dir,
        spec.temp_dir,
        SynthItem(id="abc123", title="A", url="https://x.example/a.mp3"),
    )
    params = fetch_params_for(
        direct, {}, log=_Log(), progress_hook=hook, postprocessor_hook=pp_hook, state=_HookState()
    )
    assert "match_filter" not in params, "an RSS enclosure has no live status to look at"


class _StubYoutubeDL:
    """Enough of ``YoutubeDL`` to run the match filter the way ``_match_entry`` does.

    yt-dlp catches the ``DownloadCancelled`` the filter raises and re-raises a
    fresh, message-less instance of the same class; the stub does the same so
    the engine cannot rely on the exception carrying anything.
    """

    info: Mapping[str, Any] = {"id": "x", "live_status": "is_live"}
    raise_plain_cancel = False

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params

    def __enter__(self) -> _StubYoutubeDL:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def add_post_processor(self, pp: object, when: str = "post_process") -> None:
        return None

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
        if self.raise_plain_cancel:
            raise DownloadCancelled("stopped")
        (self.params["paths"]["temp"] / Path("x.jpg")).write_bytes(b"thumb")
        try:
            self.params["match_filter"](self.info, incomplete=False)
        except DownloadCancelled as err:
            raise type(err)() from None
        return {"id": "x"}


def test_fetch_item_maps_a_refused_stream_to_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _StubYoutubeDL)
    engine = YtDlpEngine()
    spec = _spec(tmp_path, item_id="x")
    with pytest.raises(NotReady) as caught:
        engine.fetch_item(spec, {}, CancelToken(), lambda _: None, _Log())
    assert caught.value.live_status is LiveStatus.is_live
    assert caught.value.retry_after is None
    assert str(caught.value) == NOT_READY_MESSAGES[LiveStatus.is_live]
    assert isinstance(caught.value.__cause__, NotYetAvailable)
    assert not list(spec.temp_dir.iterdir()), "temp leftovers are discarded like any failure"


UPCOMING_REASON = "This live event will begin in 2 hours."


def _extractor_refusal(reason: str) -> DownloadError:
    """What ``extract_info`` raises when the extractor refuses a format-less video: the
    expected ``ExtractorError`` wrapped by ``YoutubeDL.trouble`` with ``exc_info``."""
    try:
        raise ExtractorError(reason, expected=True, video_id="x", ie="youtube")
    except ExtractorError:
        import sys

        return DownloadError(f"ERROR: [youtube] x: {reason}", sys.exc_info())


class _RefusingYoutubeDL(_StubYoutubeDL):
    """The extractor refuses the video before ``process_video_result`` runs the filter."""

    reason = UPCOMING_REASON

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
        (self.params["paths"]["temp"] / Path("x.jpg")).write_bytes(b"thumb")
        raise _extractor_refusal(self.reason)

    def process_ie_result(self, ie_result: dict[str, Any], download: bool = True) -> dict[str, Any]:
        raise _extractor_refusal(self.reason)


def test_fetch_item_maps_the_extractors_not_started_refusal_to_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stream that has not started has no formats: the extractor refuses it before the
    match filter can see ``is_upcoming``, so the refusal itself is reported as NotReady."""
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _RefusingYoutubeDL)
    engine = YtDlpEngine()
    spec = _spec(tmp_path, item_id="x")
    with pytest.raises(NotReady) as caught:
        engine.fetch_item(spec, {}, CancelToken(), lambda _: None, _Log())
    assert caught.value.live_status is LiveStatus.is_upcoming
    assert caught.value.retry_after is None, "the reason gives no usable start time"
    assert str(caught.value).startswith(NOT_READY_MESSAGES[LiveStatus.is_upcoming])
    assert UPCOMING_REASON in str(caught.value)
    assert isinstance(caught.value.__cause__, DownloadError)
    assert not list(spec.temp_dir.iterdir()), "temp leftovers are discarded like any failure"


def test_fetch_item_keeps_other_extractor_refusals_permanent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _RefusingYoutubeDL)
    monkeypatch.setattr(_RefusingYoutubeDL, "reason", "Private video")
    engine = YtDlpEngine()
    with pytest.raises(PermanentError, match="Private video"):
        engine.fetch_item(_spec(tmp_path, item_id="x"), {}, CancelToken(), lambda _: None, _Log())


def test_direct_fetch_never_reads_a_refusal_as_a_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An RSS enclosure has no live status; a refusal wording is a permanent error there."""
    from copycast.application.ports import SynthItem

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _RefusingYoutubeDL)
    home, temp = tmp_path / "m", tmp_path / "t"
    direct = FetchSpec(
        FetchKind.direct,
        "https://x.example/a.mp3",
        "x",
        home,
        temp,
        SynthItem(id="x", title="A", url="https://x.example/a.mp3"),
    )
    with pytest.raises(PermanentError):
        YtDlpEngine().fetch_item(direct, {}, CancelToken(), lambda _: None, _Log())


class _SilentYoutubeDL(_StubYoutubeDL):
    """A listing run with ``ignoreerrors``: the refusal is logged and nothing is returned."""

    reason = UPCOMING_REASON

    def extract_info(self, url: str, download: bool = True) -> None:
        self.params["logger"].error(f"ERROR: [youtube] x: {self.reason}")
        return None

    def sanitize_info(self, info: object) -> object:
        return info


def test_list_source_reports_the_logged_reason_when_yt_dlp_extracts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _SilentYoutubeDL)
    seen: list[str] = []

    class _Recording(_Log):
        def error(self, message: str) -> None:
            seen.append(message)

    with pytest.raises(PermanentError) as caught:
        YtDlpEngine().list_source(
            "https://www.youtube.com/watch?v=x", {}, CancelToken(), _Recording()
        )
    assert str(caught.value) == (
        "yt-dlp extracted nothing from https://www.youtube.com/watch?v=x: "
        f"[youtube] x: {UPCOMING_REASON}"
    )
    assert seen == [f"ERROR: [youtube] x: {UPCOMING_REASON}"], "the job log still gets the line"


def test_fetch_item_keeps_a_plain_cancellation_a_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _StubYoutubeDL)
    monkeypatch.setattr(_StubYoutubeDL, "raise_plain_cancel", True)
    engine = YtDlpEngine()
    with pytest.raises(Cancelled):
        engine.fetch_item(_spec(tmp_path, item_id="x"), {}, CancelToken(), lambda _: None, _Log())


def test_cancelled_token_short_circuits_before_any_call(tmp_path: Path) -> None:
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


def test_audio_target_agrees_with_the_domain_prediction() -> None:
    """A stable media URL promises the container ``audio_target`` leaves for that file.

    ``archived_ext_for`` decides from the listing alone; ``audio_target`` also probes
    the codec, so the comparison assumes the codec each container carries in practice.
    """
    from copycast.adapters.engine.synth import EXT_CODEC
    from copycast.adapters.engine.ytdlp import audio_target
    from copycast.domain.media import ENGINE_EXT, KNOWN_EXTS, PODCAST_EXTS, archived_ext_for

    for ext in sorted(KNOWN_EXTS):
        target = audio_target(ext, EXT_CODEC.get(ext))
        left = (ext if ext in PODCAST_EXTS else ENGINE_EXT) if target == "best" else target
        assert archived_ext_for(ext) == left, ext
