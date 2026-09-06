"""Ordinal assignment: numbered once from the oldest item, never renumbered."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from copycast.domain.enums import ListingOrder
from copycast.domain.listing import SourceListingItem


def order_oldest_first(
    items: Sequence[SourceListingItem], listing_order: ListingOrder
) -> list[SourceListingItem]:
    """Sort by ``published_at`` when every item has one, else by listing position."""
    if items and all(item.published_at is not None for item in items):
        positional = _by_position(items, listing_order)
        rank = {item.source_key: idx for idx, item in enumerate(positional)}
        return sorted(
            positional,
            key=lambda item: (item.published_at, rank[item.source_key]),  # type: ignore[return-value]
        )
    return _by_position(items, listing_order)


def _by_position(
    items: Sequence[SourceListingItem], listing_order: ListingOrder
) -> list[SourceListingItem]:
    ordered = sorted(items, key=lambda item: item.position)
    if listing_order is ListingOrder.newest_first:
        ordered.reverse()
    return ordered


def assign_ordinals(
    existing_max: int,
    new_items: Iterable[SourceListingItem],
    listing_order: ListingOrder,
) -> dict[str, int]:
    """Number unseen items oldest to newest starting at ``existing_max + 1``.

    Returns ``{source_key: ordinal}`` in assignment order. Duplicate keys keep
    their first occurrence. Existing items are never renumbered: the caller
    passes only keys absent from the Catalog.
    """
    unique: dict[str, SourceListingItem] = {}
    for item in new_items:
        unique.setdefault(item.source_key, item)
    ordered = order_oldest_first(list(unique.values()), listing_order)
    return {item.source_key: existing_max + offset for offset, item in enumerate(ordered, start=1)}
