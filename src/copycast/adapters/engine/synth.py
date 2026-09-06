"""Synthesized info dicts for direct (RSS enclosure) fetches.

The engine hands :func:`build`'s dict to ``YoutubeDL.process_ie_result`` so
an enclosure goes through exactly the same download, tagging and Artwork
pipeline as a yt-dlp extraction. ``extractor`` is always ``copycast:rss``.
"""

from __future__ import annotations

from datetime import datetime
from posixpath import basename
from typing import Any, Final
from urllib.parse import urlsplit

from copycast.application.ports import SynthItem
from copycast.domain.listing import SourceListingItem

EXTRACTOR: Final = "copycast:rss"
EXTRACTOR_KEY: Final = "CopycastRss"

# MIME type -> (yt-dlp ext, audio codec name). Codec names follow ffprobe.
MIME_MEDIA: Final[dict[str, tuple[str, str | None]]] = {
    "audio/mpeg": ("mp3", "mp3"),
    "audio/mp3": ("mp3", "mp3"),
    "audio/mpeg3": ("mp3", "mp3"),
    "audio/x-mpeg": ("mp3", "mp3"),
    "audio/x-mp3": ("mp3", "mp3"),
    "audio/mp4": ("m4a", "aac"),
    "audio/x-m4a": ("m4a", "aac"),
    "audio/m4a": ("m4a", "aac"),
    "audio/mp4a-latm": ("m4a", "aac"),
    "audio/aac": ("aac", "aac"),
    "audio/aacp": ("aac", "aac"),
    "audio/x-aac": ("aac", "aac"),
    "audio/ogg": ("ogg", "vorbis"),
    "audio/vorbis": ("ogg", "vorbis"),
    "audio/x-vorbis": ("ogg", "vorbis"),
    "audio/opus": ("opus", "opus"),
    "audio/x-opus": ("opus", "opus"),
    "audio/webm": ("webm", "opus"),
    "audio/flac": ("flac", "flac"),
    "audio/x-flac": ("flac", "flac"),
    "audio/wav": ("wav", "pcm_s16le"),
    "audio/x-wav": ("wav", "pcm_s16le"),
    "audio/wave": ("wav", "pcm_s16le"),
    "audio/vnd.wave": ("wav", "pcm_s16le"),
    "audio/aiff": ("aiff", "pcm_s16be"),
    "audio/x-aiff": ("aiff", "pcm_s16be"),
    "video/mp4": ("mp4", "aac"),
    "video/x-m4v": ("mp4", "aac"),
    "video/quicktime": ("mov", "aac"),
    "video/webm": ("webm", "opus"),
    "video/ogg": ("ogv", "vorbis"),
}

EXT_MIME: Final[dict[str, str]] = {
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "m4b": "audio/mp4",
    "mp3": "audio/mpeg",
    "aac": "audio/aac",
    "ogg": "audio/ogg",
    "oga": "audio/ogg",
    "opus": "audio/opus",
    "webm": "audio/webm",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "aiff": "audio/aiff",
    "mka": "audio/x-matroska",
    "mov": "video/quicktime",
    "ogv": "video/ogg",
}

KNOWN_EXTS: Final = frozenset(EXT_MIME)


def clean_mime(mime: str | None) -> str | None:
    if not mime:
        return None
    value = mime.split(";", 1)[0].strip().lower()
    return value or None


def url_ext(url: str | None) -> str | None:
    if not url:
        return None
    name = basename(urlsplit(url).path)
    if "." not in name:
        return None
    ext = name.rsplit(".", 1)[1].lower()
    return ext if ext in KNOWN_EXTS else None


def media_type(mime: str | None, url: str | None) -> tuple[str | None, str | None]:
    """``(ext, acodec)`` from the enclosure MIME type, else from the URL extension."""
    clean = clean_mime(mime)
    if clean and clean in MIME_MEDIA:
        return MIME_MEDIA[clean]
    ext = url_ext(url)
    if ext is not None:
        return ext, MIME_MEDIA.get(EXT_MIME[ext], (ext, None))[1]
    return None, None


def mime_for_ext(ext: str) -> str:
    """The MIME type Copycast serves for a media extension (``audio/mp4`` for m4a)."""
    return EXT_MIME.get(ext.lower().lstrip("."), "application/octet-stream")


def build(item: SynthItem) -> dict[str, Any]:
    """The info dict for ``YoutubeDL.process_ie_result(d, download=True)``."""
    ext, acodec = item.ext, item.acodec
    if ext is None or acodec is None:
        guessed_ext, guessed_codec = media_type(item.mime, item.url)
        ext = ext or guessed_ext
        acodec = acodec or guessed_codec
    scheme = urlsplit(item.url).scheme.lower()
    info: dict[str, Any] = {
        "_type": "video",
        "id": item.id,
        "title": item.title,
        "url": item.url,
        "webpage_url": item.webpage_url or item.url,
        "extractor": item.extractor,
        "extractor_key": EXTRACTOR_KEY,
        "format_id": "enclosure",
        "protocol": scheme if scheme in {"http", "https"} else "http",
        "vcodec": "none",
        "ext": ext,
        "acodec": acodec,
        "description": item.description,
        "timestamp": item.timestamp,
        "duration": item.duration,
        "artist": item.artist,
        "uploader": item.artist,
        "album": item.album,
        "episode_number": item.episode_number,
        "season_number": item.season_number,
        "thumbnails": [{"url": item.thumbnail_url, "id": "0"}] if item.thumbnail_url else None,
    }
    return {key: value for key, value in info.items() if value is not None}


def from_listing_item(
    item: SourceListingItem,
    *,
    item_id: str,
    feed_title: str | None = None,
    feed_author: str | None = None,
    feed_artwork_url: str | None = None,
) -> SynthItem:
    """A :class:`SynthItem` for a Catalog item that came from an RSS Source."""
    if not item.enclosure_url:
        raise ValueError(f"item {item.source_key!r} has no enclosure to fetch")
    ext, acodec = media_type(item.enclosure_type, item.enclosure_url)
    return SynthItem(
        id=item_id,
        title=item.title,
        url=item.enclosure_url,
        mime=clean_mime(item.enclosure_type),
        ext=ext,
        acodec=acodec,
        description=item.description,
        timestamp=_timestamp(item.published_at),
        duration=float(item.duration_seconds) if item.duration_seconds is not None else None,
        artist=item.author or feed_author,
        album=feed_title,
        episode_number=item.source_number,
        season_number=item.source_season,
        thumbnail_url=item.artwork_url or feed_artwork_url,
        webpage_url=item.source_url,
    )


def _timestamp(value: datetime | None) -> int | None:
    return int(value.timestamp()) if value is not None else None


__all__ = [
    "EXTRACTOR",
    "EXTRACTOR_KEY",
    "EXT_MIME",
    "MIME_MEDIA",
    "build",
    "clean_mime",
    "from_listing_item",
    "media_type",
    "mime_for_ext",
    "url_ext",
]
