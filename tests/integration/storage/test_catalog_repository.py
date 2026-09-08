"""CatalogRepository: ordinals via upsert_listing, states, tombstones, downloads, prune."""

from __future__ import annotations

from datetime import timedelta

import pytest

from copycast.adapters.db.uow import UnitOfWorkFactory
from copycast.domain.enums import ArchiveState, ListingOrder, Numbering, WantedReason
from copycast.domain.exceptions import NotFound
from copycast.domain.ids import item_id
from tests.integration.storage.conftest import NOW, inbox_row, item_row, mirror_row, seed
from tests.support.factories import listing, listing_item

pytestmark = pytest.mark.integration


async def _mirror(uow_factory: UnitOfWorkFactory) -> str:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        return feed.id


# --------------------------------------------------------------------------- upsert_listing


async def test_upsert_assigns_ordinals_from_the_oldest(uow_factory: UnitOfWorkFactory) -> None:
    feed_id = await _mirror(uow_factory)
    async with uow_factory() as uow:
        result = await uow.catalog.upsert_listing(feed_id, listing(3))
        assert (result.listed_count, result.new_count, result.delisted_count) == (3, 3, 0)
        assert len(result.new_item_ids) == 3
        assert result.seen_item_ids == result.new_item_ids
        assert result.wanted_item_ids == []
    async with uow_factory() as uow:
        rows = await uow.catalog.for_feed(feed_id)
        assert [(r.ordinal, r.title) for r in rows] == [
            (1, "Episode 1"),
            (2, "Episode 2"),
            (3, "Episode 3"),
        ]
        assert rows[0].id == item_id(feed_id, "urn:test:item:1")
        assert all(r.archive_state == ArchiveState.available and r.listed for r in rows)
        assert all(r.source_number == r.ordinal for r in rows)


async def test_upsert_never_renumbers_and_appends_new_items(
    uow_factory: UnitOfWorkFactory,
) -> None:
    feed_id = await _mirror(uow_factory)
    async with uow_factory() as uow:
        await uow.catalog.upsert_listing(feed_id, listing(3))
    async with uow_factory() as uow:
        result = await uow.catalog.upsert_listing(feed_id, listing(5))
        assert (result.new_count, result.delisted_count) == (2, 0)
        rows = await uow.catalog.for_feed(feed_id)
        assert [(r.ordinal, r.source_key) for r in rows] == [
            (n, f"urn:test:item:{n}") for n in range(1, 6)
        ]
        assert await uow.catalog.max_ordinal(feed_id) == 5


async def test_upsert_oldest_first_without_dates_follows_position(
    uow_factory: UnitOfWorkFactory,
) -> None:
    feed_id = await _mirror(uow_factory)
    playlist = listing(3, order=ListingOrder.oldest_first, with_dates=False)
    async with uow_factory() as uow:
        await uow.catalog.upsert_listing(feed_id, playlist)
        rows = await uow.catalog.for_feed(feed_id)
        assert [(r.ordinal, r.source_position) for r in rows] == [(1, 0), (2, 1), (3, 2)]


async def test_upsert_delists_absent_rows_only_for_non_empty_listings(
    uow_factory: UnitOfWorkFactory,
) -> None:
    feed_id = await _mirror(uow_factory)
    async with uow_factory() as uow:
        await uow.catalog.upsert_listing(feed_id, listing(3))
    shorter = listing(3).model_copy(update={"items": listing(3).items[:2]})  # drops item 1
    async with uow_factory() as uow:
        result = await uow.catalog.upsert_listing(feed_id, shorter)
        assert result.delisted_count == 1
        gone = await uow.catalog.by_source_key(feed_id, "urn:test:item:1")
        assert gone is not None and gone.listed is False
    empty = listing(0)
    async with uow_factory() as uow:
        result = await uow.catalog.upsert_listing(feed_id, empty)
        assert (result.listed_count, result.delisted_count) == (0, 0)
        rows = await uow.catalog.for_feed(feed_id)
        assert [r.listed for r in rows] == [False, True, True]
    async with uow_factory() as uow:
        result = await uow.catalog.upsert_listing(feed_id, listing(3))
        assert result.new_count == 0
        back = await uow.catalog.by_source_key(feed_id, "urn:test:item:1")
        assert back is not None and back.listed is True


async def test_upsert_refreshes_metadata_non_blank(uow_factory: UnitOfWorkFactory) -> None:
    feed_id = await _mirror(uow_factory)
    async with uow_factory() as uow:
        await uow.catalog.upsert_listing(feed_id, listing(1))
    changed = listing(1).model_copy(
        update={
            "items": [
                listing_item(
                    1,
                    published_at=NOW,
                    title="   ",
                    description=None,
                    source_number=7,
                    tab="videos",
                    archivable=False,
                )
            ]
        }
    )
    async with uow_factory() as uow:
        loaded = await uow.catalog.by_source_key(feed_id, "urn:test:item:1")
        assert loaded is not None
        await uow.catalog.upsert_listing(feed_id, changed)
        # The loaded instance is refreshed eagerly (no lazy load under asyncio).
        assert loaded.title == "Episode 1"
        assert loaded.description == "Description of episode 1"
        assert loaded.published_at == NOW
        assert loaded.published_at_approximate is False
        assert (loaded.source_number, loaded.tab, loaded.archivable) == (7, "videos", False)


async def test_approximate_dates_fill_blanks_only_and_are_flagged(
    uow_factory: UnitOfWorkFactory,
) -> None:
    feed_id = await _mirror(uow_factory)
    undated = listing(1).model_copy(update={"items": [listing_item(1, published_at=None)]})
    rough = listing(1).model_copy(
        update={"items": [listing_item(1, published_at=NOW, published_at_exact=False)]}
    )
    drifted = listing(1).model_copy(
        update={
            "items": [
                listing_item(1, published_at=NOW + timedelta(days=2), published_at_exact=False)
            ]
        }
    )
    exact = listing(1).model_copy(
        update={"items": [listing_item(1, published_at=NOW - timedelta(days=1))]}
    )
    async with uow_factory() as uow:
        await uow.catalog.upsert_listing(feed_id, undated)
        row = await uow.catalog.by_source_key(feed_id, "urn:test:item:1")
        assert row is not None and row.published_at is None and not row.published_at_approximate
        await uow.catalog.upsert_listing(feed_id, rough)
        assert row.published_at == NOW and row.published_at_approximate is True
        await uow.catalog.upsert_listing(feed_id, drifted)
        assert row.published_at == NOW and row.published_at_approximate is True
        await uow.catalog.upsert_listing(feed_id, exact)
        assert row.published_at == NOW - timedelta(days=1)
        assert row.published_at_approximate is False
        # A new row from an approximate listing is flagged from the start.
        two = listing(2).model_copy(
            update={"items": [listing_item(2, published_at=NOW, published_at_exact=False)]}
        )
        await uow.catalog.upsert_listing(feed_id, two)
        second = await uow.catalog.by_source_key(feed_id, "urn:test:item:2")
        assert second is not None and second.published_at_approximate is True
        # The archive's exact date clears it; a manual fetch may overwrite the text.
        await uow.catalog.fill_metadata(second.id, published_at=NOW, description="Full")
        assert second.published_at_approximate is False
        assert second.description == "Description of episode 2"  # never overwritten by default
        await uow.catalog.fill_metadata(second.id, description="Full", overwrite=True)
        assert second.description == "Full"


async def test_upsert_with_wanted_reason_marks_new_and_deleted_rows(
    uow_factory: UnitOfWorkFactory,
) -> None:
    async with uow_factory() as uow:
        inbox = await uow.feeds.add(inbox_row())
        feed_id = inbox.id
        first = await uow.catalog.upsert_listing(
            feed_id, listing(2), wanted_reason=WantedReason.request
        )
        assert sorted(first.wanted_item_ids) == sorted(first.new_item_ids)
        rows = await uow.catalog.for_feed(feed_id)
        assert {r.archive_state for r in rows} == {ArchiveState.wanted}
        assert {r.wanted_reason for r in rows} == {WantedReason.request}
        await uow.catalog.tombstone(rows[0].id, listed=False)
        await uow.catalog.mark_state(
            rows[1].id,
            ArchiveState.archived,
            media_path="media/x.m4a",
            media_mime="audio/mp4",
            media_bytes=1,
        )
    async with uow_factory() as uow:
        again = await uow.catalog.upsert_listing(
            feed_id, listing(2), wanted_reason=WantedReason.request
        )
        assert again.new_count == 0
        assert again.wanted_item_ids == [rows[0].id]  # the tombstone flips back, archived stays
        flipped = await uow.catalog.require(rows[0].id)
        assert flipped.archive_state == ArchiveState.wanted and flipped.deleted_at is None
        assert (await uow.catalog.require(rows[1].id)).archive_state == ArchiveState.archived


async def test_upsert_unknown_feed(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        with pytest.raises(NotFound):
            await uow.catalog.upsert_listing("nope", listing(1))


async def test_upsert_large_listing_is_fast_enough(uow_factory: UnitOfWorkFactory) -> None:
    feed_id = await _mirror(uow_factory)
    big = listing(2000)
    async with uow_factory() as uow:
        result = await uow.catalog.upsert_listing(feed_id, big)
        assert result.new_count == 2000
    async with uow_factory() as uow:
        result = await uow.catalog.upsert_listing(feed_id, big)
        assert (result.new_count, result.delisted_count) == (0, 0)
        assert await uow.catalog.max_ordinal(feed_id) == 2000


# --------------------------------------------------------------------------- state transitions


async def test_set_wanted_only_touches_wantable_archivable_rows(
    uow_factory: UnitOfWorkFactory,
) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        available = item_row(feed, 1)
        deleted = item_row(feed, 2, state=ArchiveState.deleted, deleted_at=NOW)
        failed = item_row(feed, 3, state=ArchiveState.failed, last_error="x", attempt_count=2)
        archived = item_row(feed, 4, state=ArchiveState.archived)
        unarchivable = item_row(feed, 5, archivable=False)
        await seed(uow, available, deleted, failed, archived, unarchivable)
        changed = await uow.catalog.set_wanted(
            [r.id for r in (available, deleted, failed, archived, unarchivable)],
            WantedReason.manual,
        )
        assert set(changed) == {available.id, deleted.id, failed.id}
        assert available.archive_state == ArchiveState.wanted
        assert deleted.deleted_at is None and deleted.wanted_reason == WantedReason.manual
        assert failed.last_error is None
        assert archived.archive_state == ArchiveState.archived
        assert unarchivable.archive_state == ArchiveState.available
        assert await uow.catalog.set_wanted([], WantedReason.manual) == []


async def test_mark_state_transitions(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        item = item_row(feed, 1, state=ArchiveState.wanted, wanted_reason="backfill")
        await seed(uow, item)
        assert await uow.catalog.mark_state(
            item.id, ArchiveState.archiving, only_from=ArchiveState.wanted
        )
        assert item.archive_state == ArchiveState.archiving
        assert not await uow.catalog.mark_state(
            item.id, ArchiveState.archiving, only_from=ArchiveState.wanted
        )
        with pytest.raises(ValueError, match="media_path"):
            await uow.catalog.mark_state(item.id, ArchiveState.archived)
        assert await uow.catalog.mark_state(
            item.id,
            ArchiveState.failed,
            error="network",
            count_attempt=True,
            only_from=[ArchiveState.archiving, ArchiveState.wanted],
        )
        assert (item.attempt_count, item.last_error) == (1, "network")
        assert await uow.catalog.mark_state(
            item.id,
            ArchiveState.archived,
            media_path=f"media/{item.id}.m4a",
            media_mime="audio/mp4",
            media_bytes=4407,
            duration_seconds=61,
            archived_at=NOW,
        )
        assert item.archive_state == ArchiveState.archived
        assert (item.media_bytes, item.duration_seconds, item.last_error) == (4407, 61, None)
        assert item.archived_at == NOW
        assert await uow.catalog.mark_state(item.id, ArchiveState.available)
        assert item.wanted_reason is None
        await uow.catalog.set_source_item_xml(item.id, "<item/>")
        assert item.source_item_xml == "<item/>"


async def test_tombstone_keeps_row_and_clears_media(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        inbox = await uow.feeds.add(inbox_row())
        episode = item_row(feed, 1, state=ArchiveState.archived, download_count=3)
        inbox_episode = item_row(inbox, 1, state=ArchiveState.archived)
        await seed(uow, episode, inbox_episode)
        assert await uow.catalog.tombstone(episode.id, listed=True, at=NOW)
        assert await uow.catalog.tombstone(inbox_episode.id, listed=False)
        assert not await uow.catalog.tombstone("0000000000000000", listed=True)
        assert episode.archive_state == ArchiveState.deleted
        assert episode.deleted_at == NOW
        assert (episode.media_path, episode.media_mime, episode.media_bytes) == (None, None, None)
        assert episode.archived_at is None
        assert episode.listed is True
        assert episode.download_count == 3  # telemetry survives
        assert inbox_episode.listed is False
        counts = await uow.catalog.count_by_state(feed.id)
        assert (counts.available, counts.archived) == (1, 0)
        inbox_counts = await uow.catalog.count_by_state(inbox.id)
        assert (inbox_counts.available, inbox_counts.archived) == (0, 0)


async def test_count_by_state_derives_catalog_states(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        await seed(
            uow,
            item_row(feed, 1, state=ArchiveState.archived),
            item_row(feed, 2, state=ArchiveState.archived, listed=False),
            item_row(feed, 3),
            item_row(feed, 4, state=ArchiveState.deleted),
            item_row(feed, 5, state=ArchiveState.wanted),
            item_row(feed, 6, state=ArchiveState.failed),
            item_row(feed, 7, state=ArchiveState.archiving),
        )
        counts = await uow.catalog.count_by_state(feed.id)
        assert counts.model_dump() == {
            "listed": 1,
            "available": 2,
            "delisted": 1,
            "wanted": 1,
            "archived": 2,
            "failed": 1,
        }
        many = await uow.catalog.count_by_state_many([feed.id, "unknown"])
        assert set(many) == {feed.id}
        assert await uow.catalog.count_by_state_many([]) == {}
        assert (await uow.catalog.count_by_state("unknown")).archived == 0


# --------------------------------------------------------------------------- downloads


async def test_record_download_counts_only_archived_items(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        episode = item_row(feed, 1, state=ArchiveState.archived)
        pending = item_row(feed, 2, state=ArchiveState.wanted)
        await seed(uow, episode, pending)
        first = NOW - timedelta(hours=2)
        assert await uow.catalog.record_download(episode.id, at=first) is True
        assert (episode.download_count, episode.first_downloaded_at) == (1, first)
        assert await uow.catalog.record_download(episode.id, at=NOW) is True
        assert episode.download_count == 2
        assert episode.first_downloaded_at == first
        assert episode.last_downloaded_at == NOW
        assert await uow.catalog.record_download(pending.id) is False
        assert pending.download_count == 0
        assert await uow.catalog.record_download("0000000000000000") is False


async def test_prunable_criteria(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        inbox = await uow.feeds.add(inbox_row())
        old_downloaded = item_row(
            inbox,
            1,
            state=ArchiveState.archived,
            first_seen_at=NOW - timedelta(days=30),
            first_downloaded_at=NOW - timedelta(days=20),
            download_count=1,
        )
        old_never = item_row(
            inbox, 2, state=ArchiveState.archived, first_seen_at=NOW - timedelta(days=30)
        )
        new_downloaded = item_row(
            inbox,
            3,
            state=ArchiveState.archived,
            first_seen_at=NOW - timedelta(days=1),
            first_downloaded_at=NOW - timedelta(hours=1),
            download_count=2,
        )
        not_archived = item_row(inbox, 4, first_seen_at=NOW - timedelta(days=30))
        await seed(uow, old_downloaded, old_never, new_downloaded, not_archived)

        with pytest.raises(ValueError, match="criterion"):
            await uow.catalog.prunable(inbox.id)
        downloaded = await uow.catalog.prunable(inbox.id, downloaded=True)
        assert [r.id for r in downloaded] == [old_downloaded.id, new_downloaded.id]
        older = await uow.catalog.prunable(inbox.id, added_before=NOW - timedelta(days=7))
        assert [r.id for r in older] == [old_downloaded.id, old_never.id]
        both = await uow.catalog.prunable(
            inbox.id, downloaded=True, added_before=NOW - timedelta(days=7)
        )
        assert [r.id for r in both] == [old_downloaded.id]
        auto = await uow.catalog.prunable(inbox.id, downloaded_before=NOW - timedelta(days=7))
        assert [r.id for r in auto] == [old_downloaded.id]  # never-downloaded rows are exempt


# --------------------------------------------------------------------------- reads and selection


async def test_list_page_filters_sorts_and_counts(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        await seed(
            uow,
            item_row(feed, 1, state=ArchiveState.archived, title="Alpha talk"),
            item_row(feed, 2, state=ArchiveState.archived, listed=False, title="Beta"),
            item_row(feed, 3, title="Gamma", published_at=None),
            item_row(feed, 4, state=ArchiveState.wanted, title="Delta alpha"),
        )
        rows, total = await uow.catalog.list_page(feed.id)
        assert total == 4
        assert [r.ordinal for r in rows] == [4, 2, 1, 3]  # published desc, NULLS last
        rows, total = await uow.catalog.list_page(feed.id, state=ArchiveState.archived)
        assert (total, [r.ordinal for r in rows]) == (2, [2, 1])
        rows, total = await uow.catalog.list_page(
            feed.id, state=[ArchiveState.archived, ArchiveState.wanted], listed=True
        )
        assert (total, [r.ordinal for r in rows]) == (2, [4, 1])
        rows, total = await uow.catalog.list_page(feed.id, q="alpha", sort="title", order="asc")
        assert [r.title for r in rows] == ["Alpha talk", "Delta alpha"]
        rows, _ = await uow.catalog.list_page(feed.id, sort="ordinal", order="asc", limit=2)
        assert [r.ordinal for r in rows] == [1, 2]
        rows, _ = await uow.catalog.list_page(feed.id, sort="ordinal", limit=2, offset=2)
        assert [r.ordinal for r in rows] == [2, 1]
        rows, _ = await uow.catalog.list_page(feed.id, sort="added", order="asc")
        assert [r.ordinal for r in rows] == [1, 2, 3, 4]
        rows, _ = await uow.catalog.list_page(feed.id, sort="published", order="asc")
        assert [r.ordinal for r in rows] == [3, 1, 2, 4]
        render = await uow.catalog.for_render(feed.id)
        assert [r.ordinal for r in render] == [2, 1]
        many = await uow.catalog.get_many([rows[0].id, rows[0].id, "0000000000000000"])
        assert len(many) == 1
        assert await uow.catalog.get_many([]) == []
        with pytest.raises(NotFound):
            await uow.catalog.require(rows[0].id, feed_id="another")


async def test_resolve_numbers_prefers_unique_source_numbers(
    uow_factory: UnitOfWorkFactory,
) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        rows = [
            item_row(feed, 1, source_number=10),
            item_row(feed, 2, source_number=20, state=ArchiveState.archived),
            item_row(feed, 3, source_number=20),
            item_row(feed, 4, source_number=None),
        ]
        await seed(uow, *rows)
        resolved = await uow.catalog.resolve_numbers(feed.id, numbers=[10, 20, 3, 4, 99])
        # 10 -> source number; 20 is ambiguous -> ordinal 20 missing -> unresolved;
        # 3 -> ordinal 3; 4 -> ordinal 4; 99 unknown.
        assert resolved.resolved == [rows[0].id, rows[2].id, rows[3].id]
        assert resolved.unresolved == ["20", "99"]
        assert resolved.already_archived_count == 0
        by_ordinal = await uow.catalog.resolve_numbers(
            feed.id, numbers=[2], item_ids=[rows[0].id, "zzzz"], numbering=Numbering.ordinal
        )
        assert by_ordinal.resolved == [rows[1].id, rows[0].id]
        assert by_ordinal.unresolved == ["zzzz"]
        assert by_ordinal.already_archived_count == 1
        assert by_ordinal.numbering_used is Numbering.ordinal
        candidates = await uow.catalog.selection_candidates(feed.id)
        assert [c.ordinal for c in candidates] == [1, 2, 3, 4]


async def test_available_ids_policy_input(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        rows = [
            item_row(feed, 1, first_seen_at=NOW - timedelta(days=10)),
            item_row(feed, 2, first_seen_at=NOW - timedelta(days=5)),
            item_row(feed, 3, first_seen_at=NOW - timedelta(days=1)),
            item_row(feed, 4, listed=False),
            item_row(feed, 5, archivable=False),
            item_row(feed, 6, state=ArchiveState.archived),
        ]
        await seed(uow, *rows)
        assert await uow.catalog.available_ids(feed.id) == [rows[2].id, rows[1].id, rows[0].id]
        assert await uow.catalog.available_ids(feed.id, latest_n=2) == [rows[2].id, rows[1].id]
        assert await uow.catalog.available_ids(
            feed.id, first_seen_after=NOW - timedelta(days=3)
        ) == [rows[2].id]
        assert await uow.catalog.ids_in_state(feed.id, ArchiveState.archived) == [rows[5].id]
