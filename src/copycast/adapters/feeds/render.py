"""Render a Mirror Feed or Inbox Feed from what the repositories provide.

The input is a small read model (:class:`FeedView`, :class:`ItemView`,
:class:`AssetView`) built from database rows, so rendering is a pure
function that golden tests exercise without Postgres. A Mirror whose Source
was RSS reproduces the original ``<channel>`` and ``<item>`` XML with only
the enclosure, mirrored assets and Copycast's own channel tags replaced;
every other feed is synthesized.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import TYPE_CHECKING, Final

from lxml import etree

from copycast.adapters.feeds.urls import asset_url, feed_url, media_url
from copycast.adapters.sources.rss import (
    NS_ATOM,
    NS_CONTENT,
    NS_ITUNES,
    NS_PODCAST,
    NSMAP,
    parse_fragment,
    tag,
)
from copycast.domain.enums import (
    ArchiveState,
    AssetFormat,
    AssetKind,
    AssetProvenance,
    AssetState,
    BackfillMode,
    FeedKind,
    SourceKind,
)
from copycast.domain.urls import COPYCAST_ARTWORK_PATH
from copycast.version import APP_VERSION

if TYPE_CHECKING:
    from lxml.etree import XmlElement as Element
else:  # lxml exposes no public element type at runtime
    Element = object

GENERATOR: Final = f"Copycast {APP_VERSION}"
INBOX_AUTHOR: Final = "Copycast"
INBOX_DESCRIPTION: Final = "Episodes saved to the {title} Inbox with Copycast."
CONTENT_TYPE: Final = "application/rss+xml; charset=utf-8"
DEFAULT_LANGUAGE: Final = "en"
CAPTION_FORMATS: Final = frozenset({AssetFormat.vtt, AssetFormat.srt})

TRANSCRIPT_MIME: Final[dict[AssetFormat, str]] = {
    AssetFormat.vtt: "text/vtt",
    AssetFormat.srt: "application/x-subrip",
    AssetFormat.json: "application/json",
    AssetFormat.text: "text/plain",
    AssetFormat.html: "text/html",
}
CHAPTERS_MIME: Final = "application/json+chapters"

_DROPPED_CHANNEL_CHILDREN: Final = frozenset(
    {
        (None, "item"),
        (None, "generator"),
        (None, "lastBuildDate"),
        (NS_ITUNES, "new-feed-url"),
    }
)


# --------------------------------------------------------------------------- read model


@dataclass(frozen=True, slots=True)
class AssetView:
    """One ``assets`` row; only ``archived`` rows with a ``local_path`` are served."""

    kind: AssetKind
    local_path: str | None = None
    state: AssetState = AssetState.archived
    provenance: AssetProvenance = AssetProvenance.mirrored
    language: str | None = None
    format: AssetFormat | None = None
    mime: str | None = None
    size_bytes: int | None = None
    remote_url: str | None = None

    @property
    def served(self) -> bool:
        return self.state is AssetState.archived and bool(self.local_path)


@dataclass(frozen=True, slots=True)
class ItemView:
    """One ``catalog_items`` row with its assets; rendered only when archived with media."""

    id: str
    ordinal: int
    title: str
    archive_state: ArchiveState = ArchiveState.archived
    listed: bool = True
    source_number: int | None = None
    source_season: int | None = None
    description: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    published_at_approximate: bool = False
    first_seen_at: datetime | None = None
    duration_seconds: int | None = None
    source_url: str | None = None
    artwork_url: str | None = None
    media_ext: str | None = None
    media_mime: str | None = None
    media_bytes: int | None = None
    source_item_xml: str | None = None
    archivable: bool = True
    assets: tuple[AssetView, ...] = ()

    @property
    def renderable(self) -> bool:
        return self.archive_state is ArchiveState.archived and bool(self.media_ext)

    @property
    def on_demand(self) -> bool:
        """Listed and archivable but not archived (Tombstones included): an Automatic feed
        lists it and archives it on request."""
        return self.listed and self.archivable and self.archive_state is not ArchiveState.archived


@dataclass(frozen=True, slots=True)
class FeedView:
    """One ``feeds`` row; ``assets`` carries the feed Artwork row when mirrored."""

    id: str
    kind: FeedKind
    title: str
    revision: int = 1
    title_override: str | None = None
    """Set when ``title`` is the operator's own: a preserved RSS channel gets it too."""
    description: str | None = None
    author: str | None = None
    artwork_url: str | None = None
    language: str | None = None
    link: str | None = None
    source_kind: SourceKind | None = None
    source_channel_xml: str | None = None
    last_modified: datetime | None = None
    backfill_mode: BackfillMode | None = None
    assets: tuple[AssetView, ...] = ()

    @property
    def automatic(self) -> bool:
        return self.backfill_mode is BackfillMode.automatic


@dataclass(frozen=True, slots=True)
class RenderedFeed:
    body: bytes
    last_modified: datetime
    revision: int
    content_type: str = CONTENT_TYPE


# --------------------------------------------------------------------------- entry point


def render_feed(
    feed: FeedView,
    items: Sequence[ItemView],
    *,
    base_url: str,
    now: datetime | None = None,
) -> RenderedFeed:
    """The feed document for ``feed`` and its archived ``items``.

    Items are ordered ``published_at DESC NULLS LAST, ordinal DESC``;
    tombstoned and never-archived items are excluded, Delisted ones kept. An
    Automatic feed also lists what is not archived yet, with a placeholder
    enclosure the media route fills on the first request.
    """
    current = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    last_modified = (feed.last_modified or current).astimezone(UTC).replace(microsecond=0)
    renderable = sorted(
        (item for item in items if item.renderable or (feed.automatic and item.on_demand)),
        key=_order_key,
    )
    self_url = feed_url(base_url, feed.id)
    feed_artwork = _local_url(base_url, feed.id, _pick_artwork(feed.assets)) or feed.artwork_url
    if feed_artwork is None and feed.kind is FeedKind.inbox:
        feed_artwork = base_url.rstrip("/") + COPYCAST_ARTWORK_PATH

    preserved = (
        feed.kind is FeedKind.mirror
        and feed.source_kind is SourceKind.rss
        and bool(feed.source_channel_xml)
    )
    root: Element | None = None
    channel: Element | None = None
    if preserved:
        assert feed.source_channel_xml is not None
        root, channel = _preserved_channel(
            feed.source_channel_xml, self_url, feed_artwork, last_modified
        )
    if root is None or channel is None:
        root, channel = _synthesized_channel(feed, base_url, self_url, feed_artwork, last_modified)
    elif feed.title_override:
        _set_title(channel, feed.title)

    for item in renderable:
        element = _preserved_item(feed, item, base_url) if preserved else None
        if element is None:
            element = _synthesized_item(feed, item, base_url, feed_artwork, current)
        channel.append(element)

    etree.cleanup_namespaces(root)
    body: bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
    return RenderedFeed(body=body, last_modified=last_modified, revision=feed.revision)


def _order_key(item: ItemView) -> tuple[int, float, int]:
    if item.published_at is None:
        return (1, 0.0, -item.ordinal)
    return (0, -item.published_at.timestamp(), -item.ordinal)


# --------------------------------------------------------------------------- channels


def _merged_nsmap(source: dict[str | None, str] | None) -> dict[str | None, str]:
    nsmap: dict[str | None, str] = dict(source or {})
    nsmap.pop(None, None)
    bound = set(nsmap.values())
    for prefix, uri in NSMAP.items():
        if uri in bound:
            continue
        candidate = prefix
        while candidate in nsmap:
            candidate = f"copycast-{candidate}"
        nsmap[candidate] = uri
        bound.add(uri)
    return nsmap


def _preserved_channel(
    source_xml: str, self_url: str, artwork: str | None, last_modified: datetime
) -> tuple[Element | None, Element | None]:
    try:
        source = parse_fragment(source_xml)
    except (etree.XMLSyntaxError, ValueError):
        return None, None
    local = etree.QName(source).localname
    if local == "rss":
        source_root, source_channel = source, source.find("channel")
    elif local == "channel":
        source_root, source_channel = None, source
    else:
        return None, None
    if source_channel is None:
        return None, None

    root = etree.Element("rss", nsmap=_merged_nsmap(source.nsmap))
    root.set("version", "2.0")
    if source_root is not None:
        for name, value in source_root.attrib.items():
            root.set(name, value)
    channel = etree.SubElement(root, "channel")
    for child in source_channel:
        if not isinstance(child.tag, str):
            continue
        qname = etree.QName(child)
        key = (qname.namespace, qname.localname)
        if key in _DROPPED_CHANNEL_CHILDREN:
            continue
        if key == (NS_ATOM, "link") and (child.get("rel") or "").strip().lower() == "self":
            continue
        channel.append(_copy(child))
    if artwork:
        _set_artwork(channel, artwork, create=False)
    _append_copycast_tags(channel, self_url, last_modified)
    return root, channel


def _synthesized_channel(
    feed: FeedView,
    base_url: str,
    self_url: str,
    artwork: str | None,
    last_modified: datetime,
) -> tuple[Element, Element]:
    root = etree.Element("rss", nsmap=_merged_nsmap(None))
    root.set("version", "2.0")
    channel = etree.SubElement(root, "channel")
    inbox = feed.kind is FeedKind.inbox
    _sub(channel, "title", feed.title)
    _sub(channel, "link", feed.link or base_url.rstrip("/") + "/")
    _sub(
        channel,
        "description",
        feed.description or (INBOX_DESCRIPTION.format(title=feed.title) if inbox else feed.title),
    )
    _sub(channel, "language", feed.language or DEFAULT_LANGUAGE)
    author = feed.author or (INBOX_AUTHOR if inbox else None)
    if author:
        _sub(channel, tag(NS_ITUNES, "author"), author)
    if artwork:
        etree.SubElement(channel, tag(NS_ITUNES, "image")).set("href", artwork)
    _sub(channel, tag(NS_ITUNES, "type"), "episodic")
    _sub(channel, tag(NS_ITUNES, "explicit"), "false")
    _append_copycast_tags(channel, self_url, last_modified)
    return root, channel


def _append_copycast_tags(channel: Element, self_url: str, last_modified: datetime) -> None:
    link = etree.SubElement(channel, tag(NS_ATOM, "link"))
    link.set("href", self_url)
    link.set("rel", "self")
    link.set("type", "application/rss+xml")
    _sub(channel, "generator", GENERATOR)
    _sub(channel, "lastBuildDate", rfc2822(last_modified))


# --------------------------------------------------------------------------- items


def _preserved_item(feed: FeedView, item: ItemView, base_url: str) -> Element | None:
    if not item.source_item_xml:
        return None
    try:
        element = parse_fragment(item.source_item_xml)
    except (etree.XMLSyntaxError, ValueError):
        return None
    if etree.QName(element).localname != "item":
        return None
    # An Automatic feed lists unarchived items too: the enclosure is then the placeholder.
    _replace_all(element, [_enclosure(base_url, feed, item)], (None, "enclosure"))
    for notes in (element.find("description"), element.find(tag(NS_CONTENT, "encoded"))):
        if notes is not None and notes.text:
            notes.text = localized_notes(notes.text, base_url, feed.id, item.assets)

    chapters = _pick_chapters(item.assets)
    if chapters is not None:
        _replace_all(
            element, [_chapters_element(base_url, feed.id, chapters)], (NS_PODCAST, "chapters")
        )
    transcripts = _pick_transcripts(item.assets)
    if transcripts:
        _replace_all(
            element,
            [_transcript_element(base_url, feed.id, t) for t in transcripts],
            (NS_PODCAST, "transcript"),
        )
    artwork = _local_url(base_url, feed.id, _pick_artwork(item.assets))
    if artwork:
        _set_artwork(element, artwork, create=True)
    return element


def localized_notes(
    text: str | None, base_url: str, feed_id: str, assets: Iterable[AssetView]
) -> str | None:
    """The show notes with every mirrored attachment's URL replaced by the local copy."""
    if not text:
        return text
    for asset in _served(assets, AssetKind.attachment):
        local = _local_url(base_url, feed_id, asset)
        if asset.remote_url and local and asset.remote_url in text:
            text = text.replace(asset.remote_url, local)
    return text


def approximate_phrase(published_at: datetime, now: datetime) -> str:
    """YouTube's own wording, regenerated from the approximate date: "3 weeks ago"."""
    seconds = max(0.0, (now - published_at).total_seconds())
    days = seconds / 86_400
    if days < 1:
        hours = int(seconds // 3_600)
        return "today" if hours < 1 else f"{hours} hour{'s' if hours != 1 else ''} ago"
    for unit, length in (("year", 365.0), ("month", 30.0), ("week", 7.0), ("day", 1.0)):
        count = int(days // length)
        if count >= 1:
            return f"{count} {unit}{'s' if count != 1 else ''} ago"
    return "today"


def with_approximate_note(description: str | None, published_at: datetime, now: datetime) -> str:
    """Tell the listener the date is YouTube's rough one until the video is archived."""
    note = (
        f"Published about {approximate_phrase(published_at, now)} "
        "(approximate date from YouTube; exact once downloaded)."
    )
    text = (description or "").strip()
    return f"{note}\n\n{text}" if text else note


def with_source_link(description: str | None, source_url: str | None) -> str | None:
    """The description with the Source's page appended (podcast apps rarely show ``<link>``)."""
    if not source_url:
        return description
    text = (description or "").rstrip()
    if source_url in text:
        return text or None
    return f"{text}\n\n{source_url}" if text else source_url


PLACEHOLDER_EXT = "mp3"
PLACEHOLDER_MIME = "audio/mpeg"
PLACEHOLDER_BYTES_PER_SECOND = 16_000
"""128 kbit/s: what an on-demand item's enclosure claims until the real file exists."""


def placeholder_length(duration_seconds: int | None) -> int:
    """A plausible size for an unarchived item; some apps reject a 0-byte enclosure."""
    return max(1, duration_seconds or 0) * PLACEHOLDER_BYTES_PER_SECOND


def _enclosure(base_url: str, feed: FeedView, item: ItemView) -> Element:
    """The enclosure of an archived item, or a placeholder an Automatic feed serves on demand."""
    enclosure = etree.Element("enclosure")
    enclosure.set("url", media_url(base_url, feed.id, item.id, item.media_ext or PLACEHOLDER_EXT))
    length = item.media_bytes if item.media_ext else placeholder_length(item.duration_seconds)
    enclosure.set("length", str(length or placeholder_length(item.duration_seconds)))
    enclosure.set(
        "type",
        item.media_mime or (PLACEHOLDER_MIME if not item.media_ext else "application/octet-stream"),
    )
    return enclosure


def _synthesized_item(
    feed: FeedView, item: ItemView, base_url: str, feed_artwork: str | None, now: datetime
) -> Element:
    element = etree.Element("item")
    _sub(element, "title", item.title)
    description = with_source_link(
        localized_notes(item.description, base_url, feed.id, item.assets), item.source_url
    )
    if item.published_at_approximate and item.published_at is not None:
        description = with_approximate_note(description, item.published_at, now)
    if description:
        _sub(element, "description", description)
    if item.source_url:
        _sub(element, "link", item.source_url)
    guid = _sub(element, "guid", item.source_url or f"urn:copycast:{item.id}")
    guid.set("isPermaLink", "false")
    published = item.first_seen_at if feed.kind is FeedKind.inbox else item.published_at
    _sub(element, "pubDate", rfc2822(published or item.first_seen_at or now))
    element.append(_enclosure(base_url, feed, item))
    if item.duration_seconds is not None:
        _sub(element, tag(NS_ITUNES, "duration"), str(int(item.duration_seconds)))
    number = item.source_number if item.source_number is not None else item.ordinal
    _sub(element, tag(NS_ITUNES, "episode"), str(number))
    if item.source_season is not None:
        _sub(element, tag(NS_ITUNES, "season"), str(item.source_season))
    _sub(element, tag(NS_ITUNES, "episodeType"), "full")
    author = item.author or feed.author
    if author:
        _sub(element, tag(NS_ITUNES, "author"), author)
    artwork = _local_url(base_url, feed.id, _pick_artwork(item.assets)) or item.artwork_url
    if artwork:
        etree.SubElement(element, tag(NS_ITUNES, "image")).set("href", artwork)
    chapters = _pick_chapters(item.assets)
    if chapters is not None:
        element.append(_chapters_element(base_url, feed.id, chapters))
    for transcript in _pick_transcripts(item.assets):
        element.append(_transcript_element(base_url, feed.id, transcript))
    return element


# --------------------------------------------------------------------------- assets


def _served(assets: Iterable[AssetView], kind: AssetKind) -> list[AssetView]:
    return [asset for asset in assets if asset.served and asset.kind is kind]


def _provenance_rank(asset: AssetView) -> int:
    return 0 if asset.provenance is AssetProvenance.mirrored else 1


def _pick_artwork(assets: Iterable[AssetView]) -> AssetView | None:
    candidates = sorted(_served(assets, AssetKind.artwork), key=_provenance_rank)
    return candidates[0] if candidates else None


def _pick_chapters(assets: Iterable[AssetView]) -> AssetView | None:
    candidates = sorted(_served(assets, AssetKind.chapters), key=_provenance_rank)
    return candidates[0] if candidates else None


def _pick_transcripts(assets: Iterable[AssetView]) -> list[AssetView]:
    """One transcript per (language, format); mirrored beats generated."""
    chosen: dict[tuple[str, str], AssetView] = {}
    for asset in sorted(_served(assets, AssetKind.transcript), key=_provenance_rank):
        key = ((asset.language or "").lower(), asset.format.value if asset.format else "")
        chosen.setdefault(key, asset)
    return [chosen[key] for key in sorted(chosen)]


def _local_url(base_url: str, feed_id: str, asset: AssetView | None) -> str | None:
    if asset is None or not asset.local_path:
        return None
    return asset_url(base_url, feed_id, asset.local_path)


def _chapters_element(base_url: str, feed_id: str, asset: AssetView) -> Element:
    element = etree.Element(tag(NS_PODCAST, "chapters"))
    assert asset.local_path is not None
    element.set("url", asset_url(base_url, feed_id, asset.local_path))
    element.set("type", asset.mime or CHAPTERS_MIME)
    return element


def _transcript_element(base_url: str, feed_id: str, asset: AssetView) -> Element:
    element = etree.Element(tag(NS_PODCAST, "transcript"))
    assert asset.local_path is not None
    element.set("url", asset_url(base_url, feed_id, asset.local_path))
    mime = asset.mime or (TRANSCRIPT_MIME[asset.format] if asset.format else "text/plain")
    element.set("type", mime)
    if asset.language:
        element.set("language", asset.language)
    if asset.format in CAPTION_FORMATS:
        element.set("rel", "captions")
    return element


# --------------------------------------------------------------------------- XML helpers


def _sub(parent: Element, name: str, text: str) -> Element:
    element = etree.SubElement(parent, name)
    element.text = text
    return element


def _copy(element: Element) -> Element:
    return parse_fragment(etree.tostring(element))


def _matches(element: Element, key: tuple[str | None, str]) -> bool:
    if not isinstance(element.tag, str):
        return False
    qname = etree.QName(element)
    return (qname.namespace, qname.localname) == key


def _replace_all(parent: Element, replacements: list[Element], key: tuple[str | None, str]) -> None:
    """Replace every child matching ``key`` with ``replacements`` at the first one's position."""
    existing = [child for child in parent if _matches(child, key)]
    index = parent.index(existing[0]) if existing else len(parent)
    for child in existing:
        parent.remove(child)
    for offset, replacement in enumerate(replacements):
        parent.insert(index + offset, replacement)


def _set_title(channel: Element, title: str) -> None:
    """Replace the channel's ``<title>`` (and ``itunes:title`` when present) with the operator's."""
    element = channel.find("title")
    if element is None:
        element = etree.Element("title")
        channel.insert(0, element)
    element.text = title
    itunes_title = channel.find(tag(NS_ITUNES, "title"))
    if itunes_title is not None:
        itunes_title.text = title


def _set_artwork(parent: Element, url: str, *, create: bool) -> None:
    image = parent.find(tag(NS_ITUNES, "image"))
    if image is not None:
        image.set("href", url)
        image.text = None
    elif create:
        etree.SubElement(parent, tag(NS_ITUNES, "image")).set("href", url)
    rss_image = parent.find("image/url")
    if rss_image is not None:
        rss_image.text = url


def rfc2822(value: datetime) -> str:
    """``Mon, 15 Jan 2024 08:00:00 GMT`` (always UTC)."""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return format_datetime(aware.astimezone(UTC).replace(microsecond=0), usegmt=True)


__all__ = [
    "CONTENT_TYPE",
    "GENERATOR",
    "NS_CONTENT",
    "AssetView",
    "FeedView",
    "ItemView",
    "RenderedFeed",
    "approximate_phrase",
    "localized_notes",
    "placeholder_length",
    "render_feed",
    "rfc2822",
    "with_source_link",
]
