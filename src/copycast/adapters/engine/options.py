"""yt-dlp option layering: config ``[engine.options]`` -> Feed options -> ``BASE_OPTIONS``.

``BASE_OPTIONS`` is overlaid last so no operator or Feed setting can change
what Copycast owns (formats, post-processors, sidecars); the per-call keys
(paths, output template, hooks, logger) are added by the engine itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from copycast.application.ports import EngineLog
from copycast.domain.engine_options import ENGINE_OWNED_OPTIONS, EngineOptions
from copycast.domain.enums import FetchKind

YTDLP_FORMAT: Final = "bestaudio[ext=m4a]/bestaudio/best"
DIRECT_FORMAT: Final = "bestaudio/best"
LIVE_CHAT_EXCLUDE: Final = "-live_chat"
DEFAULT_SUBTITLE_LANGUAGE: Final = "en"

POSTPROCESSORS: Final[list[dict[str, Any]]] = [
    {"key": "FFmpegExtractAudio", "preferredcodec": "best"},
    {
        "key": "FFmpegMetadata",
        "add_metadata": True,
        "add_chapters": True,
        "add_infojson": False,
    },
]
"""The audio steps. The thumbnail convertor and embedder are added by the engine itself
(``ytdlp.ThumbnailConvertor`` / ``ytdlp.ThumbnailEmbedder``) so a bad image warns instead
of failing the whole fetch."""

BASE_OPTIONS: Final[dict[str, Any]] = {
    "format": YTDLP_FORMAT,
    "postprocessors": POSTPROCESSORS,
    "writethumbnail": True,
    "writeinfojson": True,
    "clean_infojson": True,
    "writesubtitles": True,
    "writeautomaticsub": False,
    "subtitlesformat": "vtt/srt/best",
    "noplaylist": True,
    "continuedl": True,
    "retries": 3,
    "fragment_retries": 3,
    "socket_timeout": 30,
    "noprogress": True,
    "quiet": True,
    "no_color": True,
    "overwrites": True,
    "allow_playlist_files": False,
}

LISTING_OPTIONS: Final[dict[str, Any]] = {
    "extract_flat": True,
    "lazy_playlist": False,
    "ignoreerrors": "only_download",
    "skip_download": True,
    "simulate": True,
    "writeinfojson": False,
    "writethumbnail": False,
    "writesubtitles": False,
    "writeautomaticsub": False,
    "allow_playlist_files": False,
    "postprocessors": [],
    "noprogress": True,
    "quiet": True,
    "no_color": True,
    "socket_timeout": 30,
    "retries": 3,
}


def with_cookiefile(options: Mapping[str, Any] | None, cookies_path: Path | None) -> dict[str, Any]:
    """Add the stored cookie file as ``cookiefile`` unless the options already name one."""
    merged = dict(options or {})
    if cookies_path is not None and "cookiefile" not in merged and cookies_path.is_file():
        merged["cookiefile"] = str(cookies_path)
    return merged


def strip_owned(options: Mapping[str, Any] | None) -> dict[str, Any]:
    """Drop every key Copycast owns; the layers below ``BASE_OPTIONS`` never carry them."""
    if not options:
        return {}
    return {key: value for key, value in options.items() if key not in ENGINE_OWNED_OPTIONS}


def subtitle_languages(language: str | None) -> list[str]:
    """``[feed language, 'en', '-live_chat']`` with the language's primary subtag added."""
    languages: list[str] = []
    for candidate in (language, DEFAULT_SUBTITLE_LANGUAGE):
        if not candidate:
            continue
        code = candidate.strip().lower().replace("_", "-")
        if not code:
            continue
        primary = code.split("-", 1)[0]
        for entry in (code, primary):
            if entry and entry not in languages:
                languages.append(entry)
    languages.append(LIVE_CHAT_EXCLUDE)
    return languages


def merge_options(
    global_options: Mapping[str, Any] | None,
    feed_options: Mapping[str, Any] | None = None,
    *,
    language: str | None = None,
) -> dict[str, Any]:
    """Layer config -> Feed -> ``BASE_OPTIONS`` (last wins) for a fetch.

    ``subtitleslangs`` defaults to :func:`subtitle_languages` of ``language``
    unless the Feed (or config) chose its own.
    """
    merged = EngineOptions.merge(strip_owned(global_options), strip_owned(feed_options), None)
    merged.update(BASE_OPTIONS)
    merged.setdefault("subtitleslangs", subtitle_languages(language))
    return merged


def fetch_params(
    options: Mapping[str, Any] | None,
    *,
    kind: FetchKind,
    item_id: str,
    home_dir: Path,
    temp_dir: Path,
    log: EngineLog | None = None,
) -> dict[str, Any]:
    """The complete ``YoutubeDL`` params for one fetch (hooks are added by the engine)."""
    params = merge_options(options)
    if kind is FetchKind.direct:
        params["format"] = DIRECT_FORMAT
    params["outtmpl"] = {"default": f"{item_id}.%(ext)s"}
    params["paths"] = {"home": str(home_dir), "temp": str(temp_dir)}
    if log is not None:
        params["logger"] = log
    return params


def listing_params(
    options: Mapping[str, Any] | None, *, log: EngineLog | None = None
) -> dict[str, Any]:
    """``YoutubeDL`` params for a flat listing: no downloads, no sidecars."""
    params = EngineOptions.merge(strip_owned(options), None, None)
    params.update(LISTING_OPTIONS)
    if log is not None:
        params["logger"] = log
    return params


__all__ = [
    "BASE_OPTIONS",
    "DIRECT_FORMAT",
    "LISTING_OPTIONS",
    "POSTPROCESSORS",
    "YTDLP_FORMAT",
    "fetch_params",
    "listing_params",
    "merge_options",
    "strip_owned",
    "subtitle_languages",
    "with_cookiefile",
]
