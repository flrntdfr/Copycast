"""Real ``YtDlpEngine.fetch_item`` over the origin fixture server (needs ffmpeg/ffprobe)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from copycast.adapters.engine.ytdlp import YtDlpEngine, engine_info
from copycast.application.ports import (
    Cancelled,
    CancelToken,
    FetchSpec,
    PermanentError,
    Progress,
    SynthItem,
)
from copycast.domain.enums import EnginePhase, FetchKind
from tests.support.origin import Origin

pytestmark = pytest.mark.ffmpeg

TINY_MP3_BYTES = 4407


class RecordingLog:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def debug(self, message: str) -> None:
        self.lines.append(("debug", message))

    def info(self, message: str) -> None:
        self.lines.append(("info", message))

    def warning(self, message: str) -> None:
        self.lines.append(("warning", message))

    def error(self, message: str) -> None:
        self.lines.append(("error", message))

    def text(self) -> str:
        return "\n".join(line for _, line in self.lines)


@pytest.fixture(autouse=True)
def _needs_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")


def ffprobe(path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def synth_for(origin: Origin, item_id: str) -> SynthItem:
    return SynthItem(
        id=item_id,
        title="Episode 5: Chapters, transcripts and all",
        url=origin.url_for("/media/tiny.mp3"),
        mime="audio/mpeg",
        description="The fifth episode.",
        timestamp=1705305600,
        duration=1.0,
        artist="Alex Host",
        album="Copycast Test Podcast",
        episode_number=5,
        season_number=1,
        thumbnail_url=origin.url_for("/media/tiny.jpg"),
        webpage_url="https://podcast.example/episodes/5",
    )


def spec_for(origin: Origin, root: Path, item_id: str = "0123456789abcdef") -> FetchSpec:
    synth = synth_for(origin, item_id)
    return FetchSpec(FetchKind.direct, synth.url, item_id, root / "media", root / "tmp", synth)


def test_direct_fetch_embeds_tags_and_artwork_without_re_encoding(
    origin: Origin, tmp_path: Path
) -> None:
    spec = spec_for(origin, tmp_path)
    log = RecordingLog()
    progress: list[Progress] = []
    result = YtDlpEngine().fetch_item(
        spec, {"ratelimit": None}, CancelToken(), progress.append, log
    )

    assert result.audio_path == spec.home_dir / f"{spec.item_id}.mp3"
    assert result.ext == "mp3" and result.mime == "audio/mpeg"
    assert result.size_bytes == result.audio_path.stat().st_size > TINY_MP3_BYTES
    assert result.duration_seconds == 1
    assert result.info_json_path == spec.home_dir / f"{spec.item_id}.info.json"
    assert result.artwork_path == spec.home_dir / f"{spec.item_id}.jpg"
    assert result.subtitle_paths == {} and result.chapters == []
    assert result.engine_version == engine_info().version

    info = json.loads(result.info_json_path.read_text())
    assert info["extractor"] == "copycast:rss" and info["id"] == spec.item_id
    assert info["album"] == "Copycast Test Podcast"

    probe = ffprobe(result.audio_path)
    tags = {k.lower(): v for k, v in probe["format"].get("tags", {}).items()}
    assert tags["title"] == "Episode 5: Chapters, transcripts and all"
    assert tags["artist"] == "Alex Host"
    assert tags["album"] == "Copycast Test Podcast"
    codecs = [
        (s["codec_name"], s.get("disposition", {}).get("attached_pic", 0)) for s in probe["streams"]
    ]
    assert ("mp3", 0) in codecs, "audio stream must still be mp3 (stream copy, never re-encoded)"
    assert ("mjpeg", 1) in codecs, "the Artwork must be an attached picture"

    phases = [p.phase for p in progress]
    assert phases[0] is EnginePhase.downloading
    assert EnginePhase.postprocessing in phases
    assert phases[-1] is EnginePhase.finished
    assert progress[-1].bytes_done == result.size_bytes
    downloading = [p for p in progress if p.phase is EnginePhase.downloading]
    assert all(p.bytes_total == TINY_MP3_BYTES for p in downloading)

    assert f"Destination: {spec.temp_dir / spec.item_id}.mp3" in log.text()
    assert sorted(p.name for p in spec.temp_dir.iterdir()) == []
    assert origin.hits("/media/tiny.mp3") >= 1 and origin.hits("/media/tiny.jpg") >= 1


def test_cancel_during_download_raises_cancelled_and_leaves_the_part_in_tmp(
    origin: Origin, tmp_path: Path
) -> None:
    spec = spec_for(origin, tmp_path, "fedcba9876543210")
    token = CancelToken()
    seen: list[Progress] = []

    def cancel_on_first_bytes(progress: Progress) -> None:
        seen.append(progress)
        if progress.phase is EnginePhase.downloading:
            token.cancel()

    with pytest.raises(Cancelled):
        YtDlpEngine().fetch_item(spec, {}, token, cancel_on_first_bytes, RecordingLog())
    parts = sorted(p.name for p in spec.temp_dir.iterdir())
    assert parts == [f"{spec.item_id}.mp3.part"]
    assert not (spec.home_dir / f"{spec.item_id}.mp3").exists()
    assert any(p.phase is EnginePhase.downloading for p in seen)


def test_missing_enclosure_is_permanent(origin: Origin, tmp_path: Path) -> None:
    synth = SynthItem(
        id="deadbeefdeadbeef",
        title="gone",
        url=origin.url_for("/media/missing.mp3"),
        mime="audio/mpeg",
    )
    spec = FetchSpec(
        FetchKind.direct, synth.url, synth.id, tmp_path / "media", tmp_path / "tmp", synth
    )
    with pytest.raises(PermanentError):
        YtDlpEngine().fetch_item(spec, {}, CancelToken(), lambda _: None, RecordingLog())
    assert not any((tmp_path / "media").glob("*.mp3"))


def test_ytdlp_kind_fetch_of_a_direct_media_url(origin: Origin, tmp_path: Path) -> None:
    """The generic extractor handles a bare media URL: same outputs, no synthesized dict."""
    spec = FetchSpec(
        FetchKind.ytdlp,
        origin.url_for("/media/tiny.mp3"),
        "abcdefabcdefabcd",
        tmp_path / "media",
        tmp_path / "tmp",
    )
    result = YtDlpEngine().fetch_item(spec, {}, CancelToken(), lambda _: None, RecordingLog())
    assert result.audio_path.name == "abcdefabcdefabcd.mp3"
    assert result.mime == "audio/mpeg"
    assert result.info_json_path is not None and result.info_json_path.exists()
    assert ffprobe(result.audio_path)["streams"][0]["codec_name"] == "mp3"


def _origin_with(tmp_path: Path, files: dict[str, bytes]) -> Origin:
    """An origin serving the tiny fixtures plus ``files`` (name -> bytes) under ``/media``."""
    root = tmp_path / "origin"
    (root / "media").mkdir(parents=True)
    from tests.support.origin import FIXTURES_DIR

    shutil.copy(FIXTURES_DIR / "media" / "tiny.mp3", root / "media" / "tiny.mp3")
    for name, data in files.items():
        (root / "media" / name).write_bytes(data)
    return Origin(root=root)


def _spec_with_thumbnail(origin: Origin, root: Path, thumbnail: str) -> FetchSpec:
    import dataclasses

    synth = dataclasses.replace(
        synth_for(origin, "0123456789abcdef"), thumbnail_url=origin.url_for(f"/media/{thumbnail}")
    )
    return FetchSpec(FetchKind.direct, synth.url, synth.id, root / "media", root / "tmp", synth)


def test_jpeg_served_under_a_png_name_is_renamed_converted_and_embedded(tmp_path: Path) -> None:
    """megaphone/imgix answer ``image.png?auto=format`` with a JPEG; ffmpeg must not choke."""
    from tests.support.origin import FIXTURES_DIR

    jpeg = (FIXTURES_DIR / "media" / "tiny.jpg").read_bytes()
    with _origin_with(tmp_path, {"cover.png": jpeg}) as origin:
        spec = _spec_with_thumbnail(origin, tmp_path / "work", "cover.png")
        log = RecordingLog()
        result = YtDlpEngine().fetch_item(spec, {}, CancelToken(), lambda _p: None, log)
    assert result.artwork_path == spec.home_dir / f"{spec.item_id}.jpg"
    assert result.artwork_path.read_bytes()[:3] == b"\xff\xd8\xff"
    codecs = [
        (s["codec_name"], s.get("disposition", {}).get("attached_pic", 0))
        for s in ffprobe(result.audio_path)["streams"]
    ]
    assert ("mjpeg", 1) in codecs
    assert "Correcting thumbnail" in log.text() and "extension to jpg" in log.text()
    assert "Conversion failed" not in log.text()


def test_an_unreadable_thumbnail_warns_and_the_audio_still_archives(tmp_path: Path) -> None:
    with _origin_with(tmp_path, {"cover.png": b"<!doctype html><p>not an image</p>"}) as origin:
        spec = _spec_with_thumbnail(origin, tmp_path / "work", "cover.png")
        log = RecordingLog()
        result = YtDlpEngine().fetch_item(spec, {}, CancelToken(), lambda _p: None, log)
    assert result.audio_path.is_file() and result.ext == "mp3"
    codecs = [s["codec_name"] for s in ffprobe(result.audio_path)["streams"]]
    assert "mp3" in codecs and "mjpeg" not in codecs
    warnings = [line for level, line in log.lines if level == "warning"]
    assert any("thumbnail" in line.lower() for line in warnings), log.text()
    assert not any(level == "error" for level, _ in log.lines), log.text()
