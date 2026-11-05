"""The fetch guard ahead of yt-dlp: refused URLs never reach ``YoutubeDL``."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yt_dlp

from copycast.adapters.engine.ytdlp import YtDlpEngine, guard_fetch_spec
from copycast.adapters.sources.guard import install_policy
from copycast.application.ports import CancelToken, EngineError, FetchSpec, SynthItem
from copycast.domain.destinations import DestinationPolicy
from copycast.domain.enums import FetchKind
from copycast.domain.exceptions import DestinationNotAllowed

PUBLIC_MP3 = "http://93.184.216.34/episode.mp3"


class _Log:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def debug(self, message: str) -> None: ...
    def info(self, message: str) -> None: ...
    def error(self, message: str) -> None: ...

    def warning(self, message: str) -> None:
        self.warnings.append(message)


class _Reached(Exception):
    """Raised by the stub: yt-dlp was asked to run."""


class _RecordingYoutubeDL:
    urls: list[str] = []

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params

    def __enter__(self) -> _RecordingYoutubeDL:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
        type(self).urls.append(url)
        raise _Reached(url)

    def process_ie_result(self, ie_result: dict[str, Any], download: bool = True) -> dict[str, Any]:
        type(self).urls.append(str(ie_result.get("url")))
        raise _Reached(ie_result.get("url"))

    def add_post_processor(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.fixture(autouse=True)
def no_exemptions(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Nothing exempt, and yt-dlp replaced by a stub that records what it was asked."""
    _RecordingYoutubeDL.urls = []
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _RecordingYoutubeDL)
    previous = install_policy(DestinationPolicy())
    try:
        yield
    finally:
        install_policy(previous)


def _direct(tmp_path: Path, url: str, thumbnail: str | None = None) -> FetchSpec:
    synth = SynthItem(id="x", title="A", url=url, thumbnail_url=thumbnail)
    return FetchSpec(FetchKind.direct, url, "x", tmp_path / "m", tmp_path / "t", synth)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8333/copycast/feeds/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::ffff:10.0.0.1]/feed",
        "file:///etc/passwd",
        "seaweedfs:8333/copycast",
    ],
)
def test_list_source_refuses_before_yt_dlp_runs(url: str) -> None:
    with pytest.raises(DestinationNotAllowed):
        YtDlpEngine().list_source(url, {}, CancelToken(), _Log())
    assert _RecordingYoutubeDL.urls == []


def test_list_source_hands_search_keys_and_public_urls_to_yt_dlp() -> None:
    engine = YtDlpEngine()
    for url in ("ytsearch5:a podcast", "https://93.184.216.34/playlist"):
        with pytest.raises(EngineError):  # the stub's refusal, classified: yt-dlp did run
            engine.list_source(url, {}, CancelToken(), _Log())
    assert _RecordingYoutubeDL.urls == ["ytsearch5:a podcast", "https://93.184.216.34/playlist"]


def test_fetch_item_refuses_an_internal_item_url(tmp_path: Path) -> None:
    spec = FetchSpec(FetchKind.ytdlp, "http://10.0.0.8/watch", "x", tmp_path / "m", tmp_path / "t")
    with pytest.raises(DestinationNotAllowed, match="private"):
        YtDlpEngine().fetch_item(spec, {}, CancelToken(), lambda _: None, _Log())
    assert _RecordingYoutubeDL.urls == []


def test_fetch_item_refuses_an_internal_enclosure(tmp_path: Path) -> None:
    spec = _direct(tmp_path, "http://192.168.1.10/episode.mp3")
    with pytest.raises(DestinationNotAllowed, match="private"):
        YtDlpEngine().fetch_item(spec, {}, CancelToken(), lambda _: None, _Log())
    assert _RecordingYoutubeDL.urls == []


def test_an_internal_artwork_url_is_dropped_not_fatal(tmp_path: Path) -> None:
    log = _Log()
    spec = _direct(tmp_path, PUBLIC_MP3, thumbnail="http://127.0.0.1:9333/cover.jpg")
    guarded = guard_fetch_spec(spec, log)
    assert guarded.synth is not None and guarded.synth.thumbnail_url is None
    assert guarded.synth.url == PUBLIC_MP3
    assert log.warnings and "artwork not embedded" in log.warnings[0]


def test_public_urls_pass_unchanged(tmp_path: Path) -> None:
    spec = _direct(tmp_path, PUBLIC_MP3, thumbnail="https://93.184.216.34/cover.jpg")
    assert guard_fetch_spec(spec, _Log()) is spec


def test_an_exempt_network_lets_the_engine_reach_it(tmp_path: Path) -> None:
    install_policy(DestinationPolicy.from_entries(["192.168.1.0/24"]))
    spec = _direct(tmp_path, "http://192.168.1.10/episode.mp3")
    with pytest.raises(EngineError):
        YtDlpEngine().fetch_item(spec, {}, CancelToken(), lambda _: None, _Log())
    assert _RecordingYoutubeDL.urls == ["http://192.168.1.10/episode.mp3"]
