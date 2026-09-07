"""The yt-dlp implementation of the Engine port: the only module importing ``yt_dlp``.

Every call builds one ``YoutubeDL`` with the layered options, wires the
progress and post-processor hooks (which raise ``DownloadCancelled`` when
the :class:`CancelToken` is set) and maps every yt-dlp exception through
:func:`errors.classify`.
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
import shutil
import subprocess
from collections.abc import Callable, Generator, Mapping
from datetime import date
from pathlib import Path
from typing import Any, Final, cast

import yt_dlp
from yt_dlp.postprocessor import (
    EmbedThumbnailPP,
    FFmpegExtractAudioPP,
    FFmpegMetadataPP,
    FFmpegThumbnailsConvertorPP,
)
from yt_dlp.utils import DownloadCancelled, PostProcessingError, replace_extension
from yt_dlp.version import CHANNEL, RELEASE_GIT_HEAD, __version__

from copycast.adapters.engine.errors import classify
from copycast.adapters.engine.options import fetch_params, listing_params, with_cookiefile
from copycast.adapters.engine.synth import build, mime_for_ext
from copycast.adapters.sources.listing import normalize
from copycast.adapters.storage.atomic import write_atomic
from copycast.adapters.storage.layout import Layout
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
    """Implements :class:`copycast.application.ports.Engine` over ``yt_dlp.YoutubeDL``.

    A cookie file stored under the data directory (``Layout.cookies_path``)
    rides along with every call as ``cookiefile`` unless the options name
    one; yt-dlp's refreshed cookies are written back atomically afterwards.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings
        self._cookies_path: Path | None = (
            Layout(settings.data_dir).cookies_path() if settings is not None else None
        )

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
        try:
            with cookie_scope(self._cookies_path, options) as with_cookies:
                params = listing_params(with_cookies, log=log)
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
        state = _HookState()
        pp_hook = _postprocessor_hook(cancel, on_progress, state)
        try:
            with cookie_scope(self._cookies_path, options) as with_cookies:
                params = fetch_params(
                    with_cookies,
                    kind=spec.kind,
                    item_id=spec.item_id,
                    home_dir=spec.home_dir,
                    temp_dir=spec.temp_dir,
                    log=log,
                )
                params["progress_hooks"] = [_progress_hook(cancel, on_progress, state)]
                params["postprocessor_hooks"] = [pp_hook]
                with yt_dlp.YoutubeDL(params) as ydl:
                    add_postprocessors(ydl, pp_hook)
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


# --------------------------------------------------------------------------- cookies


@contextlib.contextmanager
def cookie_scope(
    cookies_path: Path | None, options: Mapping[str, Any]
) -> Generator[dict[str, Any]]:
    """Options with ``cookiefile`` pointing at a private copy of the stored cookie file.

    yt-dlp rewrites the file it is given (sites rotate cookies), and two jobs
    may run at once, so each call works on its own copy and the shared file
    is replaced atomically only when the call changed it.
    """
    if cookies_path is None or "cookiefile" in options or not cookies_path.is_file():
        yield dict(options)
        return
    original = cookies_path.read_bytes()
    scratch = cookies_path.with_name(f"{cookies_path.stem}.{os.getpid()}.{id(options):x}.txt")
    scratch.write_bytes(original)
    try:
        yield with_cookiefile(options, scratch)
        try:
            updated = scratch.read_bytes()
        except OSError:
            return
        if updated and updated != original and cookies_path.is_file():
            write_atomic(cookies_path, updated)
    finally:
        scratch.unlink(missing_ok=True)


# --------------------------------------------------------------------------- thumbnails

IMAGE_MAGIC: Final[tuple[tuple[bytes, str], ...]] = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)


def sniff_image_ext(path: Path) -> str | None:
    """The extension the bytes of ``path`` call for, or ``None`` when it is no known image."""
    try:
        with path.open("rb") as handle:
            head = handle.read(16)
    except OSError:
        return None
    for magic, ext in IMAGE_MAGIC:
        if head.startswith(magic):
            return ext
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def fix_thumbnail_extensions(info: dict[str, Any], say: Callable[[str], None]) -> int:
    """Rename downloaded thumbnails whose extension lies about their bytes.

    A CDN that answers ``image.png?auto=format`` with a JPEG makes yt-dlp
    save ``x.png`` holding JPEG data; ffmpeg then picks its decoder from the
    name and fails. Returns how many files were renamed.
    """
    renamed = 0
    files_to_move = info.get("__files_to_move")
    for thumbnail in cast(list[dict[str, Any]], info.get("thumbnails") or []):
        current = thumbnail.get("filepath")
        if not isinstance(current, str) or not os.path.isfile(current):
            continue
        actual = sniff_image_ext(Path(current))
        ext = os.path.splitext(current)[1].lstrip(".").lower()
        if actual is None or actual == ext or (actual == "jpg" and ext == "jpeg"):
            continue
        target = replace_extension(current, actual)
        say(f'Correcting thumbnail "{current}" extension to {actual}')
        os.replace(current, target)
        thumbnail["filepath"] = target
        if isinstance(files_to_move, dict) and current in files_to_move:
            moved = cast(dict[str, Any], files_to_move)
            moved[target] = replace_extension(str(moved.pop(current)), actual)
        renamed += 1
    return renamed


class ThumbnailConvertor(FFmpegThumbnailsConvertorPP):
    """yt-dlp's convertor, with the extension fixed first and failures downgraded to warnings."""

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        try:
            fix_thumbnail_extensions(info, self.to_screen)
            files, info = super().run(info)
            return list(files), info
        except PostProcessingError as exc:
            self.report_warning(f"thumbnail conversion failed, keeping the original: {exc}")
            return [], info


class ThumbnailEmbedder(EmbedThumbnailPP):
    """yt-dlp's embedder, kept from overwriting artwork the file already carries, non-fatal.

    An enclosure often ships its own cover in the ID3 tags; the feed's episode
    image is then kept as a sidecar asset instead of replacing it.
    """

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        path = info.get("filepath")
        if isinstance(path, str) and has_embedded_artwork(Path(path)):
            self.to_screen(f"Keeping the artwork already embedded in {path}")
            return [], info
        try:
            files, info = super().run(info)
            return list(files), info
        except PostProcessingError as exc:
            self.report_warning(f"thumbnail not embedded: {exc}")
            return [], info


def has_embedded_artwork(path: Path) -> bool:
    """Whether ffprobe sees an attached picture (ID3 APIC, MP4 covr) in ``path``."""
    binary = shutil.which("ffprobe")
    if binary is None or not path.is_file():
        return False
    try:
        completed = subprocess.run(
            [binary, "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        streams = cast(
            list[dict[str, Any]], json.loads(completed.stdout or "{}").get("streams", [])
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    for stream in streams:
        disposition = cast(dict[str, Any], stream.get("disposition") or {})
        if stream.get("codec_type") == "video" and int(disposition.get("attached_pic") or 0):
            return True
    return False


# --------------------------------------------------------------------------- audio

PODCAST_EXTS: Final = frozenset({"mp3", "m4a"})
"""Containers every podcast app plays and that carry chapters and cover art: kept as they are."""
AAC_CONTAINERS: Final = frozenset({"mp4", "m4v", "mov", "mkv", "webm", "mka"})
"""AAC inside a video container is remuxed to m4a without touching the audio."""
TRANSCODE_CODEC: Final = "mp3"
TRANSCODE_QUALITY: Final = "192"


def audio_target(ext: str, codec: str | None) -> str:
    """``best`` (copy or remux) for mp3, m4a and AAC-in-a-container; ``mp3`` for the rest.

    Opus, Vorbis, FLAC, WAV and friends play in few podcast apps and cannot
    carry chapters the way mp3 and m4a do, so they are transcoded once.
    """
    ext = ext.lower().lstrip(".")
    if ext in PODCAST_EXTS:
        return "best"
    if codec == "aac" and ext in AAC_CONTAINERS:
        return "best"
    return TRANSCODE_CODEC


class AudioNormalizer(FFmpegExtractAudioPP):
    """yt-dlp's audio extraction with the target chosen per file by :func:`audio_target`."""

    def __init__(self, downloader: yt_dlp.YoutubeDL) -> None:
        super().__init__(downloader, preferredcodec="best", preferredquality=TRANSCODE_QUALITY)

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        ext = str(info.get("ext") or "")
        codec: str | None = None
        path = info.get("filepath")
        if ext.lower() not in PODCAST_EXTS and isinstance(path, str):
            codec = self.get_audio_codec(path)
        target = audio_target(ext, codec)
        self.mapping = target
        if target != "best":
            self.to_screen(
                f"Transcoding {ext} ({codec or 'unknown codec'}) to mp3 for podcast apps"
            )
        files, info = super().run(info)
        return list(files), info


def add_postprocessors(ydl: yt_dlp.YoutubeDL, hook: Hook) -> None:
    """The chain: thumbnail to JPEG before the download; then audio, metadata, artwork."""
    steps: tuple[tuple[Any, str], ...] = (
        (ThumbnailConvertor(ydl, format="jpg"), "before_dl"),
        (AudioNormalizer(ydl), "post_process"),
        (
            FFmpegMetadataPP(ydl, add_metadata=True, add_chapters=True, add_infojson=False),
            "post_process",
        ),
        (ThumbnailEmbedder(ydl, already_have_thumbnail=True), "post_process"),
    )
    for pp, when in steps:
        pp.add_progress_hook(hook)
        ydl.add_post_processor(pp, when=when)


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
    "IMAGE_MAGIC",
    "PODCAST_EXTS",
    "RESUMABLE_SUFFIXES",
    "AudioNormalizer",
    "ThumbnailConvertor",
    "ThumbnailEmbedder",
    "YtDlpEngine",
    "add_postprocessors",
    "audio_target",
    "build_engine",
    "collect_outputs",
    "cookie_scope",
    "discard_temp_leftovers",
    "engine_info",
    "ffmpeg_version",
    "fix_thumbnail_extensions",
    "has_embedded_artwork",
    "release_date",
    "sniff_image_ext",
]
