"""AssetRepository, RequestRepository and TelemetryRepository behaviours."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from copycast.adapters.db.uow import UnitOfWorkFactory
from copycast.domain.enums import (
    AssetFormat,
    AssetKind,
    AssetProvenance,
    AssetState,
    JobTrigger,
    RefreshRunStatus,
    RequestedVia,
)
from copycast.domain.exceptions import NotFound
from copycast.domain.ids import asset_id
from tests.integration.storage.conftest import NOW, asset_row, inbox_row, item_row, mirror_row, seed

pytestmark = pytest.mark.integration


async def test_asset_upsert_is_keyed_by_identity(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        item = item_row(feed, 1)
        await seed(uow, item)
        wanted = await uow.assets.upsert(
            feed.id,
            item.id,
            AssetKind.transcript,
            language="en",
            format=AssetFormat.vtt,
            remote_url="https://podcast.example/en.vtt",
        )
        assert wanted.id == asset_id(feed.id, item.id, "transcript", "en", "vtt", "mirrored")
        assert wanted.state == AssetState.wanted and wanted.local_path is None
        archived = await uow.assets.upsert(
            feed.id,
            item.id,
            AssetKind.transcript,
            language="en",
            format=AssetFormat.vtt,
            remote_url="https://podcast.example/en.vtt",
            local_path=f"assets/{item.id}.transcript.en.mirrored.vtt",
            mime="text/vtt",
            size_bytes=42,
            state=AssetState.archived,
            fetched_at=NOW,
        )
        assert archived.id == wanted.id  # same identity, updated in place
        assert (archived.state, archived.size_bytes) == ("archived", 42)
        generated = await uow.assets.upsert(
            feed.id,
            item.id,
            AssetKind.transcript,
            language="en",
            format=AssetFormat.vtt,
            provenance=AssetProvenance.generated,
        )
        assert generated.id != archived.id
        assert len(await uow.assets.for_item(item.id)) == 2
        assert (await uow.assets.for_items([item.id, "0000000000000000"]))[item.id][0].id
        assert await uow.assets.for_items([]) == {}
        assert await uow.assets.archived_bytes(feed.id) == 42


async def test_asset_marks_lookups_and_deletion(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        item = item_row(feed, 1)
        await seed(uow, item)
        artwork = asset_row(feed, None)
        item_art = asset_row(feed, item)
        chapters = asset_row(feed, item, AssetKind.chapters, state=AssetState.wanted)
        await seed(uow, artwork, item_art, chapters)
        feed_art = await uow.assets.feed_artwork(feed.id)
        assert feed_art is not None and feed_art.id == artwork.id
        assert feed_art.basename == "feed.artwork.jpg"
        found = await uow.assets.by_basename(feed.id, f"{item.id}.artwork.jpg")
        assert found is not None and found.id == item_art.id
        assert await uow.assets.by_basename(feed.id, f"{item.id}.chapters.json") is None  # wanted
        assert await uow.assets.by_basename(feed.id, "../feed.json") is None
        assert await uow.assets.mark(
            chapters.id,
            AssetState.archived,
            local_path=f"assets/{item.id}.chapters.json",
            mime="application/json",
            size_bytes=10,
            fetched_at=NOW,
        )
        assert (chapters.state, chapters.size_bytes) == ("archived", 10)
        assert await uow.assets.mark(chapters.id, AssetState.failed, last_error="404")
        assert (chapters.state, chapters.last_error) == ("failed", "404")
        assert not await uow.assets.mark("0000000000000000", AssetState.failed)
        listing = await uow.assets.for_feed(feed.id)
        assert listing[0].id == artwork.id  # feed Artwork (NULL item) sorts first
        removed = await uow.assets.delete_for_item(item.id)
        assert {a.id for a in removed} == {item_art.id, chapters.id}
        assert await uow.assets.for_item(item.id) == []
        assert await uow.assets.delete_for_item(item.id) == []
        assert await uow.assets.delete(artwork.id) is True
        assert await uow.assets.delete(artwork.id) is False
        assert await uow.assets.get(artwork.id) is None


async def test_requests_lifecycle(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        inbox = await uow.feeds.add(inbox_row())
        other = await uow.feeds.add(inbox_row("Other"))
        items = [item_row(inbox, 1), item_row(inbox, 2)]
        await seed(uow, *items)
        request = await uow.requests.add(inbox.id, "https://x.example/playlist", RequestedVia.mcp)
        assert request.status == "queued" and request.item_count == 0
        assert (await uow.requests.require(request.id)).id == request.id
        with pytest.raises(NotFound):
            await uow.requests.require(request.id, feed_id=other.id)
        with pytest.raises(NotFound):
            await uow.requests.require(uuid.uuid4())
        assert await uow.requests.link_items(request.id, [i.id for i in items]) == 2
        assert await uow.requests.link_items(request.id, [items[0].id]) == 0
        assert await uow.requests.link_items(request.id, []) == 0
        assert set(await uow.requests.item_ids(request.id)) == {i.id for i in items}
        grouped = await uow.requests.item_ids_many([request.id])
        assert set(grouped[request.id]) == {i.id for i in items}
        assert await uow.requests.item_ids_many([]) == {}
        by_item = await uow.requests.request_ids_for_items([items[0].id, "0000000000000000"])
        assert by_item[items[0].id] == [request.id] and by_item["0000000000000000"] == []
        assert await uow.requests.request_ids_for_items([]) == {}
        await uow.requests.mark_expanded(request.id, 2, at=NOW)
        assert (request.status, request.item_count, request.expanded_at) == ("expanded", 2, NOW)
        failed = await uow.requests.add(inbox.id, "https://x.example/rss", RequestedVia.ui)
        await uow.requests.mark_failed(failed.id, "use create_mirror")
        assert (failed.status, failed.error) == ("failed", "use create_mirror")
        # Both rows share the transaction's created_at, so only the set is deterministic.
        assert {r.id for r in await uow.requests.for_feed(inbox.id)} == {request.id, failed.id}
        page, total = await uow.requests.list_page(inbox.id, limit=1)
        assert total == 2 and len(page) == 1
        counts = await uow.requests.count_for_feeds([inbox.id, other.id])
        assert counts == {inbox.id: 2, other.id: 0}
        assert await uow.requests.count_for_feeds([]) == {}


async def test_telemetry_repository(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        run = await uow.telemetry.start_refresh_run(feed.id, trigger=JobTrigger.scheduled)
        assert run.status == "running" and run.id > 0
        await uow.telemetry.finish_refresh_run(
            run.id,
            RefreshRunStatus.succeeded,
            listed_count=10,
            new_count=2,
            delisted_count=1,
            wanted_count=2,
            engine_version="2026.08.19",
        )
        assert (run.status, run.listed_count, run.new_count) == ("succeeded", 10, 2)
        assert run.finished_at is not None
        runs = await uow.telemetry.refresh_runs(feed.id)
        assert [r.id for r in runs] == [run.id]
        assert await uow.telemetry.purge_refresh_runs(NOW - timedelta(days=90)) == 0

        await uow.telemetry.heartbeat("worker-1", {"running_jobs": 1})
        await uow.telemetry.heartbeat("worker-1", {"running_jobs": 2})
        beat = await uow.telemetry.latest_heartbeat()
        assert beat is not None and beat.status == {"running_jobs": 2}

        first = await uow.telemetry.record_engine_version(
            "2026.08.19", channel="nightly", release_date=date(2026, 8, 19), app_version="1.0.0"
        )
        second = await uow.telemetry.record_engine_version("2026.08.19", channel="stable")
        assert first.id == second.id and second.channel == "nightly"
        assert [v.version for v in await uow.telemetry.engine_versions()] == ["2026.08.19"]
