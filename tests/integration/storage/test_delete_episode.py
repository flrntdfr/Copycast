"""``delete_episode`` through the real unit of work: files, tombstone, counters, export."""

from __future__ import annotations

import pytest

from copycast.adapters.db.uow import UnitOfWorkFactory
from copycast.adapters.storage.descriptor import FeedDescriptor
from copycast.adapters.storage.layout import Layout
from copycast.application.services.episodes import delete_episode
from copycast.domain.enums import ArchiveState, AssetKind, AssetState, DeleteReason
from copycast.domain.exceptions import NotFound
from tests.integration.storage.conftest import NOW, inbox_row, item_row, mirror_row, seed

pytestmark = pytest.mark.integration


async def test_delete_episode_mirror(uow_factory: UnitOfWorkFactory, layout: Layout) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        episode = item_row(feed, 1, state=ArchiveState.archived, media_bytes=2000)
        other = item_row(feed, 2, state=ArchiveState.archived, media_bytes=3000)
        await seed(uow, episode, other)
        layout.ensure_feed_dirs(feed.id)
        media = layout.media_path(feed.id, episode.id, "m4a")
        media.write_bytes(b"\0" * 2000)
        info = layout.info_json_path(feed.id, episode.id)
        info.write_text("{}")
        item_xml = layout.item_xml_path(feed.id, episode.id)
        item_xml.write_text("<item/>")
        part = layout.tmp_dir(feed.id) / f"{episode.id}.m4a.part"
        part.write_bytes(b"\0")
        artwork = layout.item_artwork_path(feed.id, episode.id, "jpg")
        artwork.write_bytes(b"jpg")
        await uow.assets.upsert(
            feed.id,
            episode.id,
            AssetKind.artwork,
            local_path=layout.relative(feed.id, artwork),
            mime="image/jpeg",
            size_bytes=3,
            state=AssetState.archived,
        )
        await uow.feeds.recount_storage(feed.id)
        assert feed.storage_bytes == 5003

    async with uow_factory() as uow:
        result = await delete_episode(uow, episode.id, DeleteReason.user)
        assert result.was_archived is True and result.reason is DeleteReason.user
        assert result.bytes_freed == 2003
        assert result.revision == 2 and result.intent_version == 2
        assert media.exists()  # files go only once the transaction committed
    assert not media.exists() and not info.exists() and not part.exists()
    assert not artwork.exists()
    assert item_xml.exists()  # the Source's original <item> is kept

    async with uow_factory() as uow:
        row = await uow.catalog.require(episode.id)
        assert row.archive_state == ArchiveState.deleted and row.listed is True
        assert row.media_path is None and row.deleted_at is not None
        assert await uow.assets.for_item(episode.id) == []
        refreshed = await uow.feeds.require(feed.id)
        assert refreshed.storage_bytes == 3000
        assert (refreshed.intent_version, refreshed.revision) == (2, 2)
    descriptor = FeedDescriptor.read(layout.descriptor_path(feed.id))
    states = {i.id: i.archive_state for i in descriptor.items}
    assert states[episode.id] is ArchiveState.deleted


async def test_delete_episode_inbox_hides_row_and_prune_reason(
    uow_factory: UnitOfWorkFactory, layout: Layout
) -> None:
    async with uow_factory() as uow:
        inbox = await uow.feeds.add(inbox_row())
        episode = item_row(inbox, 1, state=ArchiveState.archived)
        await seed(uow, episode)
    async with uow_factory() as uow:
        result = await delete_episode(uow, episode.id, DeleteReason.prune)
        assert result.reason is DeleteReason.prune and result.bytes_freed == 1000
    async with uow_factory() as uow:
        row = await uow.catalog.require(episode.id)
        assert row.archive_state == ArchiveState.deleted and row.listed is False
        counts = await uow.catalog.count_by_state(inbox.id)
        assert counts.available == 0


async def test_delete_episode_never_archived_still_tombstones(
    uow_factory: UnitOfWorkFactory,
) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        item = item_row(feed, 1, first_seen_at=NOW)
        await seed(uow, item)
    async with uow_factory() as uow:
        result = await delete_episode(uow, item.id)
        assert result.was_archived is False and result.bytes_freed == 0
    async with uow_factory() as uow:
        assert (await uow.catalog.require(item.id)).archive_state == ArchiveState.deleted
        with pytest.raises(NotFound):
            await delete_episode(uow, "0000000000000000")
