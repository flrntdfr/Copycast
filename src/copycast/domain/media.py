"""Media containers: the extension a Catalog item's public URL ends with, and its MIME type.

The URL a feed advertises for an item is ``{base}/feeds/{feed}/media/{item_id}.{public_ext}``
and ``public_ext`` is fixed when the row is created: podcast apps treat a changed enclosure
URL as a new file and download it again on every device. :func:`predicted_ext` makes that
prediction from what the listing knows; :func:`mime_for_ext` is the reverse table, the
``type`` a feed claims for an enclosure that is not archived yet.
"""

from __future__ import annotations

from posixpath import basename
from typing import Final
from urllib.parse import urlsplit

from copycast.domain.enums import SourceKind

DEFAULT_EXT: Final = "mp3"
"""What an RSS enclosure of unknown type is taken to be."""
ENGINE_EXT: Final = "m4a"
"""The container the Engine prefers (``bestaudio[ext=m4a]``): what a ytdlp Source archives to."""
FALLBACK_MIME: Final = "application/octet-stream"

MIME_EXT: Final[dict[str, str]] = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mpeg3": "mp3",
    "audio/x-mpeg": "mp3",
    "audio/x-mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/m4a": "m4a",
    "audio/mp4a-latm": "m4a",
    "audio/aac": "aac",
    "audio/aacp": "aac",
    "audio/x-aac": "aac",
    "audio/ogg": "ogg",
    "audio/vorbis": "ogg",
    "audio/x-vorbis": "ogg",
    "audio/opus": "opus",
    "audio/x-opus": "opus",
    "audio/webm": "webm",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/vnd.wave": "wav",
    "audio/aiff": "aiff",
    "audio/x-aiff": "aiff",
    "video/mp4": "mp4",
    "video/x-m4v": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
    "video/ogg": "ogv",
}
"""Enclosure MIME type -> the extension the Engine gives the file (yt-dlp's ``ext``)."""

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
"""Media extension -> the MIME type Copycast serves the file under."""

KNOWN_EXTS: Final = frozenset(EXT_MIME)


def clean_mime(mime: str | None) -> str | None:
    """``"Audio/MPEG; charset=binary"`` -> ``"audio/mpeg"``; ``None`` when blank."""
    if not mime:
        return None
    value = mime.split(";", 1)[0].strip().lower()
    return value or None


def url_ext(url: str | None) -> str | None:
    """The URL path's extension when it names a known media container, else ``None``."""
    if not url:
        return None
    name = basename(urlsplit(url).path)
    if "." not in name:
        return None
    ext = name.rsplit(".", 1)[1].lower()
    return ext if ext in KNOWN_EXTS else None


def ext_for_mime(mime: str | None) -> str | None:
    """The extension of a known enclosure MIME type, else ``None``."""
    clean = clean_mime(mime)
    return MIME_EXT.get(clean) if clean else None


def mime_for_ext(ext: str) -> str:
    """The MIME type Copycast serves for a media extension (``audio/mp4`` for m4a)."""
    return EXT_MIME.get(ext.lower().lstrip("."), FALLBACK_MIME)


def predicted_ext(
    source_kind: SourceKind | str | None,
    enclosure_type: str | None = None,
    enclosure_url: str | None = None,
) -> str:
    """The extension of the URL a feed will advertise for a new Catalog item, for good.

    A ytdlp Source archives to the Engine's container (``m4a``) whatever the listing
    says. An RSS enclosure follows its MIME type, else its URL's known extension, else
    ``mp3``. An Inbox (no Source) is filled by the Engine too, unless the Request named
    a media file whose type or URL says otherwise.
    """
    kind = SourceKind(source_kind) if source_kind else None
    if kind is SourceKind.ytdlp:
        return ENGINE_EXT
    ext = ext_for_mime(enclosure_type) or url_ext(enclosure_url)
    if ext is not None:
        return ext
    return DEFAULT_EXT if kind is SourceKind.rss else ENGINE_EXT


__all__ = [
    "DEFAULT_EXT",
    "ENGINE_EXT",
    "EXT_MIME",
    "FALLBACK_MIME",
    "KNOWN_EXTS",
    "MIME_EXT",
    "clean_mime",
    "ext_for_mime",
    "mime_for_ext",
    "predicted_ext",
    "url_ext",
]
