"""Title override, the operator's default policy, bulk archive/retry, purge."""

from __future__ import annotations

import pytest

from copycast.app import Container
from copycast.application.models import MirrorDefaults, MirrorRead, MirrorUpdate, PurgeRequest
from copycast.application.ports import PermanentError
from copycast.domain.enums import ArchiveState, BackfillMode
from copycast.worker.runner import Runner
from tests.integration.worker.conftest import Source, create_mirror, uow
from tests.support.fake_engine import FakeEngine

pytestmark = pytest.mark.integration


async def test_title_override_survives_refresh_and_clears(
    container: Container, source: Source, runner: Runner
) -> None:
    url = source.write_rss("titled", items=[1], artwork=False, title="Source Title")
    mirror = await create_mirror(container, url)
    await runner.run_until_idle()
    assert mirror.title == "Source Title" and mirror.title_override is None

    mine = await container.services.update_mirror(mirror.id, MirrorUpdate(title="  My Show "))
    assert mine.title == "My Show" and mine.source_title == "Source Title"
    assert mine.title_override == "My Show"
    listed = await container.services.list_feeds()
    assert [f.title for f in listed.feeds if f.id == mirror.id] == ["My Show"]
    rendered = await container.renderer.render(mirror.id)
    assert rendered is not None and b"<title>My Show</title>" in rendered.body

    # The Source's title keeps updating underneath; the override stays.
    source.write_rss("titled", items=[1], artwork=False, title="Renamed at the Source")
    job = await container.services.request_refresh(mirror.id)
    assert job is not None
    await runner.run_until_idle()
    read = await container.services.get_feed(mirror.id)
    assert isinstance(read, MirrorRead)
    assert read.title == "My Show" and read.source_title == "Renamed at the Source"

    # Blank or null restores the Source's title.
    restored = await container.services.update_mirror(mirror.id, MirrorUpdate(title=None))
    assert restored.title == "Renamed at the Source" and restored.title_override is None


async def test_new_mirrors_take_the_operator_default_policy(
    container: Container, source: Source, runner: Runner
) -> None:
    from copycast.application.models import MirrorCreate

    url = source.write_rss("defaulted", items=[2, 1], artwork=False)
    created = await container.services.create_mirror(MirrorCreate(source_url=url))
    assert created.backfill.mode is BackfillMode.automatic
    assert created.backfill.retention_days == 7 and created.follow is True
    await runner.run_until_idle()
    assert created.counts.archived == 0

    await container.services.set_mirror_defaults(MirrorDefaults(backfill={"mode": "all"}))
    other = source.write_rss("defaulted-all", items=[1], artwork=False)
    everything = await container.services.create_mirror(MirrorCreate(source_url=other))
    assert everything.backfill.mode is BackfillMode.all
    await runner.run_until_idle()
    assert (await container.services.get_feed(everything.id)).episode_count == 1


async def test_archive_available_retry_failed_and_purge(
    container: Container, source: Source, runner: Runner, engine: FakeEngine
) -> None:
    url = source.write_rss("bulk", items=[3, 2, 1], artwork=False)
    mirror = await create_mirror(container, url, mode=BackfillMode.automatic)
    await runner.run_until_idle()
    async with uow(container) as unit:
        states = {i.source_number: i.archive_state for i in await unit.catalog.for_feed(mirror.id)}
    assert set(states.values()) == {ArchiveState.available}

    # Everything Available is queued at once; the mode stays Automatic.
    queued = await container.services.archive_available(mirror.id)
    assert len(queued.resolved) == 3 and len(queued.jobs) == 3
    await runner.run_until_idle()
    read = await container.services.get_feed(mirror.id)
    assert read.episode_count == 3
    assert isinstance(read, MirrorRead) and read.backfill.mode is BackfillMode.automatic
    again = await container.services.archive_available(mirror.id)
    assert again.resolved == [] and again.jobs == []

    # A purge tombstones every archived Episode of every feed; a dry run only counts.
    preview = await container.services.purge_episodes(PurgeRequest(dry_run=True))
    assert preview.matched == 3 and preview.deleted_count == 0 and preview.bytes_freed > 0
    assert (await container.services.get_feed(mirror.id)).episode_count == 3
    purged = await container.services.purge_episodes(PurgeRequest(dry_run=False))
    assert purged.deleted_count == 3 and purged.bytes_freed == preview.bytes_freed
    read = await container.services.get_feed(mirror.id)
    assert read.episode_count == 0 and read.storage_bytes == 0
    assert not list(container.layout.media_dir(mirror.id).glob("*.m4a"))
    # Tombstones count as Available for an explicit request.
    back = await container.services.archive_available(mirror.id)
    assert len(back.resolved) == 3

    # Failed downloads are retried in bulk.
    for _ in range(3):
        engine.fail_next(PermanentError("video is private"))
    await runner.run_until_idle()
    async with uow(container) as unit:
        failed = await unit.catalog.ids_in_state(mirror.id, ArchiveState.failed)
    assert len(failed) == 3
    retried = await container.services.retry_failed(mirror.id)
    assert sorted(retried.resolved) == sorted(failed)
    await runner.run_until_idle()
    assert (await container.services.get_feed(mirror.id)).episode_count == 3
    assert (await container.services.retry_failed(mirror.id)).resolved == []


async def test_refresh_fills_dates_and_descriptions_and_keeps_the_first_date(
    container: Container, runner: Runner, engine: FakeEngine
) -> None:
    """A flat YouTube listing dates nothing; a later listing fills what is missing.

    The first date learnt stays (an approximate date would drift from Refresh to
    Refresh); the archive's exact date replaces it.
    """
    from datetime import UTC, datetime

    from tests.support.factories import listing, listing_item

    url = "https://www.youtube.com/@dated/videos"
    bare = [
        listing_item(1, published_at=None, description=None, position=1, source_number=1),
        listing_item(2, published_at=None, description=None, position=0, source_number=2),
    ]
    engine.script_listing(url, listing(2, service="YouTube", raw={"_type": "playlist"}, items=bare))
    mirror = await create_mirror(container, url, mode=BackfillMode.automatic)
    await runner.run_until_idle()
    async with uow(container) as unit:
        rows = {i.source_number: i for i in await unit.catalog.for_feed(mirror.id)}
    assert rows[1].published_at is None and rows[1].description is None

    first = datetime(2026, 8, 1, tzinfo=UTC)
    dated = [
        listing_item(
            1,
            published_at=first,
            published_at_exact=False,
            description="Notes 1",
            position=1,
            source_number=1,
        ),
        listing_item(2, published_at=None, description=None, position=0, source_number=2),
    ]
    engine.script_listing(
        url, listing(2, service="YouTube", raw={"_type": "playlist"}, items=dated)
    )
    job = await container.services.request_refresh(mirror.id)
    assert job is not None
    await runner.run_until_idle()
    async with uow(container) as unit:
        rows = {i.source_number: i for i in await unit.catalog.for_feed(mirror.id)}
    assert rows[1].published_at == first and rows[1].description == "Notes 1"
    assert rows[1].published_at_approximate is True
    read = await container.services.get_item(mirror.id, rows[1].id)
    assert read.published_at_approximate is True

    drifted = [
        listing_item(
            1, published_at=datetime(2026, 8, 3, tzinfo=UTC), published_at_exact=False, position=0
        )
    ]
    engine.script_listing(
        url, listing(1, service="YouTube", raw={"_type": "playlist"}, items=drifted)
    )
    job = await container.services.request_refresh(mirror.id)
    assert job is not None
    await runner.run_until_idle()
    async with uow(container) as unit:
        row = await unit.catalog.require(rows[1].id, mirror.id)
    assert row.published_at == first  # an approximate date never replaces a stored one
    # An exact date (an RSS pubDate the publisher corrected) does.
    corrected = datetime(2026, 8, 2, tzinfo=UTC)
    engine.script_listing(
        url,
        listing(
            1,
            service="YouTube",
            raw={"_type": "playlist"},
            items=[listing_item(1, published_at=corrected, position=0)],
        ),
    )
    job = await container.services.request_refresh(mirror.id)
    assert job is not None
    await runner.run_until_idle()
    async with uow(container) as unit:
        row = await unit.catalog.require(rows[1].id, mirror.id)
    assert row.published_at == corrected

    # The archive knows the exact date and replaces the approximate one.
    exact = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    engine.info_extra = {"upload_date": "20260730", "timestamp": int(exact.timestamp())}
    await container.services.archive_item(mirror.id, rows[1].id)
    await runner.run_until_idle()
    async with uow(container) as unit:
        row = await unit.catalog.require(rows[1].id, mirror.id)
    assert row.published_at == exact and row.published_at_approximate is False

    # The manual fetch asks the Source for the other item's full metadata.
    page_url = rows[2].source_url
    assert page_url
    engine.script_listing(
        page_url,
        listing(
            1,
            service="YouTube",
            items=[
                listing_item(
                    2,
                    published_at=datetime(2026, 6, 1, 8, 0, tzinfo=UTC),
                    description="Fetched by hand",
                    author="The channel",
                )
            ],
        ),
    )
    fetched = await container.services.fetch_item_metadata(mirror.id, rows[2].id)
    assert fetched.description == "Fetched by hand"
    assert fetched.published_at == datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    assert fetched.published_at_approximate is False


async def test_synced_mirror_deletes_what_the_source_drops(
    container: Container, source: Source, runner: Runner
) -> None:
    """A playlist kept in sync: an item that leaves the listing is tombstoned, not Delisted."""
    url = source.write_rss("synced", items=[2, 1], artwork=False)
    mirror = await create_mirror(container, url, mode=BackfillMode.all, sync_deletions=True)
    assert mirror.sync_deletions is True
    await runner.run_until_idle()
    assert (await container.services.get_feed(mirror.id)).episode_count == 2

    source.write_rss("synced", items=[2], artwork=False)
    job = await container.services.request_refresh(mirror.id)
    assert job is not None
    await runner.run_until_idle()
    async with uow(container) as unit:
        states = {i.source_number: i.archive_state for i in await unit.catalog.for_feed(mirror.id)}
    assert states == {1: ArchiveState.deleted, 2: ArchiveState.archived}
    read = await container.services.get_feed(mirror.id)
    assert read.episode_count == 1
    # Without sync the same Refresh only Delists.
    plain = await container.services.update_mirror(mirror.id, MirrorUpdate(sync_deletions=False))
    assert plain.sync_deletions is False


async def test_refresh_interval_is_the_default_unless_the_mirror_says(
    container: Container, source: Source, runner: Runner
) -> None:
    from datetime import UTC, datetime, timedelta

    from copycast.worker.scheduler import Scheduler

    url = source.write_rss("interval", items=[1], artwork=False)
    mirror = await create_mirror(container, url)
    await runner.run_until_idle()
    assert mirror.refresh_interval_hours is None
    assert (await container.services.get_mirror_defaults()).refresh_interval_hours == 24
    scheduler = Scheduler(container)
    now = datetime.now(UTC)
    async with uow(container) as unit:
        await unit.feeds.set_refresh_attempt(mirror.id, now - timedelta(hours=6))
    assert await scheduler._due_refreshes(now) == 0  # 6 h < the 24 h default

    hourly = await container.services.update_mirror(
        mirror.id, MirrorUpdate(refresh_interval_hours=4)
    )
    assert hourly.refresh_interval_hours == 4
    assert await scheduler._due_refreshes(now) == 1  # 6 h >= its own 4 h
    await runner.run_until_idle()
    async with uow(container) as unit:
        await unit.feeds.set_refresh_attempt(mirror.id, now - timedelta(hours=6))
    await container.services.update_mirror(mirror.id, MirrorUpdate(refresh_interval_hours=None))
    await container.services.set_mirror_defaults(MirrorDefaults(refresh_interval_hours=5))
    assert await scheduler._due_refreshes(now) == 1  # the default moved under 6 h
