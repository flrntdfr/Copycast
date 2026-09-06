"""The yt-dlp implementation of the Engine port: the only module importing ``yt_dlp``.

Every call builds one ``YoutubeDL`` with the layered options, wires the
progress and post-processor hooks (which raise ``DownloadCancelled`` when
the :class:`CancelToken` is set) and maps every yt-dlp exception through
:func:`errors.classify`.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any, Final, cast

import yt_dlp
from yt_dlp.utils import DownloadCancelled
from yt_dlp.version import CHANNEL, RELEASE_GIT_HEAD, __version__

from copycast.adapters.engine.errors import classify
from copycast.adapters.engine.options import fetch_params, listing_params
from copycast.adapters.engine.synth import build, mime_for_ext
from copycast.adapters.sources.listing import normalize
from copycast.application.ports import (
    Cancelled,
    CancelToken,
    EngineLog,
    EngineVersion,
    FetchResult,
    FetchSpec,
    PermanentError,
    Progress,
    ProgressCallback,
)
from copycast.domain.enums import EnginePhase, FetchKind
from copycast.domain.listing import SourceListing
from copycast.settings import Settings

ENGINE_NAME: Final = "yt-dlp"
INFO_JSON_SUFFIX: Final = ".info.json"
ARTWORK_EXTS: Final = frozenset({"jpg", "jpeg", "png", "webp", "gif"})
SUBTITLE_EXTS: Final = frozenset({"vtt", "srt", "ass", "lrc", "ttml", "json3", "srv3", "sbv"})
CANCEL_MESSAGE: Final = "cancelled by Copycast"

Hook = Callable[[dict[str, Any]], None]


@functools.cache
def ffmpeg_version() -> str | None:
    """``ffmpeg -version``'s version token, cached for the process; None when absent."""
    binary = shutil.which("ffmpeg")
    if binary is None:
        return None
    try:
        completed = subprocess.run(
            [binary, "-version"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first = (completed.stdout or completed.stderr).splitlines()[:1]
    if not first:
        return None
    tokens = first[0].split()
    if len(tokens) >= 3 and tokens[0] == "ffmpeg" and tokens[1] == "version":
        return tokens[2]
    return first[0].strip() or None


def release_date(version: str) -> date | None:
    """``2026.08.19`` (or a nightly ``2026.08.19.123456``) -> ``date(2026, 8, 19)``."""
    parts = version.split(".")
    if len(parts) < 3:
        return None
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def engine_info() -> EngineVersion:
    """What ``about`` and ``--version`` report; touches no network."""
    return EngineVersion(
        name=ENGINE_NAME,
        version=__version__,
        channel=CHANNEL,
        release_date=release_date(__version__),
        git_head=RELEASE_GIT_HEAD,
        ffmpeg_version=ffmpeg_version(),
    )


class YtDlpEngine:
    """Implements :class:`copycast.application.ports.Engine` over ``yt_dlp.YoutubeDL``."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    def version(self) -> EngineVersion:
        return engine_info()

    def list_source(
        self,
        url: str,
        options: Mapping[str, Any],
        cancel: CancelToken,
        log: EngineLog,
    ) -> SourceListing:
        _check_cancel(cancel, f"listing of {url}")
        params = listing_params(options, log=log)
        try:
            with yt_dlp.YoutubeDL(params) as ydl:
                info = ydl.extract_info(url, download=False)
                info = ydl.sanitize_info(info)
        except Exception as exc:
            raise classify(exc) from exc
        if info is None:
            raise PermanentError(f"yt-dlp extracted nothing from {url}")
        _check_cancel(cancel, f"listing of {url}")
        return normalize(info, url)

    def fetch_item(
        self,
        spec: FetchSpec,
        options: Mapping[str, Any],
        cancel: CancelToken,
        on_progress: ProgressCallback,
        log: EngineLog,
    ) -> FetchResult:
        _check_cancel(cancel, f"fetch of {spec.item_id}")
        if spec.kind is FetchKind.direct and spec.synth is None:
            raise PermanentError(f"direct fetch of {spec.item_id} without a synthesized item")
        spec.home_dir.mkdir(parents=True, exist_ok=True)
        spec.temp_dir.mkdir(parents=True, exist_ok=True)
        params = fetch_params(
            options,
            kind=spec.kind,
            item_id=spec.item_id,
            home_dir=spec.home_dir,
            temp_dir=spec.temp_dir,
            log=log,
        )
        state = _HookState()
        params["progress_hooks"] = [_progress_hook(cancel, on_progress, state)]
        params["postprocessor_hooks"] = [_postprocessor_hook(cancel, on_progress, state)]
        try:
            with yt_dlp.YoutubeDL(params) as ydl:
                if spec.kind is FetchKind.direct:
                    assert spec.synth is not None
                    info = ydl.process_ie_result(build(spec.synth), download=True)
                else:
                    info = ydl.extract_info(spec.url, download=True)
        except Exception as exc:
            discard_temp_leftovers(spec)
            raise classify(exc) from exc
        if info is None:
            raise PermanentError(f"yt-dlp produced nothing for {spec.url}")
        if info.get("_type") == "playlist":
            raise PermanentError(f"{spec.url} is a playlist, not a single item")
        result = collect_outputs(spec, info, engine_version=__version__)
        on_progress(
            Progress(
                EnginePhase.finished, bytes_done=result.size_bytes, bytes_total=result.size_bytes
            )
        )
        return result


def build_engine(settings: Settings) -> YtDlpEngine:
    return YtDlpEngine(settings)


# --------------------------------------------------------------------------- hooks


def _check_cancel(cancel: CancelToken, what: str) -> None:
    if cancel.cancelled:
        raise Cancelled(f"{what} cancelled")


class _HookState:
    """Shared between the hooks: post-processing is only reported once a download finished.

    yt-dlp runs the thumbnail converter *before* the download; without this
    gate the job would appear to post-process, then download.
    """

    __slots__ = ("downloaded",)

    def __init__(self) -> None:
        self.downloaded = False


def _progress_hook(cancel: CancelToken, on_progress: ProgressCallback, state: _HookState) -> Hook:
    def hook(status: dict[str, Any]) -> None:
        stage = status.get("status")
        if stage == "downloading":
            phase = EnginePhase.downloading
        elif stage == "finished":
            phase = EnginePhase.postprocessing
            state.downloaded = True
        else:
            phase = None
        if phase is not None:
            total = _int(status.get("total_bytes")) or _int(status.get("total_bytes_estimate"))
            done = _int(status.get("downloaded_bytes"))
            if phase is EnginePhase.postprocessing and done is None:
                done = total
            on_progress(
                Progress(
                    phase,
                    bytes_done=done,
                    bytes_total=total,
                    speed_bps=_float(status.get("speed")),
                    eta_s=_int(status.get("eta")),
                )
            )
        if cancel.cancelled:
            raise DownloadCancelled(CANCEL_MESSAGE)

    return hook


def _postprocessor_hook(
    cancel: CancelToken, on_progress: ProgressCallback, state: _HookState
) -> Hook:
    def hook(status: dict[str, Any]) -> None:
        if state.downloaded and status.get("status") in {"started", "processing"}:
            on_progress(Progress(EnginePhase.postprocessing))
        if cancel.cancelled:
            raise DownloadCancelled(CANCEL_MESSAGE)

    return hook


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    return None


def _float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


# --------------------------------------------------------------------------- outputs

RESUMABLE_SUFFIXES: Final = (".part", ".ytdl")


def discard_temp_leftovers(spec: FetchSpec) -> None:
    """Remove ``{item_id}.*`` files yt-dlp parked in ``temp_dir`` except the resumable ones.

    yt-dlp converts the thumbnail before the download and only moves it home
    with the finished audio, so a cancelled or failed fetch leaves it in
    ``tmp/`` next to the ``.part`` file; only the latter is worth keeping.
    """
    prefix = f"{spec.item_id}."
    try:
        entries = list(spec.temp_dir.iterdir())
    except OSError:
        return
    for path in entries:
        if not path.name.startswith(prefix) or path.name.endswith(RESUMABLE_SUFFIXES):
            continue
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            continue


def collect_outputs(
    spec: FetchSpec, info: Mapping[str, Any], *, engine_version: str
) -> FetchResult:
    """Locate what yt-dlp left under ``home_dir`` for ``spec.item_id``.

    The audio file is the final ``filepath`` of the requested download when
    it exists there, else the one file with a media extension among the
    ``{item_id}.*`` siblings. Sidecars are recognised by suffix.
    """
    prefix = f"{spec.item_id}."
    audio_path = _final_audio_path(spec.home_dir, info)
    info_json_path: Path | None = None
    artwork_path: Path | None = None
    subtitle_paths: dict[str, Path] = {}
    for path in sorted(spec.home_dir.iterdir()):
        name = path.name
        if not name.startswith(prefix) or not path.is_file():
            continue
        rest = name[len(prefix) :]
        if rest == "info.json":
            info_json_path = path
            continue
        parts = rest.split(".")
        ext = parts[-1].lower()
        if len(parts) == 1 and ext in ARTWORK_EXTS:
            artwork_path = path
        elif len(parts) >= 2 and ext in SUBTITLE_EXTS:
            language = ".".join(parts[:-1])
            subtitle_paths.setdefault(language, path)
        elif audio_path is None and len(parts) == 1 and ext not in ARTWORK_EXTS:
            audio_path = path
    if audio_path is None or not audio_path.is_file():
        raise PermanentError(f"yt-dlp produced no audio file for {spec.item_id}")
    ext = audio_path.suffix.lstrip(".").lower()
    duration = info.get("duration")
    chapters_raw = info.get("chapters")
    chapters: list[dict[str, Any]] = []
    if isinstance(chapters_raw, list):
        for chapter in cast(list[object], chapters_raw):
            if isinstance(chapter, Mapping):
                chapters.append(dict(cast(Mapping[str, Any], chapter)))
    return FetchResult(
        audio_path=audio_path,
        ext=ext,
        mime=mime_for_ext(ext),
        size_bytes=audio_path.stat().st_size,
        duration_seconds=round(duration) if isinstance(duration, int | float) else None,
        info_json_path=info_json_path,
        artwork_path=artwork_path,
        subtitle_paths=subtitle_paths,
        chapters=chapters,
        engine_version=engine_version,
    )


def _final_audio_path(home_dir: Path, info: Mapping[str, Any]) -> Path | None:
    candidates: list[object] = []
    downloads = info.get("requested_downloads")
    if isinstance(downloads, list):
        for entry in cast(list[object], downloads):
            if isinstance(entry, Mapping):
                candidates.append(cast(Mapping[str, Any], entry).get("filepath"))
    candidates.append(info.get("filepath"))
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return path
        sibling = home_dir / path.name
        if sibling.is_file():
            return sibling
    return None


__all__ = [
    "ENGINE_NAME",
    "RESUMABLE_SUFFIXES",
    "YtDlpEngine",
    "build_engine",
    "collect_outputs",
    "discard_temp_leftovers",
    "engine_info",
    "ffmpeg_version",
    "release_date",
]
