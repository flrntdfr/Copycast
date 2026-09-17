"""Synthesized info dicts for direct (RSS enclosure) fetches.

The engine hands :func:`build`'s dict to ``YoutubeDL.process_ie_result`` so
an enclosure goes through exactly the same download, tagging and Artwork
pipeline as a yt-dlp extraction. ``extractor`` is always ``copycast:rss``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final
from urllib.parse import urlsplit

from copycast.application.ports import SynthItem
from copycast.domain.listing import SourceListingItem
from copycast.domain.media import (
    EXT_MIME,
    KNOWN_EXTS,
    MIME_EXT,
    clean_mime,
    mime_for_ext,
    url_ext,
)

EXTRACTOR: Final = "copycast:rss"
EXTRACTOR_KEY: Final = "CopycastRss"

# yt-dlp ext -> audio codec name (ffprobe's); the containers ``domain.media`` knows.
EXT_CODEC: Final[dict[str, str]] = {
    "mp3": "mp3",
    "m4a": "aac",
    "m4b": "aac",
    "mp4": "aac",
    "mov": "aac",
    "aac": "aac",
    "ogg": "vorbis",
    "oga": "vorbis",
    "ogv": "vorbis",
    "opus": "opus",
    "webm": "opus",
    "flac": "flac",
    "wav": "pcm_s16le",
    "aiff": "pcm_s16be",
}

# MIME type -> (yt-dlp ext, audio codec name): the domain table plus the codec per ext.
MIME_MEDIA: Final[dict[str, tuple[str, str | None]]] = {
    mime: (ext, EXT_CODEC.get(ext)) for mime, ext in MIME_EXT.items()
}


def media_type(mime: str | None, url: str | None) -> tuple[str | None, str | None]:
    """``(ext, acodec)`` from the enclosure MIME type, else from the URL extension."""
    clean = clean_mime(mime)
    if clean and clean in MIME_MEDIA:
        return MIME_MEDIA[clean]
    ext = url_ext(url)
    if ext is not None:
        return ext, EXT_CODEC.get(ext)
    return None, None


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
    "EXT_CODEC",
    "EXT_MIME",
    "KNOWN_EXTS",
    "MIME_MEDIA",
    "build",
    "clean_mime",
    "from_listing_item",
    "media_type",
    "mime_for_ext",
    "url_ext",
]
