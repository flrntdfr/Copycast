"""The Engine port: the synchronous contract between Copycast and yt-dlp.

``adapters.engine`` implements it over ``yt_dlp`` (one ``YoutubeDL`` per
call); ``tests.support.fake_engine.FakeEngine`` implements it in memory.
Callers run it off the event loop with ``asyncio.to_thread``. The error
classes below are the only exceptions an implementation may let escape:
``errors.classify`` in the adapter maps yt-dlp's exceptions onto them.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from copycast.domain.enums import EnginePhase, FetchKind
from copycast.domain.listing import SourceListing

# --------------------------------------------------------------------------- errors


class EngineError(Exception):
    """Base class of every error an Engine raises."""


class TransientError(EngineError):
    """Network, timeout, 429/5xx, throttling, unknown: retry later."""


class PermanentError(EngineError):
    """Private, unavailable, members-only, geo, 401/403/404/410, no audio: do not retry."""


class StorageFull(EngineError):
    """ENOSPC / EDQUOT while writing: re-queue without counting the attempt."""


class Cancelled(EngineError):
    """The cancel token was set while the call was in flight."""


# --------------------------------------------------------------------------- values


@dataclass(frozen=True, slots=True)
class EngineVersion:
    """What ``about`` and ``--version`` report about the engine and ffmpeg."""

    name: str
    version: str
    channel: str
    release_date: date | None = None
    git_head: str | None = None
    ffmpeg_version: str | None = None


@dataclass(frozen=True, slots=True)
class SynthItem:
    """The synthesized info dict for a direct (RSS enclosure) fetch.

    ``synth.build`` in the engine adapter turns it into the dict handed to
    ``YoutubeDL.process_ie_result``; ``extractor`` is always ``copycast:rss``.
    """

    id: str
    title: str
    url: str
    mime: str | None = None
    ext: str | None = None
    acodec: str | None = None
    description: str | None = None
    timestamp: int | None = None
    duration: float | None = None
    artist: str | None = None
    album: str | None = None
    episode_number: int | None = None
    season_number: int | None = None
    thumbnail_url: str | None = None
    webpage_url: str | None = None
    extractor: str = "copycast:rss"


@dataclass(frozen=True, slots=True)
class FetchSpec:
    """One item to fetch.

    ``home_dir`` receives the finished audio and sidecars (yt-dlp ``paths.home``);
    ``temp_dir`` holds ``.part`` files (``paths.temp``). ``synth`` is set iff
    ``kind`` is ``direct``.
    """

    kind: FetchKind
    url: str
    item_id: str
    home_dir: Path
    temp_dir: Path
    synth: SynthItem | None = None


@dataclass(frozen=True, slots=True)
class FetchResult:
    """What a successful fetch produced under ``home_dir``.

    ``subtitle_paths`` maps a language code to the subtitle file the engine
    wrote; ``chapters`` is the ``chapters`` list of the info dict verbatim.
    """

    audio_path: Path
    ext: str
    mime: str
    size_bytes: int
    duration_seconds: int | None
    info_json_path: Path | None
    artwork_path: Path | None
    subtitle_paths: dict[str, Path] = field(default_factory=dict[str, Path])
    chapters: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    engine_version: str = ""


@dataclass(frozen=True, slots=True)
class Progress:
    """A snapshot from the engine's progress hooks."""

    phase: EnginePhase
    bytes_done: int | None = None
    bytes_total: int | None = None
    speed_bps: float | None = None
    eta_s: int | None = None

    @property
    def percent(self) -> float | None:
        if not self.bytes_total or self.bytes_done is None:
            return None
        return min(100.0, 100.0 * self.bytes_done / self.bytes_total)


class CancelToken(threading.Event):
    """A ``threading.Event`` the worker sets to ask the engine to stop."""

    def cancel(self) -> None:
        self.set()

    @property
    def cancelled(self) -> bool:
        return self.is_set()


class EngineLog(Protocol):
    """The logger interface yt-dlp expects; the worker batches it into ``job_log_lines``."""

    def debug(self, message: str) -> None: ...
    def info(self, message: str) -> None: ...
    def warning(self, message: str) -> None: ...
    def error(self, message: str) -> None: ...


ProgressCallback = Callable[[Progress], None]
EngineOptionsMap = Mapping[str, Any]


class NullEngineLog:
    """An EngineLog that discards everything (tests, probes)."""

    def debug(self, message: str) -> None:
        return None

    def info(self, message: str) -> None:
        return None

    def warning(self, message: str) -> None:
        return None

    def error(self, message: str) -> None:
        return None


# --------------------------------------------------------------------------- port


class Engine(Protocol):
    """Synchronous engine contract; every method may block for minutes."""

    def version(self) -> EngineVersion:
        """Engine and ffmpeg versions; must not touch the network."""
        ...

    def list_source(
        self,
        url: str,
        options: EngineOptionsMap,
        cancel: CancelToken,
        log: EngineLog,
    ) -> SourceListing:
        """Flat-extract ``url`` into a :class:`SourceListing` (no downloads)."""
        ...

    def fetch_item(
        self,
        spec: FetchSpec,
        options: EngineOptionsMap,
        cancel: CancelToken,
        on_progress: ProgressCallback,
        log: EngineLog,
    ) -> FetchResult:
        """Download one item's best audio with tags and Artwork embedded."""
        ...
