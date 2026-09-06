"""An in-memory implementation of the Engine port.

Listings are scripted per URL; fetches write stub media and sidecars under
``spec.home_dir`` so storage code sees real files. Every call is recorded so
tests can assert on the options the engine saw.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from copycast.application.ports import (
    Cancelled,
    CancelToken,
    EngineError,
    EngineLog,
    EngineVersion,
    FetchResult,
    FetchSpec,
    Progress,
    ProgressCallback,
)
from copycast.domain.enums import EnginePhase, FetchKind
from copycast.domain.listing import SourceListing

FAKE_VERSION = EngineVersion(
    name="fake-engine",
    version="0.0.0",
    channel="test",
    release_date=date(2024, 1, 1),
    git_head="deadbeef",
    ffmpeg_version="fake 0.0",
)

STUB_AUDIO = b"\x00\x00\x00\x1cftypM4A \x00\x00\x00\x00M4A mp42isom" + b"\x00" * 100
"""A few bytes that start like an MP4 container; never decoded."""

CANCEL_POLL_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class FetchRecord:
    spec: FetchSpec
    options: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ListingRecord:
    url: str
    options: dict[str, Any]


@dataclass
class EngineRecords:
    listings: list[ListingRecord] = field(default_factory=list[ListingRecord])
    fetches: list[FetchRecord] = field(default_factory=list[FetchRecord])
    options_seen: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    @property
    def fetched_item_ids(self) -> list[str]:
        return [record.spec.item_id for record in self.fetches]

    @property
    def listed_urls(self) -> list[str]:
        return [record.url for record in self.listings]


class FakeEngine:
    """Implements :class:`copycast.application.ports.Engine` without yt-dlp."""

    def __init__(self, *, default_listing: SourceListing | None = None) -> None:
        self._lock = threading.Lock()
        self._listings: dict[str, SourceListing] = {}
        self._default_listing = default_listing
        self._failures: list[Exception] = []
        self._block: threading.Event | None = None
        self.records = EngineRecords()
        self.version_info = FAKE_VERSION
        self.audio_bytes = STUB_AUDIO
        self.duration_seconds: int | None = 61

    # ------------------------------------------------------------------ scripting

    def script_listing(self, url: str, listing: SourceListing) -> None:
        """Return ``listing`` for ``list_source(url)``; a trailing slash on ``url`` is tolerated."""
        with self._lock:
            self._listings[url] = listing

    def fail_next(self, exc: Exception) -> None:
        """Raise ``exc`` from the next engine call (listing or fetch), once per call to this."""
        with self._lock:
            self._failures.append(exc)

    def block_fetches(self, event: threading.Event) -> None:
        """Until ``event`` is set, fetches write a ``.part`` file and poll the cancel token.

        A fetch whose token is cancelled while blocked raises :class:`Cancelled`
        leaving the ``.part`` in ``temp_dir``; once the event is set the fetch
        completes normally. Pass an already-set event to unblock.
        """
        with self._lock:
            self._block = event

    def unblock(self) -> None:
        with self._lock:
            self._block = None

    def reset(self) -> None:
        with self._lock:
            self._listings.clear()
            self._failures.clear()
            self._block = None
            self.records = EngineRecords()

    # ------------------------------------------------------------------ port

    def version(self) -> EngineVersion:
        return self.version_info

    def list_source(
        self,
        url: str,
        options: Mapping[str, Any],
        cancel: CancelToken,
        log: EngineLog,
    ) -> SourceListing:
        opts = dict(options)
        with self._lock:
            self.records.listings.append(ListingRecord(url=url, options=opts))
            self.records.options_seen.append(opts)
            failure = self._failures.pop(0) if self._failures else None
            listing = self._listings.get(url) or self._listings.get(url.rstrip("/"))
        if failure is not None:
            log.error(f"fake engine failure: {failure}")
            raise failure
        if cancel.cancelled:
            raise Cancelled(f"listing of {url} cancelled")
        if listing is None:
            listing = self._default_listing
        if listing is None:
            raise EngineError(f"FakeEngine has no scripted listing for {url}")
        log.info(f"listed {url}: {len(listing.items)} items")
        return listing

    def fetch_item(
        self,
        spec: FetchSpec,
        options: Mapping[str, Any],
        cancel: CancelToken,
        on_progress: ProgressCallback,
        log: EngineLog,
    ) -> FetchResult:
        opts = dict(options)
        with self._lock:
            self.records.fetches.append(FetchRecord(spec=spec, options=opts))
            self.records.options_seen.append(opts)
            failure = self._failures.pop(0) if self._failures else None
            block = self._block
        if failure is not None:
            log.error(f"fake engine failure: {failure}")
            raise failure
        if spec.kind is FetchKind.direct and spec.synth is None:
            raise EngineError("direct fetch without a synthesized item")

        spec.home_dir.mkdir(parents=True, exist_ok=True)
        spec.temp_dir.mkdir(parents=True, exist_ok=True)
        total = len(self.audio_bytes)
        on_progress(Progress(EnginePhase.downloading, bytes_done=0, bytes_total=total))

        if block is not None and not block.is_set():
            part = spec.temp_dir / f"{spec.item_id}.m4a.part"
            part.write_bytes(self.audio_bytes[: total // 2])
            on_progress(Progress(EnginePhase.downloading, bytes_done=total // 2, bytes_total=total))
            while not block.wait(CANCEL_POLL_SECONDS):
                if cancel.cancelled:
                    log.warning(f"fetch of {spec.item_id} cancelled")
                    raise Cancelled(f"fetch of {spec.item_id} cancelled")
            part.unlink(missing_ok=True)

        if cancel.cancelled:
            raise Cancelled(f"fetch of {spec.item_id} cancelled")

        audio_path = spec.home_dir / f"{spec.item_id}.m4a"
        audio_path.write_bytes(self.audio_bytes)
        info_path = spec.home_dir / f"{spec.item_id}.info.json"
        info: dict[str, Any] = {
            "id": spec.synth.id if spec.synth else spec.item_id,
            "title": spec.synth.title if spec.synth else f"Item {spec.item_id}",
            "webpage_url": spec.url,
            "ext": "m4a",
            "acodec": "aac",
            "duration": self.duration_seconds,
            "extractor": spec.synth.extractor if spec.synth else "fake",
            "chapters": [],
        }
        info_path.write_text(json.dumps(info), encoding="utf-8")
        on_progress(Progress(EnginePhase.postprocessing, bytes_done=total, bytes_total=total))
        on_progress(Progress(EnginePhase.finished, bytes_done=total, bytes_total=total))
        log.info(f"fetched {spec.item_id}")
        return FetchResult(
            audio_path=audio_path,
            ext="m4a",
            mime="audio/mp4",
            size_bytes=total,
            duration_seconds=self.duration_seconds,
            info_json_path=info_path,
            artwork_path=None,
            subtitle_paths={},
            chapters=[],
            engine_version=self.version_info.version,
        )


def part_files(temp_dir: Path) -> list[Path]:
    return sorted(temp_dir.glob("*.part"))
