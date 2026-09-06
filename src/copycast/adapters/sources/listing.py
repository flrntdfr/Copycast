"""Normalize a yt-dlp flat extraction into the domain's :class:`SourceListing`.

Nested playlists are flattened to their leaves, leaves are deduplicated by
``source_key`` (``{extractor_key}:{id}``), dates come from ``timestamp``,
then ``release_timestamp``, then ``upload_date``. Playlist-like Sources are
``oldest_first`` with ``source_number = playlist_index``; channels and tabs
are ``newest_first``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final, cast

from copycast.domain.enums import ListingOrder
from copycast.domain.listing import SourceListing, SourceListingItem
from copycast.domain.urls import youtube_tab

SERVICE_NAMES: Final[dict[str, str]] = {
    "youtube": "YouTube",
    "soundcloud": "SoundCloud",
    "vimeo": "Vimeo",
    "twitch": "Twitch",
    "bandcamp": "Bandcamp",
    "bilibili": "BiliBili",
    "copycast": "RSS",
}

PLAYLIST_EXTRACTORS: Final = frozenset({"soundcloud:set", "bandcamp:album", "vimeo:showcase"})
YOUTUBE_PLAYLIST_PREFIXES: Final = ("PL", "OL", "FL", "RD", "UU", "LL")
LEAF_TYPES: Final = frozenset({"video", "url", "url_transparent"})


def service_name(extractor: str | None) -> str:
    """``youtube:tab`` -> ``YouTube``; unknown extractors are capitalized."""
    if not extractor:
        return "Unknown"
    head = extractor.split(":", 1)[0].strip().lower()
    if not head:
        return "Unknown"
    return SERVICE_NAMES.get(head, head[:1].upper() + head[1:])


def is_playlist_like(info: Mapping[str, Any]) -> bool:
    """Playlist extractors and YouTube ``PL/OL/FL/RD`` ids list oldest first."""
    extractor = str(info.get("extractor") or "").lower()
    if extractor.endswith(":playlist") or extractor in PLAYLIST_EXTRACTORS:
        return True
    ident = info.get("id")
    if extractor.startswith("youtube") and isinstance(ident, str):
        return ident.startswith(YOUTUBE_PLAYLIST_PREFIXES)
    return False


def listing_order_of(info: Mapping[str, Any]) -> ListingOrder:
    return ListingOrder.oldest_first if is_playlist_like(info) else ListingOrder.newest_first


def as_mapping(value: object) -> Mapping[str, Any] | None:
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else None


def flatten(info: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    """Yield the leaf entries of ``info`` in served order (nested playlists recursed)."""
    kind = info.get("_type") or "video"
    if kind in LEAF_TYPES:
        yield info
        return
    if kind in {"playlist", "multi_video"}:
        entries = info.get("entries")
        if isinstance(entries, Sequence) and not isinstance(entries, str | bytes):
            for raw in cast(Sequence[object], entries):
                entry = as_mapping(raw)
                if entry is not None:
                    yield from flatten(entry)
        return
    yield info


def source_key_of(entry: Mapping[str, Any], parent_key: str | None = None) -> str | None:
    extractor_key = entry.get("extractor_key") or entry.get("ie_key") or parent_key
    ident = entry.get("id") or entry.get("url")
    if not extractor_key or ident is None:
        return None
    return f"{extractor_key}:{ident}"


def entry_date(entry: Mapping[str, Any]) -> datetime | None:
    """``timestamp`` -> ``release_timestamp`` -> ``upload_date`` (YYYYMMDD), all UTC."""
    for key in ("timestamp", "release_timestamp"):
        value = entry.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            try:
                return datetime.fromtimestamp(float(value), tz=UTC)
            except (OverflowError, OSError, ValueError):
                continue
    upload_date = entry.get("upload_date") or entry.get("release_date")
    if isinstance(upload_date, str) and len(upload_date) == 8 and upload_date.isdigit():
        try:
            return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def best_thumbnail(entry: Mapping[str, Any]) -> str | None:
    """The thumbnail with the highest ``preference`` then the largest width."""
    thumbnails = entry.get("thumbnails")
    best: tuple[float, float, int] | None = None
    best_url: str | None = None
    if isinstance(thumbnails, list):
        for index, raw in enumerate(cast(list[object], thumbnails)):
            thumb = as_mapping(raw)
            if thumb is None:
                continue
            url = thumb.get("url")
            if not isinstance(url, str) or not url:
                continue
            rank = (_num(thumb.get("preference")), _num(thumb.get("width")), index)
            if best is None or rank > best:
                best, best_url = rank, url
    if best_url:
        return best_url
    single = entry.get("thumbnail")
    return single if isinstance(single, str) and single else None


def _num(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _text(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return round(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def author_of(entry: Mapping[str, Any]) -> str | None:
    for key in ("uploader", "channel", "artist", "creator", "uploader_id"):
        text = _text(entry.get(key))
        if text:
            return text
    return None


def normalize(info: Mapping[str, Any], url: str) -> SourceListing:
    """Turn a sanitized yt-dlp info dict (flat playlist or single video) into a listing."""
    extractor = _text(info.get("extractor"))
    parent_key = _text(info.get("extractor_key")) or _text(info.get("ie_key"))
    order = listing_order_of(info)
    webpage_url = _text(info.get("webpage_url")) or _text(info.get("original_url")) or url
    tab = youtube_tab(webpage_url) or youtube_tab(url)
    seen: set[str] = set()
    items: list[SourceListingItem] = []
    for entry in flatten(info):
        key = source_key_of(entry, parent_key)
        if key is None or key in seen:
            continue
        seen.add(key)
        duration = _int(entry.get("duration"))
        items.append(
            SourceListingItem(
                source_key=key,
                source_url=_text(entry.get("webpage_url")) or _text(entry.get("url")),
                title=_text(entry.get("title")) or _text(entry.get("id")) or key,
                description=_text(entry.get("description")),
                published_at=entry_date(entry),
                duration_seconds=max(0, duration) if duration is not None else None,
                artwork_url=best_thumbnail(entry),
                author=author_of(entry),
                source_number=(
                    _int(entry.get("playlist_index"))
                    if order is ListingOrder.oldest_first
                    else None
                ),
                source_season=_int(entry.get("season_number")),
                position=len(items),
                tab=tab,
                enclosure_url=None,
                enclosure_type=None,
                archivable=True,
            )
        )
    return SourceListing(
        service=service_name(extractor),
        extractor_key=parent_key,
        title=_text(info.get("title")) or _text(info.get("playlist_title")) or None,
        description=_text(info.get("description")),
        author=author_of(info),
        artwork_url=best_thumbnail(info),
        webpage_url=webpage_url,
        language=_text(info.get("language")),
        listing_order=order,
        items=items,
        raw=dict(info),
    )


__all__ = [
    "PLAYLIST_EXTRACTORS",
    "SERVICE_NAMES",
    "author_of",
    "best_thumbnail",
    "entry_date",
    "flatten",
    "is_playlist_like",
    "listing_order_of",
    "normalize",
    "service_name",
    "source_key_of",
]
