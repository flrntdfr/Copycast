"""Ordinals: assigned once from the oldest item, never renumbered."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from copycast.domain.enums import ListingOrder
from copycast.domain.ordinals import assign_ordinals, order_oldest_first
from tests.support.factories import EPOCH, listing, listing_item


def test_newest_first_listing_numbers_from_the_oldest() -> None:
    served = listing(5, order=ListingOrder.newest_first)
    ordinals = assign_ordinals(0, served.items, served.listing_order)
    assert ordinals == {f"urn:test:item:{n}": n for n in range(1, 6)}
    assert list(ordinals) == [f"urn:test:item:{n}" for n in range(1, 6)]


def test_oldest_first_listing_numbers_in_served_order() -> None:
    served = listing(5, order=ListingOrder.oldest_first)
    ordinals = assign_ordinals(0, served.items, served.listing_order)
    assert ordinals == {f"urn:test:item:{n}": n for n in range(1, 6)}


def test_both_orders_agree_when_dates_are_present() -> None:
    newest = listing(7, order=ListingOrder.newest_first)
    oldest = listing(7, order=ListingOrder.oldest_first)
    assert assign_ordinals(0, newest.items, newest.listing_order) == assign_ordinals(
        0, oldest.items, oldest.listing_order
    )


def test_without_dates_position_decides_and_newest_first_is_reversed() -> None:
    newest = listing(4, order=ListingOrder.newest_first, with_dates=False)
    assert assign_ordinals(0, newest.items, ListingOrder.newest_first) == {
        "urn:test:item:1": 1,
        "urn:test:item:2": 2,
        "urn:test:item:3": 3,
        "urn:test:item:4": 4,
    }
    oldest = listing(4, order=ListingOrder.oldest_first, with_dates=False)
    assert assign_ordinals(0, oldest.items, ListingOrder.oldest_first) == {
        "urn:test:item:1": 1,
        "urn:test:item:2": 2,
        "urn:test:item:3": 3,
        "urn:test:item:4": 4,
    }


def test_a_single_undated_item_makes_the_whole_batch_positional() -> None:
    items = [
        listing_item(3, published_at=EPOCH + timedelta(days=3), position=0),
        listing_item(2, published_at=None, position=1),
        listing_item(1, published_at=EPOCH + timedelta(days=1), position=2),
    ]
    ordered = order_oldest_first(items, ListingOrder.newest_first)
    assert [i.source_key for i in ordered] == [
        "urn:test:item:1",
        "urn:test:item:2",
        "urn:test:item:3",
    ]


def test_new_items_continue_after_existing_max_and_existing_are_untouched() -> None:
    later = [
        listing_item(9, published_at=EPOCH + timedelta(days=9), position=0),
        listing_item(8, published_at=EPOCH + timedelta(days=8), position=1),
    ]
    assert assign_ordinals(7, later, ListingOrder.newest_first) == {
        "urn:test:item:8": 8,
        "urn:test:item:9": 9,
    }


def test_backdated_new_item_is_numbered_after_existing_items() -> None:
    """A newly listed but old-dated item never shifts existing ordinals."""
    old_dated = [listing_item(0, published_at=EPOCH - timedelta(days=365), position=0)]
    assert assign_ordinals(42, old_dated, ListingOrder.newest_first) == {"urn:test:item:0": 43}


def test_ties_on_published_at_fall_back_to_position() -> None:
    same = datetime(2024, 5, 5, tzinfo=UTC)
    items = [
        listing_item(3, published_at=same, position=0),
        listing_item(2, published_at=same, position=1),
        listing_item(1, published_at=same, position=2),
    ]
    assert assign_ordinals(0, items, ListingOrder.newest_first) == {
        "urn:test:item:1": 1,
        "urn:test:item:2": 2,
        "urn:test:item:3": 3,
    }
    assert assign_ordinals(0, items, ListingOrder.oldest_first) == {
        "urn:test:item:3": 1,
        "urn:test:item:2": 2,
        "urn:test:item:1": 3,
    }


def test_duplicate_keys_keep_the_first_occurrence() -> None:
    items = [
        listing_item(1, published_at=EPOCH, position=0, key="dup"),
        listing_item(2, published_at=EPOCH + timedelta(days=1), position=1, key="dup"),
        listing_item(3, published_at=EPOCH + timedelta(days=2), position=2),
    ]
    assert assign_ordinals(0, items, ListingOrder.oldest_first) == {"dup": 1, "urn:test:item:3": 2}


def test_empty_input() -> None:
    assert assign_ordinals(10, [], ListingOrder.newest_first) == {}
