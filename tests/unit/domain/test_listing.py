from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from copycast.domain.enums import ListingOrder
from copycast.domain.listing import SourceListing, SourceListingItem


def test_item_defaults_and_frozen() -> None:
    item = SourceListingItem(source_key="k", title="t")
    assert item.archivable is True
    assert item.position == 0
    assert item.published_at is None
    with pytest.raises(ValidationError):
        item.title = "other"  # type: ignore[misc]


def test_naive_dates_become_utc_and_aware_dates_are_converted() -> None:
    naive = SourceListingItem(source_key="k", title="t", published_at=datetime(2024, 1, 1, 12))
    assert naive.published_at == datetime(2024, 1, 1, 12, tzinfo=UTC)
    plus_one = timezone(timedelta(hours=1))
    aware = SourceListingItem(
        source_key="k", title="t", published_at=datetime(2024, 1, 1, 12, tzinfo=plus_one)
    )
    assert aware.published_at == datetime(2024, 1, 1, 11, tzinfo=UTC)
    assert aware.published_at is not None and aware.published_at.tzinfo is UTC


def test_source_key_must_not_be_empty_and_extra_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SourceListingItem(source_key="", title="t")
    with pytest.raises(ValidationError):
        SourceListingItem(source_key="k", title="t", bogus=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        SourceListingItem(source_key="k", title="t", duration_seconds=-1)


def test_listing_defaults() -> None:
    listing = SourceListing(service="RSS")
    assert listing.listing_order is ListingOrder.newest_first
    assert listing.items == []
    assert listing.raw is None
    assert listing.source_keys == []


def test_listing_round_trips_through_json() -> None:
    listing = SourceListing(
        service="YouTube",
        extractor_key="YoutubeTab",
        title="T",
        listing_order=ListingOrder.oldest_first,
        items=[SourceListingItem(source_key="Youtube:abc", title="A", source_number=1)],
        raw={"id": "PL1"},
    )
    again = SourceListing.model_validate_json(listing.model_dump_json())
    assert again == listing
    assert again.source_keys == ["Youtube:abc"]
