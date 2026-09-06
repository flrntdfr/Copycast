"""Builders for domain and request objects.

Only pure values live here today (listings, request models, selection
candidates). Database row factories belong next to the repositories once the
storage milestone lands; add them here so every test shares one vocabulary.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from copycast.application.models import (
    BackfillRequest,
    InboxCreate,
    MirrorCreate,
    PruneRequest,
    RequestCreate,
    SelectionRequest,
)
from copycast.domain.enums import ArchiveState, BackfillMode, ListingOrder, Numbering
from copycast.domain.listing import SourceListing, SourceListingItem
from copycast.domain.selection import SelectionCandidate

EPOCH = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
FIXTURE_FEED_URL = "https://podcast.example/feed.xml"


def listing_item(
    n: int,
    *,
    published_at: datetime | None = None,
    position: int | None = None,
    key: str | None = None,
    **overrides: Any,
) -> SourceListingItem:
    """Item ``n`` (1-based) of a synthetic Source; newer items have larger ``n``."""
    fields: dict[str, Any] = {
        "source_key": key or f"urn:test:item:{n}",
        "source_url": f"https://podcast.example/episodes/{n}",
        "title": f"Episode {n}",
        "description": f"Description of episode {n}",
        "published_at": published_at,
        "duration_seconds": 60 * n,
        "enclosure_url": f"https://podcast.example/media/{n}.mp3",
        "enclosure_type": "audio/mpeg",
        "position": n - 1 if position is None else position,
    }
    fields.update(overrides)
    return SourceListingItem(**fields)


def listing(
    count: int = 3,
    *,
    order: ListingOrder = ListingOrder.newest_first,
    with_dates: bool = True,
    service: str = "RSS",
    title: str = "Test Podcast",
    **overrides: Any,
) -> SourceListing:
    """A listing of ``count`` items served in ``order`` (positions follow the served order)."""
    numbers = list(range(1, count + 1))
    served = list(reversed(numbers)) if order is ListingOrder.newest_first else numbers
    items = [
        listing_item(
            n,
            published_at=EPOCH + timedelta(days=n) if with_dates else None,
            position=pos,
            source_number=n,
        )
        for pos, n in enumerate(served)
    ]
    fields: dict[str, Any] = {
        "service": service,
        "extractor_key": None,
        "title": title,
        "description": "A test podcast",
        "author": "Tester",
        "artwork_url": "https://podcast.example/artwork.jpg",
        "webpage_url": "https://podcast.example/",
        "language": "en",
        "listing_order": order,
        "items": items,
    }
    fields.update(overrides)
    return SourceListing(**fields)


def selection_candidates(
    count: int,
    *,
    archived: Iterable[int] = (),
    source_numbers: dict[int, int | None] | None = None,
) -> list[SelectionCandidate]:
    """``count`` candidates with ordinal == n and source_number == n unless overridden."""
    archived_set = set(archived)
    numbers = source_numbers or {}
    return [
        SelectionCandidate(
            item_id=f"item{n:04d}",
            ordinal=n,
            source_number=numbers.get(n, n),
            archive_state=ArchiveState.archived if n in archived_set else ArchiveState.available,
        )
        for n in range(1, count + 1)
    ]


def mirror_create(
    source_url: str = FIXTURE_FEED_URL,
    *,
    mode: BackfillMode = BackfillMode.all,
    latest_n: int | None = None,
    selection: str | None = None,
    follow: bool | None = None,
    engine_options: dict[str, Any] | None = None,
    candidate_token: str | None = None,
) -> MirrorCreate:
    return MirrorCreate(
        source_url=source_url,
        candidate_token=candidate_token,
        backfill=BackfillRequest(mode=mode, latest_n=latest_n, selection=selection),
        follow=follow,
        engine_options=engine_options or {},
    )


def selection_request(
    selection: str | None = None,
    *,
    item_ids: list[str] | None = None,
    numbering: Numbering = Numbering.source,
    dry_run: bool = False,
) -> SelectionRequest:
    return SelectionRequest(
        selection=selection, item_ids=item_ids or [], numbering=numbering, dry_run=dry_run
    )


def inbox_create(name: str = "Later", autoprune_days: int | None = None) -> InboxCreate:
    return InboxCreate(name=name, autoprune_days=autoprune_days)


def request_create(url: str = "https://www.youtube.com/watch?v=dQw4w9WgXcQ") -> RequestCreate:
    return RequestCreate(url=url)


def prune_request(
    *, downloaded: bool = True, older_than_days: int | None = None, dry_run: bool = False
) -> PruneRequest:
    return PruneRequest(downloaded=downloaded, older_than_days=older_than_days, dry_run=dry_run)
