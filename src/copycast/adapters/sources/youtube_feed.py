"""YouTube's channel and playlist Atom feeds: exact dates and descriptions for the newest videos.

A flat ``youtube:tab`` listing carries neither an upload date nor a description
(only an approximate "3 weeks ago" date when asked for one); fetching every
video would take one request per video and trip the bot check. The channel's
Atom feed (``/feeds/videos.xml?channel_id=UC...``, also ``playlist_id=``)
answers in one request, without cookies, with the exact ``<published>`` and the
full ``<media:description>`` of the fifteen newest videos. The listing gets
those merged in; older videos keep the approximate date until archived.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import parse_qs, urlsplit

from lxml import etree

from copycast.adapters.sources.http import Fetched, SourceError, fetch
from copycast.adapters.sources.rss import PARSER
from copycast.domain.listing import SourceListing, SourceListingItem

FEED_BASE: Final = "https://www.youtube.com/feeds/videos.xml"
FEED_MAX_BYTES: Final = 2 * 1024 * 1024
NS_ATOM: Final = "http://www.w3.org/2005/Atom"
NS_YT: Final = "http://www.youtube.com/xml/schemas/2015"
NS_MEDIA: Final = "http://search.yahoo.com/mrss/"
_PLAYLIST_PREFIXES: Final = ("PL", "UU", "OL", "LL", "FL", "RD")

Fetcher = Callable[[str], Fetched]


@dataclass(frozen=True, slots=True)
class FeedEntry:
    published_at: datetime | None
    description: str | None


def channel_feed_url(info: Mapping[str, Any]) -> str | None:
    """The Atom feed of a ``youtube:tab`` listing: by channel id, else by playlist id."""
    extractor = str(info.get("extractor") or "")
    if not extractor.startswith("youtube"):
        return None
    channel_id = info.get("channel_id")
    if isinstance(channel_id, str) and channel_id.startswith("UC"):
        return f"{FEED_BASE}?channel_id={channel_id}"
    ident = info.get("id")
    if isinstance(ident, str) and ident.startswith(_PLAYLIST_PREFIXES):
        return f"{FEED_BASE}?playlist_id={ident}"
    return None


def parse_channel_feed(data: bytes | str) -> dict[str, FeedEntry]:
    """``{video_id: FeedEntry}`` from the Atom document; unreadable input gives ``{}``."""
    try:
        root = etree.fromstring(data.encode("utf-8") if isinstance(data, str) else data, PARSER)
    except (etree.LxmlError, ValueError):
        return {}
    entries: dict[str, FeedEntry] = {}
    for entry in root.iterfind(f"{{{NS_ATOM}}}entry"):
        video_id = (entry.findtext(f"{{{NS_YT}}}videoId") or "").strip()
        if not video_id:
            continue
        published = _parse_date(entry.findtext(f"{{{NS_ATOM}}}published"))
        description = entry.findtext(f"{{{NS_MEDIA}}}group/{{{NS_MEDIA}}}description")
        description = description.strip() if description and description.strip() else None
        entries[video_id] = FeedEntry(published_at=published, description=description)
    return entries


def _parse_date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def video_id_of(item: SourceListingItem) -> str | None:
    """The YouTube video id of a listing item, from its URL or its source key."""
    if item.source_url:
        parts = urlsplit(item.source_url)
        if "youtube.com" in parts.netloc:
            value = parse_qs(parts.query).get("v", [""])[0]
            if value:
                return value
        if parts.netloc.endswith("youtu.be") and parts.path.strip("/"):
            return parts.path.strip("/").split("/")[0]
    key = item.source_key
    return key.rsplit(":", 1)[-1] if key else None


def enrich_listing(listing: SourceListing, entries: Mapping[str, FeedEntry]) -> SourceListing:
    """The listing with the feed's exact dates and descriptions on the videos it names."""
    if not entries:
        return listing
    items: list[SourceListingItem] = []
    changed = False
    for item in listing.items:
        entry = entries.get(video_id_of(item) or "")
        if entry is None:
            items.append(item)
            continue
        patch: dict[str, Any] = {}
        if entry.published_at is not None:
            patch["published_at"] = entry.published_at
            patch["published_at_exact"] = True
        if entry.description and not item.description:
            patch["description"] = entry.description
        if patch:
            changed = True
            items.append(item.model_copy(update=patch))
        else:
            items.append(item)
    return listing.model_copy(update={"items": items}) if changed else listing


def _default_fetch(url: str) -> Fetched:
    return fetch(url, max_bytes=FEED_MAX_BYTES, accept="application/atom+xml, application/xml")


def enrich_from_youtube_feed(
    listing: SourceListing, *, fetcher: Fetcher = _default_fetch
) -> tuple[SourceListing, int]:
    """Merge the channel feed into a fresh listing; ``(listing, videos matched)``.

    Raises :class:`SourceError` when the feed cannot be fetched; the caller
    keeps the listing it has.
    """
    url = channel_feed_url(listing.raw or {})
    if url is None:
        return listing, 0
    entries = parse_channel_feed(fetcher(url).body)
    if not entries:
        return listing, 0
    enriched = enrich_listing(listing, entries)
    matched = sum(1 for item in enriched.items if (video_id_of(item) or "") in entries)
    return enriched, matched


__all__ = [
    "FEED_BASE",
    "FeedEntry",
    "SourceError",
    "channel_feed_url",
    "enrich_from_youtube_feed",
    "enrich_listing",
    "parse_channel_feed",
    "video_id_of",
]
