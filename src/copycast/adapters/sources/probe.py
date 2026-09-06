"""Turn user input into Source candidates.

Scheme-less input is tried as ``https://`` then ``http://``. An RSS body is
one candidate; an HTML page yields one candidate per advertised feed that
verifies as RSS (at most five); anything else is listed by the engine and
becomes a ``ytdlp`` candidate. A bare YouTube channel URL normalizes to its
``/videos`` tab and skips the HTTP round trip (YouTube pages advertise an
RSS feed without audio). Every candidate is cached for ten minutes under
its token and its normalized URL, together with the listing (and the parsed
RSS page) so ``create_mirror`` can populate the Catalog without refetching.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

import httpx

from copycast.adapters.sources.cache import TtlCache
from copycast.adapters.sources.discovery import discover_feed_links, looks_like_html
from copycast.adapters.sources.http import (
    MAX_FEED_BYTES,
    NotAFeed,
    SourceError,
    fetch,
)
from copycast.adapters.sources.rss import FEED_ACCEPT, FeedFetch, parse_feed
from copycast.application.models import ProbeCandidate, ProbeResult
from copycast.application.ports import (
    CancelToken,
    Engine,
    EngineError,
    EngineLog,
    NullEngineLog,
)
from copycast.domain.enums import SourceKind
from copycast.domain.exceptions import Unsupported
from copycast.domain.listing import SourceListing
from copycast.domain.urls import (
    normalize_source_url,
    normalize_youtube_channel,
    scheme_candidates,
    youtube_host,
)

PROBE_TTL_SECONDS: Final = 600.0
MAX_VERIFIED_LINKS: Final = 5
TOKEN_BYTES: Final = 12
MEDIA_TYPE_PREFIXES: Final = ("audio/", "video/")


@dataclass(frozen=True, slots=True)
class ProbeEntry:
    """What a candidate token resolves to: the candidate plus the listing behind it."""

    candidate: ProbeCandidate
    listing: SourceListing
    feed: FeedFetch | None = None
    created_at: float = 0.0

    @property
    def source_url(self) -> str:
        return self.candidate.source_url

    @property
    def source_kind(self) -> SourceKind:
        return self.candidate.source_kind


class ProbeCache:
    """Entries keyed by candidate token and by normalized Source URL (10 min TTL)."""

    def __init__(
        self, ttl: float = PROBE_TTL_SECONDS, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._by_token: TtlCache[str, ProbeEntry] = TtlCache(ttl, clock=clock)
        self._by_url: TtlCache[str, ProbeEntry] = TtlCache(ttl, clock=clock)

    def put(self, entry: ProbeEntry) -> None:
        self._by_token.put(entry.candidate.candidate_token, entry)
        self._by_url.put(normalize_source_url(entry.source_url), entry)

    def get_by_token(self, token: str | None) -> ProbeEntry | None:
        return self._by_token.get(token) if token else None

    def get_by_url(self, url: str | None) -> ProbeEntry | None:
        return self._by_url.get(normalize_source_url(url)) if url else None

    def get(self, *, token: str | None = None, url: str | None = None) -> ProbeEntry | None:
        return self.get_by_token(token) or self.get_by_url(url)

    def purge(self) -> None:
        self._by_token.purge()
        self._by_url.purge()

    def clear(self) -> None:
        self._by_token.clear()
        self._by_url.clear()

    def __len__(self) -> int:
        return len(self._by_token)


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def rss_candidate(source_url: str, listing: SourceListing) -> ProbeCandidate:
    return ProbeCandidate(
        candidate_token=new_token(),
        source_url=source_url,
        source_kind=SourceKind.rss,
        service=listing.service,
        title=listing.title,
        description=listing.description,
        artwork_url=listing.artwork_url,
        author=listing.author,
        item_count=len(listing.items),
    )


def ytdlp_candidate(source_url: str, listing: SourceListing) -> ProbeCandidate:
    return ProbeCandidate(
        candidate_token=new_token(),
        source_url=source_url,
        source_kind=SourceKind.ytdlp,
        service=listing.service,
        title=listing.title,
        description=listing.description,
        artwork_url=listing.artwork_url,
        author=listing.author,
        item_count=len(listing.items),
    )


def probe(
    url: str,
    *,
    engine: Engine,
    options: Mapping[str, Any] | None = None,
    cache: ProbeCache | None = None,
    client: httpx.Client | None = None,
    cancel: CancelToken | None = None,
    log: EngineLog | None = None,
    verify_limit: int = MAX_VERIFIED_LINKS,
) -> ProbeResult:
    """Resolve ``url`` to candidates; raises :class:`Unsupported` when nothing fits."""
    input_url = url.strip()
    if not input_url:
        raise Unsupported("paste a feed, page or Source URL")
    token = cancel if cancel is not None else CancelToken()
    engine_log = log if log is not None else NullEngineLog()
    engine_options = dict(options or {})
    entries: list[ProbeEntry] = []
    failures: list[str] = []
    urls = scheme_candidates(input_url)

    if any(youtube_host(u) for u in urls):
        target = normalize_youtube_channel(urls[0])
        entries = _ytdlp_entries([target], engine, engine_options, token, engine_log, failures)
    else:
        fetched_any = False
        for candidate_url in urls:
            try:
                fetched = fetch(
                    candidate_url, max_bytes=MAX_FEED_BYTES, client=client, accept=FEED_ACCEPT
                )
            except SourceError as exc:
                failures.append(str(exc))
                continue
            fetched_any = True
            content_type = fetched.content_type or ""
            if content_type.startswith(MEDIA_TYPE_PREFIXES):
                raise Unsupported(
                    f"{input_url} is a media file ({content_type}), not a Source; "
                    "send it to an Inbox instead"
                )
            try:
                parsed = parse_feed(fetched.body, url=fetched.url)
            except NotAFeed as exc:
                failures.append(str(exc))
            else:
                feed = FeedFetch(
                    candidate_url,
                    fetched.url,
                    False,
                    fetched.body,
                    fetched.etag,
                    fetched.last_modified,
                    parsed,
                )
                entries = [
                    ProbeEntry(rss_candidate(candidate_url, parsed.listing), parsed.listing, feed)
                ]
                break
            if looks_like_html(fetched.body, fetched.content_type):
                links = discover_feed_links(fetched.body, fetched.url)
                entries = _verify_links(links[:verify_limit], client, failures)
                if entries:
                    break
            break
        if not entries:
            targets = urls if not fetched_any else urls[:1]
            entries = _ytdlp_entries(targets, engine, engine_options, token, engine_log, failures)

    if not entries:
        detail = "; ".join(dict.fromkeys(failures)) or "no candidates"
        raise Unsupported(
            f"{input_url} is neither a podcast feed, a page advertising one, "
            f"nor a Source the engine supports ({detail})"
        )
    now = time.monotonic()
    result_entries = [ProbeEntry(e.candidate, e.listing, e.feed, created_at=now) for e in entries]
    if cache is not None:
        for entry in result_entries:
            cache.put(entry)
    return ProbeResult(input_url=input_url, candidates=[e.candidate for e in result_entries])


def _verify_links(
    links: list[str], client: httpx.Client | None, failures: list[str]
) -> list[ProbeEntry]:
    entries: list[ProbeEntry] = []
    seen: set[str] = set()
    for link in links:
        key = normalize_source_url(link)
        if key in seen:
            continue
        seen.add(key)
        try:
            fetched = fetch(link, max_bytes=MAX_FEED_BYTES, client=client, accept=FEED_ACCEPT)
            parsed = parse_feed(fetched.body, url=fetched.url)
        except (SourceError, NotAFeed) as exc:
            failures.append(f"{link}: {exc}")
            continue
        feed = FeedFetch(
            link, fetched.url, False, fetched.body, fetched.etag, fetched.last_modified, parsed
        )
        entries.append(ProbeEntry(rss_candidate(link, parsed.listing), parsed.listing, feed))
    return entries


def _ytdlp_entries(
    urls: list[str],
    engine: Engine,
    options: dict[str, Any],
    cancel: CancelToken,
    log: EngineLog,
    failures: list[str],
) -> list[ProbeEntry]:
    for candidate_url in urls:
        try:
            listing = engine.list_source(candidate_url, options, cancel, log)
        except EngineError as exc:
            failures.append(f"{candidate_url}: {exc}")
            continue
        if not listing.items and not listing.title:
            failures.append(f"{candidate_url}: the engine found nothing to list")
            continue
        return [ProbeEntry(ytdlp_candidate(candidate_url, listing), listing)]
    return []


__all__ = [
    "MAX_VERIFIED_LINKS",
    "PROBE_TTL_SECONDS",
    "ProbeCache",
    "ProbeEntry",
    "new_token",
    "probe",
    "rss_candidate",
    "ytdlp_candidate",
]
