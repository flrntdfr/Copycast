"""The fetch guard ahead of yt-dlp: refused URLs never reach ``YoutubeDL``."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yt_dlp

from copycast.adapters.engine.netguard import guarded_create_connection
from copycast.adapters.engine.ytdlp import YtDlpEngine, engine_claims, guard_fetch_spec
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
    with pytest.raises(DestinationNotAllowed, match="non-public"):
        YtDlpEngine().fetch_item(spec, {}, CancelToken(), lambda _: None, _Log())
    assert _RecordingYoutubeDL.urls == []


def test_fetch_item_refuses_an_internal_enclosure(tmp_path: Path) -> None:
    spec = _direct(tmp_path, "http://192.168.1.10/episode.mp3")
    with pytest.raises(DestinationNotAllowed, match="non-public"):
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


# --------------------------------------------------------------------------- Engine keys


@pytest.mark.parametrize(
    "key", ["nrk:MSUI12345678", "srgssr:rts:audio:123", "cbcplayer:2457781", "npo:VPWON_1234"]
)
def test_an_item_url_that_is_an_engine_key_passes(key: str, tmp_path: Path) -> None:
    """Broadcaster listings hand back ``nrk:<id>`` item URLs: the extractor resolves them."""
    assert engine_claims(key)
    spec = FetchSpec(FetchKind.ytdlp, key, "x", tmp_path / "m", tmp_path / "t")
    assert guard_fetch_spec(spec, _Log()) is spec


def test_engine_claims_ignores_the_generic_extractor() -> None:
    for value in ("seaweedfs:8333/copycast", "localhost:8080/x", "file:/etc/passwd"):
        assert not engine_claims(value)


def test_an_enclosure_must_be_a_url_even_when_an_extractor_claims_it(tmp_path: Path) -> None:
    with pytest.raises(DestinationNotAllowed, match="only http and https"):
        guard_fetch_spec(_direct(tmp_path, "nrk:MSUI12345678"), _Log())


def test_list_source_hands_an_engine_key_to_yt_dlp() -> None:
    with pytest.raises(EngineError):
        YtDlpEngine().list_source(":ytwatchlater", {}, CancelToken(), _Log())
    assert _RecordingYoutubeDL.urls == [":ytwatchlater"]


# --------------------------------------------------------------------------- refusals inside


class _RedirectedYoutubeDL(_RecordingYoutubeDL):
    """yt-dlp following a public page to an internal host: its connection is refused."""

    swallow = False

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any] | None:
        type(self).urls.append(url)
        try:
            guarded_create_connection(("10.0.0.5", 80))
        except OSError as exc:
            if type(self).swallow:  # ignoreerrors: yt-dlp only logs the error
                return None
            raise _Reached(f"Unable to download webpage: {exc}") from exc
        return {}

    def sanitize_info(self, info: dict[str, Any] | None) -> dict[str, Any] | None:
        return info


@pytest.mark.parametrize("swallow", [False, True])
def test_a_connection_refused_inside_a_listing_is_a_refusal(
    swallow: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _RedirectedYoutubeDL)
    monkeypatch.setattr(_RedirectedYoutubeDL, "swallow", swallow)
    with pytest.raises(DestinationNotAllowed, match=r"10\.0\.0\.5:80"):
        YtDlpEngine().list_source(PUBLIC_MP3, {}, CancelToken(), _Log())


def test_a_connection_refused_inside_a_fetch_is_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _RedirectedYoutubeDL)
    spec = FetchSpec(FetchKind.ytdlp, PUBLIC_MP3, "x", tmp_path / "m", tmp_path / "t")
    with pytest.raises(DestinationNotAllowed):
        YtDlpEngine().fetch_item(spec, {}, CancelToken(), lambda _: None, _Log())


def test_a_refusal_the_failure_does_not_mention_is_not_blamed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused thumbnail (yt-dlp warns and goes on) does not explain a later 404."""

    class _ThumbnailThen404(_RecordingYoutubeDL):
        def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
            with contextlib.suppress(OSError):
                guarded_create_connection(("127.0.0.1", 9333))
            raise _Reached("HTTP Error 404: Not Found")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _ThumbnailThen404)
    spec = FetchSpec(FetchKind.ytdlp, PUBLIC_MP3, "x", tmp_path / "m", tmp_path / "t")
    with pytest.raises(EngineError, match="404"):
        YtDlpEngine().fetch_item(spec, {}, CancelToken(), lambda _: None, _Log())
