"""FeedRepository: default Inbox, counters, storage recount, scheduling queries."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from copycast.adapters.db.repositories import DEFAULT_INBOX_TITLE, FeedRepository
from copycast.adapters.db.uow import UnitOfWorkFactory
from copycast.domain.enums import ArchiveState, AssetKind, AssetState, FeedKind
from copycast.domain.exceptions import NotFound
from copycast.domain.ids import FEED_ID_RE
from tests.integration.storage.conftest import (
    NOW,
    SOURCE_URL,
    asset_row,
    inbox_row,
    item_row,
    mirror_row,
    seed,
)

pytestmark = pytest.mark.integration


async def test_ensure_default_inbox_is_idempotent(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        first = await uow.feeds.ensure_default_inbox()
    async with uow_factory() as uow:
        second = await uow.feeds.ensure_default_inbox()
        assert (await uow.feeds.count(FeedKind.inbox)) == 1
    assert first.id == second.id
    assert first.title == DEFAULT_INBOX_TITLE
    assert first.is_default is True
    assert first.kind == FeedKind.inbox
    assert FEED_ID_RE.match(first.id)
    assert first.id.startswith("copycast-")


async def test_ensure_default_inbox_under_concurrency(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async def _create() -> str:
        async with sessionmaker() as session, session.begin():
            return (await FeedRepository(session).ensure_default_inbox()).id

    ids = await asyncio.gather(*(_create() for _ in range(4)))
    assert len(set(ids)) == 1


async def test_get_require_and_dedup_lookup(uow_factory: UnitOfWorkFactory) -> None:
    feed = mirror_row()
    async with uow_factory() as uow:
        await uow.feeds.add(feed)
    async with uow_factory() as uow:
        assert (await uow.feeds.get(feed.id)) is not None
        assert (await uow.feeds.require(feed.id)).source_url == SOURCE_URL
        found = await uow.feeds.by_dedup_key("podcast.example/feed.xml")
        assert found is not None and found.id == feed.id
        assert await uow.feeds.get("nope") is None
        with pytest.raises(NotFound):
            await uow.feeds.require("nope")
        with pytest.raises(NotFound):
            await uow.feeds.get_for_update("nope")
        locked = await uow.feeds.get_for_update(feed.id)
        assert locked.id == feed.id


async def test_find_inbox_by_id_or_case_insensitive_name(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        default = await uow.feeds.ensure_default_inbox()
        later = await uow.feeds.add(inbox_row("Later"))
        mirror = await uow.feeds.add(mirror_row())
    async with uow_factory() as uow:
        assert (await uow.feeds.find_inbox(later.id)) is not None
        by_name = await uow.feeds.find_inbox("copycast")
        assert by_name is not None and by_name.id == default.id
        by_name = await uow.feeds.find_inbox("LATER")
        assert by_name is not None and by_name.id == later.id
        assert await uow.feeds.find_inbox(mirror.id) is None
        assert await uow.feeds.find_inbox("Test Podcast") is None
        assert await uow.feeds.find_inbox("unknown") is None


async def test_list_sorting_and_kind_filter(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        await uow.feeds.add(mirror_row("https://a.example/feed", title="beta"))
        await uow.feeds.add(mirror_row("https://b.example/feed", title="Alpha"))
        await uow.feeds.add(inbox_row("Zulu"))
    async with uow_factory() as uow:
        titles = [f.title for f in await uow.feeds.list()]
        assert titles == ["Alpha", "beta", "Zulu"]
        titles = [f.title for f in await uow.feeds.list(sort="title", order="desc")]
        assert titles == ["Zulu", "beta", "Alpha"]
        mirrors = await uow.feeds.list(FeedKind.mirror)
        assert {f.kind for f in mirrors} == {"mirror"} and len(mirrors) == 2
        assert await uow.feeds.count() == 3
        assert await uow.feeds.count(FeedKind.inbox) == 1
        assert len(await uow.feeds.ids()) == 3


async def test_counters_bump_and_refresh_loaded_instance(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        assert (feed.intent_version, feed.revision) == (1, 1)
        assert await uow.feeds.bump_revision(feed.id) == 2
        assert feed.revision == 2  # the loaded instance follows the Core UPDATE
        assert await uow.feeds.update_intent(feed.id) == (2, 3)
        assert (feed.intent_version, feed.revision) == (2, 3)
        with pytest.raises(NotFound):
            await uow.feeds.bump_revision("nope")
        with pytest.raises(NotFound):
            await uow.feeds.update_intent("nope")


async def test_recount_storage_sums_archived_media_and_assets(
    uow_factory: UnitOfWorkFactory,
) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        archived = item_row(feed, 1, state=ArchiveState.archived, media_bytes=5000)
        also = item_row(feed, 2, state=ArchiveState.archived, media_bytes=7000)
        wanted = item_row(feed, 3, state=ArchiveState.wanted, media_bytes=999_999)
        await seed(uow, archived, also, wanted)
        await seed(
            uow,
            asset_row(feed, None, size_bytes=100),
            asset_row(feed, archived, size_bytes=50),
            asset_row(feed, also, AssetKind.chapters, state=AssetState.wanted, size_bytes=1),
        )
        total = await uow.feeds.recount_storage(feed.id)
        assert total == 5000 + 7000 + 100 + 50
        assert feed.storage_bytes == total
        with pytest.raises(NotFound):
            await uow.feeds.recount_storage("nope")
    async with uow_factory() as uow:
        totals = await uow.feeds.totals()
        assert (totals.feeds, totals.episodes, totals.storage_bytes) == (1, 2, 12150)


async def test_scheduler_queries(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        never = await uow.feeds.add(mirror_row("https://a.example/feed", title="never"))
        stale = await uow.feeds.add(
            mirror_row(
                "https://b.example/feed",
                title="stale",
                last_refresh_attempt_at=NOW - timedelta(hours=48),
            )
        )
        await uow.feeds.add(
            mirror_row("https://c.example/feed", title="fresh", last_refresh_attempt_at=NOW)
        )
        await uow.feeds.add(
            mirror_row(
                "https://d.example/feed",
                title="paused",
                paused=True,
                last_refresh_attempt_at=NOW - timedelta(days=9),
            )
        )
        prune_me = await uow.feeds.add(inbox_row("Prune", autoprune_days=3))
        await uow.feeds.add(inbox_row("Recent", autoprune_days=3, last_autoprune_at=NOW))
        await uow.feeds.add(inbox_row("Off"))
    async with uow_factory() as uow:
        due = await uow.feeds.mirrors_due(NOW - timedelta(hours=24))
        assert [f.id for f in due] == [never.id, stale.id]
        prunable = await uow.feeds.inboxes_due_autoprune(NOW - timedelta(hours=24))
        assert [f.id for f in prunable] == [prune_me.id]


async def test_refresh_bookkeeping_and_metadata(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row(title="Old"))
        await uow.feeds.set_refresh_attempt(feed.id, NOW)
        assert feed.last_refresh_attempt_at == NOW
        await uow.feeds.set_refresh_outcome(feed.id, success_at=None, error="boom")
        assert (feed.last_error, feed.last_refresh_success_at) == ("boom", None)
        await uow.feeds.set_refresh_outcome(feed.id, success_at=NOW, error=None)
        assert (feed.last_error, feed.last_refresh_success_at) == (None, NOW)
        await uow.feeds.apply_metadata(feed.id, title="  ", author="New Author", language=None)
        assert (feed.title, feed.author) == ("Old", "New Author")
        await uow.feeds.apply_metadata(feed.id)  # nothing to do
        assert feed.title == "Old"


async def test_delete_cascades_to_items(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        await seed(uow, item_row(feed, 1), item_row(feed, 2))
    async with uow_factory() as uow:
        assert await uow.feeds.delete(feed.id) is True
        assert await uow.feeds.delete(feed.id) is False
    async with uow_factory() as uow:
        assert await uow.catalog.for_feed(feed.id) == []
