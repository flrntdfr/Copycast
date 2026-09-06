"""``copycast rebuild``: round-trip from disk, guards, reconciliation, idempotence."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from copycast.adapters.db.locks import WORKER_LOCK, SessionLock
from copycast.adapters.db.models import Asset, CatalogItem, Feed, Request, RequestItem
from copycast.adapters.db.uow import UnitOfWorkFactory
from copycast.adapters.storage.layout import LAYOUT_VERSION_FILE, Layout, LayoutMismatch
from copycast.adapters.storage.rebuild import RebuildRefused, RebuildReport, _channel_xml, rebuild
from copycast.domain.enums import (
    ArchiveState,
    AssetFormat,
    AssetKind,
    AssetState,
    RequestedVia,
    WantedReason,
)
from copycast.domain.ids import item_id
from copycast.settings import Settings
from tests.integration.storage.conftest import (
    NOW,
    asset_row,
    inbox_row,
    item_row,
    mirror_row,
    seed,
)
from tests.support.factories import listing

pytestmark = pytest.mark.integration

FEED_TELEMETRY = {
    "created_at",
    "updated_at",
    "revision",
    "last_refresh_attempt_at",
    "last_refresh_success_at",
    "last_error",
    "source_etag",
    "source_last_modified",
    "last_autoprune_at",
}
ITEM_TELEMETRY = {
    "created_at",
    "updated_at",
    "attempt_count",
    "last_error",
    "download_count",
    "first_downloaded_at",
    "last_downloaded_at",
}
ASSET_TELEMETRY = {"updated_at", "last_error"}
REQUEST_TELEMETRY = {"updated_at"}

CHANNEL_XML = (
    '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
    "<channel><title>Test Podcast</title><item><title>gone</title></item></channel></rss>"
)


def _columns(row: Any, exclude: set[str]) -> dict[str, Any]:
    return {c.key: getattr(row, c.key) for c in row.__table__.columns if c.key not in exclude}


async def _snapshot(sessionmaker: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    async with sessionmaker() as session:
        feeds = (await session.execute(select(Feed).order_by(Feed.id))).scalars().all()
        items = (await session.execute(select(CatalogItem).order_by(CatalogItem.id))).scalars()
        assets = (await session.execute(select(Asset).order_by(Asset.id))).scalars().all()
        requests = (await session.execute(select(Request).order_by(Request.id))).scalars().all()
        links = (await session.execute(select(RequestItem))).scalars().all()
        return {
            "feeds": [_columns(f, FEED_TELEMETRY) for f in feeds],
            "items": [_columns(i, ITEM_TELEMETRY) for i in items],
            "assets": [_columns(a, ASSET_TELEMETRY) for a in assets],
            "requests": [_columns(r, REQUEST_TELEMETRY) for r in requests],
            "links": sorted((str(link.request_id), link.item_id) for link in links),
        }


async def _populate(uow_factory: UnitOfWorkFactory, layout: Layout) -> tuple[str, str]:
    """A Mirror with real files and an Inbox with a Request; descriptors exported."""
    async with uow_factory() as uow:
        default = await uow.feeds.ensure_default_inbox()
        mirror = await uow.feeds.add(
            mirror_row(source_channel_xml=_channel_xml(CHANNEL_XML.encode()))
        )
        await uow.catalog.upsert_listing(mirror.id, listing(5))
        rows = await uow.catalog.for_feed(mirror.id)
        layout.ensure_feed_dirs(mirror.id)
        layout.source_xml_path(mirror.id).write_text(CHANNEL_XML)
        for row in rows[:3]:  # ordinals 1..3 archived with files on disk
            media = layout.media_path(mirror.id, row.id, "m4a")
            media.write_bytes(b"\0" * (1000 + row.ordinal))
            layout.item_xml_path(mirror.id, row.id).write_text(
                f"<item><guid>{row.source_key}</guid><title>{row.title}</title></item>"
            )
            await uow.catalog.mark_state(
                row.id,
                ArchiveState.archived,
                media_path=layout.relative(mirror.id, media),
                media_mime="audio/mp4",
                media_bytes=media.stat().st_size,
                archived_at=NOW,
            )
            await uow.catalog.set_source_item_xml(
                row.id, layout.item_xml_path(mirror.id, row.id).read_text()
            )
        await uow.catalog.set_wanted([rows[3].id], WantedReason.backfill)
        await uow.catalog.tombstone(rows[4].id, listed=True, at=NOW)
        await uow.catalog.record_download(rows[0].id, at=NOW)  # telemetry, lost on rebuild
        feed_art = layout.feed_artwork_path(mirror.id, "jpg")
        feed_art.write_bytes(b"jpg" * 10)
        item_art = layout.item_artwork_path(mirror.id, rows[0].id, "jpg")
        item_art.write_bytes(b"jpg" * 5)
        transcript = layout.transcript_path(mirror.id, rows[1].id, "en", "mirrored", "vtt")
        transcript.write_text("WEBVTT\n")
        await uow.assets.upsert(
            mirror.id,
            None,
            AssetKind.artwork,
            local_path=layout.relative(mirror.id, feed_art),
            mime="image/jpeg",
            size_bytes=30,
            state=AssetState.archived,
            fetched_at=NOW,
        )
        await uow.assets.upsert(
            mirror.id,
            rows[0].id,
            AssetKind.artwork,
            local_path=layout.relative(mirror.id, item_art),
            mime="image/jpeg",
            size_bytes=15,
            state=AssetState.archived,
            fetched_at=NOW,
        )
        await uow.assets.upsert(
            mirror.id,
            rows[1].id,
            AssetKind.transcript,
            language="en",
            format=AssetFormat.vtt,
            remote_url="https://podcast.example/en.vtt",
            local_path=layout.relative(mirror.id, transcript),
            mime="text/vtt",
            size_bytes=7,
            state=AssetState.archived,
            fetched_at=NOW,
        )
        await uow.assets.upsert(
            mirror.id,
            rows[2].id,
            AssetKind.chapters,
            format=AssetFormat.json,
            remote_url="https://podcast.example/chapters.json",
            state=AssetState.wanted,
        )
        await uow.feeds.recount_storage(mirror.id)
        await uow.update_intent(mirror.id)

        inbox = await uow.feeds.add(inbox_row("Later", autoprune_days=14))
        inbox_item = item_row(inbox, 1, state=ArchiveState.archived)
        await seed(uow, inbox_item)
        layout.ensure_feed_dirs(inbox.id)
        layout.media_path(inbox.id, inbox_item.id, "m4a").write_bytes(b"\0" * 1000)
        request = await uow.requests.add(inbox.id, "https://x.example/v", RequestedVia.mcp)
        await uow.requests.link_items(request.id, [inbox_item.id])
        await uow.requests.mark_expanded(request.id, 1, at=NOW)
        await uow.feeds.recount_storage(inbox.id)
        await uow.update_intent(inbox.id)
        await uow.update_intent(default.id)
    return mirror.id, inbox.id


async def _wipe(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as session, session.begin():
        for feed in (await session.execute(select(Feed))).scalars().all():
            await session.delete(feed)


async def test_round_trip_restores_everything_but_telemetry(
    uow_factory: UnitOfWorkFactory,
    layout: Layout,
    settings: Settings,
    db_engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    mirror_id, inbox_id = await _populate(uow_factory, layout)
    before = await _snapshot(sessionmaker)
    assert len(before["feeds"]) == 3
    await _wipe(sessionmaker)
    assert (await _snapshot(sessionmaker))["feeds"] == []

    report = await rebuild(settings, yes=False, db_engine=db_engine)
    assert isinstance(report, RebuildReport)
    assert report.feeds_on_disk == 3 and report.feeds_upserted == 3
    assert report.items_upserted == 6 and report.requests_upserted == 1
    assert report.media_archived == 4 and report.media_reset_to_wanted == 0
    assert report.skipped == [] and report.deleted_feeds == []

    after = await _snapshot(sessionmaker)
    assert after == before

    async with sessionmaker() as session:
        mirror = await session.get(Feed, mirror_id)
        assert mirror is not None
        assert mirror.source_channel_xml is not None
        assert "<item>" not in mirror.source_channel_xml  # items stripped from the channel cache
        assert (
            mirror.storage_bytes
            == before["feeds"][[f["id"] for f in before["feeds"]].index(mirror_id)]["storage_bytes"]
        )
        first = await session.get(CatalogItem, item_id(mirror_id, "urn:test:item:1"))
        assert first is not None
        assert first.download_count == 0  # telemetry is lost by design
        assert first.source_item_xml is not None and "<guid>" in first.source_item_xml
        tomb = await session.get(CatalogItem, item_id(mirror_id, "urn:test:item:5"))
        assert tomb is not None and tomb.archive_state == ArchiveState.deleted
        inbox = await session.get(Feed, inbox_id)
        assert inbox is not None and inbox.autoprune_days == 14

    # Idempotent: a second run changes nothing but the revision.
    again = await rebuild(settings, yes=True, db_engine=db_engine)
    assert again.deleted_feeds == [] and again.deleted_items == 0
    assert await _snapshot(sessionmaker) == after


async def test_wrong_layout_version_refuses(
    settings: Settings, data_dir: Path, db_engine: AsyncEngine
) -> None:
    (data_dir / LAYOUT_VERSION_FILE).write_text("2\n")
    with pytest.raises(LayoutMismatch, match="layout version '2'"):
        await rebuild(settings, yes=False, db_engine=db_engine)


async def test_refuses_while_the_worker_holds_its_lock(
    settings: Settings, db_engine: AsyncEngine
) -> None:
    async with SessionLock(db_engine, WORKER_LOCK) as worker:
        assert worker.held
        with pytest.raises(RebuildRefused, match="WORKER_LOCK"):
            await rebuild(settings, yes=False, db_engine=db_engine)
        report = await rebuild(settings, yes=False, lock_held=True, db_engine=db_engine)
        assert report.default_inbox_id.startswith("copycast-")
    report = await rebuild(settings, yes=False, db_engine=db_engine)
    assert report.feeds_on_disk == 1  # the default Inbox's descriptor was exported


async def test_reconciles_media_and_ignores_tmp_leftovers(
    uow_factory: UnitOfWorkFactory,
    layout: Layout,
    settings: Settings,
    db_engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with uow_factory() as uow:
        mirror = await uow.feeds.add(mirror_row())
        await uow.catalog.upsert_listing(mirror.id, listing(3))
        rows = await uow.catalog.for_feed(mirror.id)
        layout.ensure_feed_dirs(mirror.id)
        # ordinal 1: claims archived but the file is gone -> wanted
        await uow.catalog.mark_state(
            rows[0].id,
            ArchiveState.archived,
            media_path=f"media/{rows[0].id}.m4a",
            media_mime="audio/mp4",
            media_bytes=5,
        )
        # ordinal 2: archiving with only a .part in tmp/ -> wanted, .part ignored
        await uow.catalog.mark_state(rows[1].id, ArchiveState.archiving)
        (layout.tmp_dir(mirror.id) / f"{rows[1].id}.m4a.part").write_bytes(b"\0" * 999)
        # ordinal 3: available but a file exists -> archived from the file
        layout.media_path(mirror.id, rows[2].id, "mp3").write_bytes(b"\0" * 4407)
        await uow.update_intent(mirror.id)
    # An orphan media file with sidecars: a row is created, ordinal appended.
    orphan_id = "abcdef0123456789"
    layout.media_path(mirror.id, orphan_id, "m4a").write_bytes(b"\0" * 10)
    layout.info_json_path(mirror.id, orphan_id).write_text(
        json.dumps(
            {
                "id": "xyz",
                "extractor_key": "Youtube",
                "extractor": "youtube",
                "title": "Orphan",
                "timestamp": 1_700_000_000,
                "duration": 61.5,
                "uploader": "Someone",
                "webpage_url": "https://youtube.example/xyz",
            }
        )
    )
    # A stray asset file for the orphan and one for nobody.
    layout.item_artwork_path(mirror.id, orphan_id, "jpg").write_bytes(b"x")
    (layout.assets_dir(mirror.id) / "README.txt").write_text("not ours")
    (layout.assets_dir(mirror.id) / ".feed.json.deadbeef.tmp").write_text("leftover")

    report = await rebuild(settings, yes=False, db_engine=db_engine)
    assert report.media_reset_to_wanted == 2
    assert report.media_archived == 1  # the orphan counts under rows_from_files
    assert report.rows_from_files == 1
    assert report.assets_from_files == 1

    async with sessionmaker() as session:
        rows = (
            (
                await session.execute(
                    select(CatalogItem)
                    .where(CatalogItem.feed_id == mirror.id)
                    .order_by(CatalogItem.ordinal)
                )
            )
            .scalars()
            .all()
        )
        assert [(r.ordinal, r.archive_state) for r in rows] == [
            (1, ArchiveState.wanted),
            (2, ArchiveState.wanted),
            (3, ArchiveState.archived),
            (4, ArchiveState.archived),
        ]
        assert rows[0].media_path is None and rows[0].wanted_reason == WantedReason.backfill
        assert rows[2].media_mime == "audio/mpeg" and rows[2].media_bytes == 4407
        orphan = rows[3]
        assert orphan.id == orphan_id and orphan.listed is False
        assert orphan.source_key == "Youtube:xyz" and orphan.title == "Orphan"
        assert orphan.duration_seconds == 61 and orphan.author == "Someone"
        assert orphan.published_at is not None and orphan.published_at.year == 2023
        assets = (
            (await session.execute(select(Asset).where(Asset.feed_id == mirror.id))).scalars().all()
        )
        assert len(assets) == 1 and assets[0].item_id == orphan_id
        feed = await session.get(Feed, mirror.id)
        assert feed is not None
        assert feed.storage_bytes == 4407 + 10 + 1  # the .part in tmp/ never counts


async def test_yes_deletes_rows_absent_from_disk_and_skips_unreadable_dirs(
    uow_factory: UnitOfWorkFactory,
    layout: Layout,
    settings: Settings,
    db_engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with uow_factory() as uow:
        kept = await uow.feeds.add(mirror_row("https://kept.example/feed"))
        await uow.catalog.upsert_listing(kept.id, listing(2))
        await uow.update_intent(kept.id)
    async with uow_factory() as uow:  # after the export: rows that exist only in the database
        ghost = await uow.feeds.add(mirror_row("https://ghost.example/feed"))  # never exported
        stale_only_in_db = item_row(kept, 9, state=ArchiveState.available)
        await seed(uow, stale_only_in_db, asset_row(kept, stale_only_in_db))
    # Unreadable directories are reported, not fatal.
    (layout.feeds_dir / "no-descriptor").mkdir()
    corrupt = layout.feeds_dir / "corrupt"
    corrupt.mkdir()
    (corrupt / "feed.json").write_text("{not json")
    foreign = layout.feeds_dir / "foreign"
    foreign.mkdir()
    shutil.copy(layout.descriptor_path(kept.id), foreign / "feed.json")

    dry = await rebuild(settings, yes=False, db_engine=db_engine)
    assert dry.would_delete_feeds == [ghost.id]
    assert (dry.would_delete_items, dry.would_delete_assets) == (1, 1)
    skipped = {s.feed_id: s.reason for s in dry.skipped}
    assert skipped["no-descriptor"] == "no feed.json"
    assert skipped["corrupt"].startswith(f"{corrupt / 'feed.json'}: not JSON")
    assert skipped["foreign"] == f"feed.json belongs to {kept.id!r}"
    async with sessionmaker() as session:
        assert await session.get(Feed, ghost.id) is not None

    wet = await rebuild(settings, yes=True, db_engine=db_engine)
    assert wet.deleted_feeds == [ghost.id]
    assert (wet.deleted_items, wet.deleted_assets) == (1, 1)
    async with sessionmaker() as session:
        assert await session.get(Feed, ghost.id) is None
        assert await session.get(CatalogItem, stale_only_in_db.id) is None
        remaining = (
            (await session.execute(select(CatalogItem).where(CatalogItem.feed_id == kept.id)))
            .scalars()
            .all()
        )
        assert len(remaining) == 2


async def test_rebuild_keeps_the_feed_pair_and_mints_one_for_old_descriptors(
    uow_factory: UnitOfWorkFactory,
    layout: Layout,
    settings: Settings,
    db_engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    mirror_id, inbox_id = await _populate(uow_factory, layout)
    async with sessionmaker() as session:
        mirror = await session.get(Feed, mirror_id)
        assert mirror is not None
        pair = (mirror.auth_username, mirror.auth_password)
    # A descriptor written before credentials existed carries none of them.
    inbox_descriptor = layout.descriptor_path(inbox_id)
    data = json.loads(inbox_descriptor.read_text(encoding="utf-8"))
    del data["feed"]["auth_username"], data["feed"]["auth_password"]
    inbox_descriptor.write_text(json.dumps(data), encoding="utf-8")
    await _wipe(sessionmaker)

    await rebuild(settings, yes=False, db_engine=db_engine)
    async with sessionmaker() as session:
        mirror = await session.get(Feed, mirror_id)
        inbox = await session.get(Feed, inbox_id)
        assert mirror is not None and inbox is not None
        assert (mirror.auth_username, mirror.auth_password) == pair
        assert len(inbox.auth_username) == 8 and len(inbox.auth_password) == 24
