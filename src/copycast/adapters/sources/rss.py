"""RSS Sources: hardened parsing into a :class:`SourceListing` plus the original elements.

Identity is the trimmed ``<guid>`` text, else the enclosure URL; items with
neither are skipped; items without an enclosure are listed but not
archivable. The original ``<channel>`` (with its ``<rss>`` wrapper and
namespace declarations, items removed) and every ``<item>`` are kept so the
Mirror Feed can reproduce them verbatim.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from posixpath import basename
from typing import TYPE_CHECKING, Final
from urllib.parse import urljoin, urlsplit

import httpx
from lxml import etree

from copycast.adapters.sources.http import (
    MAX_FEED_BYTES,
    BodyTooLarge,
    Fetched,
    NotAFeed,
    fetch,
)
from copycast.domain.enums import ListingOrder
from copycast.domain.listing import SourceListing, SourceListingItem

if TYPE_CHECKING:
    from lxml.etree import XmlElement as Element
else:  # lxml exposes no public element type at runtime
    Element = object

NS_ITUNES: Final = "http://www.itunes.com/dtds/podcast-1.0.dtd"
NS_PODCAST: Final = "https://podcastindex.org/namespace/1.0"
NS_ATOM: Final = "http://www.w3.org/2005/Atom"
NS_CONTENT: Final = "http://purl.org/rss/1.0/modules/content/"
NS_DC: Final = "http://purl.org/dc/elements/1.1/"
NS_MEDIA: Final = "http://search.yahoo.com/mrss/"

NSMAP: Final[dict[str, str]] = {
    "itunes": NS_ITUNES,
    "podcast": NS_PODCAST,
    "atom": NS_ATOM,
    "content": NS_CONTENT,
}

MAX_PAGES: Final = 20
SERVICE_RSS: Final = "RSS"
FEED_ACCEPT: Final = "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.1"

PARSER: Final = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    huge_tree=False,
    remove_blank_text=False,
    recover=False,
)

_DURATION_RE: Final = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")


def tag(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"


@dataclass(frozen=True, slots=True)
class ParsedFeed:
    """The listing and the source elements of one RSS document."""

    listing: SourceListing
    root: Element
    channel: Element
    items: dict[str, Element] = field(default_factory=dict[str, Element])
    next_url: str | None = None

    def item_element(self, source_key: str) -> Element | None:
        return self.items.get(source_key)


@dataclass(frozen=True, slots=True)
class FeedFetch:
    """The outcome of :func:`fetch_feed`; ``parsed`` is None when ``not_modified``."""

    url: str
    final_url: str
    not_modified: bool
    body: bytes | None
    etag: str | None
    last_modified: str | None
    parsed: ParsedFeed | None
    pages: int = 1


# --------------------------------------------------------------------------- parsing


def parse_feed(data: bytes | str, *, url: str | None = None) -> ParsedFeed:
    """Parse one RSS document; raises :class:`NotAFeed` for anything else."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > MAX_FEED_BYTES:
        raise BodyTooLarge(url or "<document>", MAX_FEED_BYTES)
    if not raw.strip():
        raise NotAFeed("empty document")
    try:
        root = etree.fromstring(raw, PARSER)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise NotAFeed(f"not well-formed XML: {exc}") from exc
    if not isinstance(root.tag, str):
        raise NotAFeed("no root element")
    _strip_entities(root)
    local = etree.QName(root).localname
    if local == "feed":
        raise NotAFeed("Atom feeds are not supported; paste an RSS feed URL")
    if local == "RDF":
        raise NotAFeed("RSS 1.0 (RDF) feeds are not supported")
    if local != "rss":
        raise NotAFeed(f"root element is <{local}>, not <rss>")
    channel = root.find("channel")
    if channel is None:
        raise NotAFeed("<rss> without a <channel>")
    return _parse_channel(root, channel, url)


def _parse_channel(root: Element, channel: Element, url: str | None) -> ParsedFeed:
    link = _text(channel, "link")
    base = link if link and _is_absolute(link) else url
    listing_items: list[SourceListingItem] = []
    elements: dict[str, Element] = {}
    for element in channel.iterfind("item"):
        item = _parse_item(element, base)
        if item is None or item.source_key in elements:
            continue
        elements[item.source_key] = element
        listing_items.append(item.model_copy(update={"position": len(listing_items)}))
    artwork = _attr(channel, tag(NS_ITUNES, "image"), "href") or _text(channel, "image/url")
    listing = SourceListing(
        service=SERVICE_RSS,
        extractor_key=None,
        title=_text(channel, "title"),
        description=_text(channel, "description") or _text(channel, tag(NS_ITUNES, "summary")),
        author=(
            _text(channel, tag(NS_ITUNES, "author"))
            or _text(channel, "managingEditor")
            or _text(channel, f"{tag(NS_ITUNES, 'owner')}/{tag(NS_ITUNES, 'name')}")
        ),
        artwork_url=_resolve(base, artwork),
        webpage_url=_resolve(base, link) if link else url,
        language=_text(channel, "language"),
        listing_order=ListingOrder.newest_first,
        items=listing_items,
        raw=None,
    )
    return ParsedFeed(
        listing=listing,
        root=root,
        channel=channel,
        items=elements,
        next_url=_next_link(channel, url or base),
    )


def _parse_item(element: Element, base: str | None) -> SourceListingItem | None:
    guid = _text(element, "guid")
    enclosure = _pick_enclosure(element)
    enclosure_url = _resolve(base, enclosure.get("url")) if enclosure is not None else None
    if not enclosure_url and enclosure is not None:
        enclosure = None
    source_key = guid or enclosure_url
    if not source_key:
        return None
    title = _text(element, "title") or _text(element, tag(NS_ITUNES, "title"))
    if not title:
        title = guid or basename(urlsplit(enclosure_url or "").path) or source_key
    enclosure_type = _clean_type(enclosure.get("type")) if enclosure is not None else None
    link = _text(element, "link")
    return SourceListingItem(
        source_key=source_key,
        source_url=_resolve(base, link) if link else None,
        title=title,
        description=(
            _text(element, "description")
            or _text(element, tag(NS_ITUNES, "summary"))
            or _text(element, tag(NS_CONTENT, "encoded"))
        ),
        published_at=parse_date(_text(element, "pubDate") or _text(element, tag(NS_DC, "date"))),
        duration_seconds=parse_duration(_text(element, tag(NS_ITUNES, "duration"))),
        artwork_url=_resolve(base, _attr(element, tag(NS_ITUNES, "image"), "href")),
        author=(
            _text(element, tag(NS_ITUNES, "author"))
            or _text(element, "author")
            or _text(element, tag(NS_DC, "creator"))
        ),
        source_number=parse_int(_text(element, tag(NS_ITUNES, "episode"))),
        source_season=parse_int(_text(element, tag(NS_ITUNES, "season"))),
        position=0,
        tab=None,
        enclosure_url=enclosure_url,
        enclosure_type=enclosure_type,
        archivable=enclosure_url is not None,
    )


def _pick_enclosure(element: Element) -> Element | None:
    """The first ``audio/*`` enclosure, else the first enclosure carrying a URL."""
    enclosures = [e for e in element.iterfind("enclosure") if (e.get("url") or "").strip()]
    for enclosure in enclosures:
        if (_clean_type(enclosure.get("type")) or "").startswith("audio/"):
            return enclosure
    return enclosures[0] if enclosures else None


def _next_link(channel: Element, base: str | None) -> str | None:
    """The ``atom:link rel="next"`` target, resolved against the document's own URL."""
    for link in channel.iterfind(tag(NS_ATOM, "link")):
        rel = (link.get("rel") or "").strip().lower()
        href = (link.get("href") or "").strip()
        if rel == "next" and href:
            return _resolve(base, href)
    return None


# --------------------------------------------------------------------------- serialization


def channel_xml(parsed: ParsedFeed) -> str:
    """The ``<rss>`` root and channel without any ``<item>``: what ``source_channel_xml`` stores."""
    root_copy = _deepcopy(parsed.root)
    channel = root_copy.find("channel")
    if channel is not None:
        for item in list(channel.iterfind("item")):
            channel.remove(item)
    etree.cleanup_namespaces(root_copy)
    return etree.tostring(root_copy, encoding="unicode")


def item_xml(element: Element) -> str:
    """One ``<item>`` as text (namespace declarations included): ``source_item_xml``."""
    copy = _deepcopy(element)
    etree.cleanup_namespaces(copy)
    return etree.tostring(copy, encoding="unicode")


def parse_fragment(text: str | bytes) -> Element:
    """Parse a stored ``source_channel_xml`` / ``source_item_xml`` string (hardened parser)."""
    raw = text.encode("utf-8") if isinstance(text, str) else text
    return etree.fromstring(raw, PARSER)


def _deepcopy(element: Element) -> Element:
    return copy.deepcopy(element)


def _is_entity(node: Element) -> bool:
    """True for an entity-reference node (its ``tag`` is the ``etree.Entity`` factory)."""
    return not isinstance(node.tag, str) and getattr(node.tag, "__name__", "") == "Entity"


def _strip_entities(root: Element) -> None:
    """Remove entity-reference nodes (kept unresolved by the hardened parser).

    With ``resolve_entities=False`` an ``&xxe;`` stays in the tree as a node
    whose serialization could not be re-parsed without its DTD. Dropping the
    nodes (tails preserved) keeps stored XML fragments self-contained.
    """
    for node in list(root.iter()):
        if not _is_entity(node):
            continue
        parent = node.getparent()
        if parent is None:
            continue
        tail = node.tail or ""
        previous = node.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
        parent.remove(node)


# --------------------------------------------------------------------------- fetching


def fetch_feed(
    url: str,
    *,
    client: httpx.Client | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    follow_next: bool = False,
    max_pages: int = MAX_PAGES,
) -> FeedFetch:
    """GET and parse an RSS Source, optionally walking ``atom:link rel="next"`` pages.

    A 304 yields ``not_modified=True`` with no body. Later pages extend the
    first page's items (deduplicated, positions continuing); the stored body
    and channel come from the first page only.
    """
    first = fetch(
        url,
        max_bytes=MAX_FEED_BYTES,
        client=client,
        etag=etag,
        last_modified=last_modified,
        accept=FEED_ACCEPT,
    )
    if first.not_modified:
        return FeedFetch(url, first.url, True, None, first.etag, first.last_modified, None)
    parsed = parse_feed(first.body, url=first.url)
    pages = 1
    if follow_next and parsed.next_url:
        parsed, pages = _follow_pages(parsed, first, client, max_pages)
    return FeedFetch(
        url, first.url, False, first.body, first.etag, first.last_modified, parsed, pages
    )


def _follow_pages(
    parsed: ParsedFeed, first: Fetched, client: httpx.Client | None, max_pages: int
) -> tuple[ParsedFeed, int]:
    visited: set[str | None] = {first.url}
    items = list(parsed.listing.items)
    elements = dict(parsed.items)
    next_url = parsed.next_url
    pages = 1
    while next_url and pages < max_pages and next_url not in visited:
        visited.add(next_url)
        try:
            page = fetch(next_url, max_bytes=MAX_FEED_BYTES, client=client, accept=FEED_ACCEPT)
            page_parsed = parse_feed(page.body, url=page.url)
        except (BodyTooLarge, NotAFeed):
            break
        pages += 1
        for item in page_parsed.listing.items:
            if item.source_key in elements:
                continue
            elements[item.source_key] = page_parsed.items[item.source_key]
            items.append(item.model_copy(update={"position": len(items)}))
        next_url = page_parsed.next_url
    merged = ParsedFeed(
        listing=parsed.listing.model_copy(update={"items": items}),
        root=parsed.root,
        channel=parsed.channel,
        items=elements,
        next_url=parsed.next_url,
    )
    return merged, pages


# --------------------------------------------------------------------------- helpers


def _text(element: Element, path: str) -> str | None:
    found = element.find(path)
    if found is None or found.text is None:
        return None
    text = found.text.strip()
    return text or None


def _attr(element: Element, path: str, name: str) -> str | None:
    found = element.find(path)
    if found is None:
        return None
    value = (found.get(name) or "").strip()
    if not value and found.text:
        value = found.text.strip()
    return value or None


def _is_absolute(url: str) -> bool:
    parts = urlsplit(url)
    return bool(parts.scheme and parts.netloc)


def _resolve(base: str | None, url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if _is_absolute(url) or not base:
        return url
    return urljoin(base, url)


def _clean_type(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.split(";", 1)[0].strip().lower()
    return cleaned or None


def parse_date(text: str | None) -> datetime | None:
    """RFC 2822 (``pubDate``) or ISO 8601; naive values are UTC; junk is None."""
    if not text:
        return None
    value = text.strip()
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def parse_duration(text: str | None) -> int | None:
    """``HH:MM:SS``, ``MM:SS``, ``SS`` or ``SS.s`` -> whole seconds; junk is None."""
    if not text:
        return None
    value = text.strip()
    match = _DURATION_RE.match(value)
    if match:
        return int(float(match.group(1)))
    parts = value.split(":")
    if not 2 <= len(parts) <= 3:
        return None
    try:
        numbers = [float(part.strip()) for part in parts]
    except ValueError:
        return None
    if any(n < 0 for n in numbers):
        return None
    total = 0.0
    for number in numbers:
        total = total * 60 + number
    return int(total)


def parse_int(text: str | None) -> int | None:
    if not text:
        return None
    value = text.strip()
    return int(value) if value.isdigit() else None


def item_elements(parsed: ParsedFeed, keys: Iterable[str]) -> Mapping[str, Element]:
    return {key: parsed.items[key] for key in keys if key in parsed.items}


__all__ = [
    "FEED_ACCEPT",
    "MAX_PAGES",
    "NSMAP",
    "NS_ATOM",
    "NS_CONTENT",
    "NS_DC",
    "NS_ITUNES",
    "NS_MEDIA",
    "NS_PODCAST",
    "PARSER",
    "SERVICE_RSS",
    "FeedFetch",
    "ParsedFeed",
    "channel_xml",
    "fetch_feed",
    "item_elements",
    "item_xml",
    "parse_date",
    "parse_duration",
    "parse_feed",
    "parse_fragment",
    "parse_int",
    "tag",
]
