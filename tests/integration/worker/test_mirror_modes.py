"""Mirror modes: a Rolling window, switching into one, Ephemeral (on demand, expiring),
Archive without backfill, and the Lock (``archive_flag``) that every deleting mode spares and
the Archive policy sets."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from copycast.app import Container
from copycast.application.models import (
    ArchiveFlagUpdate,
    ClearFeedRequest,
    MirrorUpdate,
    PurgeRequest,
)
from copycast.application.services import policy
from copycast.domain.enums import ArchiveState, BackfillMode, WantedReason
from copycast.domain.exceptions import Conflict
from copycast.worker.runner import Runner
from tests.integration.worker.conftest import Source, create_mirror, uow

pytestmark = pytest.mark.integration


async def _states(container: Container, feed_id: str) -> dict[int | None, ArchiveState]:
    async with uow(container) as unit:
        return {i.source_number: i.archive_state for i in await unit.catalog.for_feed(feed_id)}


async def test_rolling_keeps_the_newest_n_and_tombstones_the_rest(
    container: Container, source: Source, runner: Runner
) -> None:
    url = source.write_rss("rolling", items=[3, 2, 1], artwork=False)
    mirror = await create_mirror(container, url, mode=BackfillMode.rolling, latest_n=2)
    assert mirror.backfill.mode is BackfillMode.rolling and mirror.backfill.latest_n == 2
    await runner.run_until_idle()
    assert await _states(container, mirror.id) == {
        1: ArchiveState.available,
        2: ArchiveState.archived,
        3: ArchiveState.archived,
    }

    # A newer item rolls the oldest archived one out: tombstoned, media gone, re-archivable.
    source.write_rss("rolling", items=[4, 3, 2, 1], artwork=False)
    job = await container.services.request_refresh(mirror.id)
    assert job is not None
    await runner.run_until_idle()
    assert await _states(container, mirror.id) == {
        1: ArchiveState.available,
        2: ArchiveState.deleted,
        3: ArchiveState.archived,
        4: ArchiveState.archived,
    }
    async with uow(container) as unit:
        rows = {i.source_number: i for i in await unit.catalog.for_feed(mirror.id)}
        rolled = rows[2]
        assert rolled.media_path is None and rolled.deleted_at is not None
        assert rows[4].wanted_reason == WantedReason.follow
    assert container.layout.find_media(mirror.id, rolled.id) is None
    # On purpose it comes back (and would roll out again at the next Refresh).
    await container.services.archive_item(mirror.id, rolled.id)
    await runner.run_until_idle()
    assert (await _states(container, mirror.id))[2] == ArchiveState.archived


async def test_switching_to_rolling_previews_and_prunes_at_once(
    container: Container, source: Source, runner: Runner
) -> None:
    url = source.write_rss("switch", items=[3, 2, 1], artwork=False)
    # Ephemeral, everything archived by hand: nothing is locked (the Archive policy would).
    mirror = await create_mirror(container, url, mode=BackfillMode.automatic)
    await runner.run_until_idle()
    await container.services.archive_available(mirror.id)
    await runner.run_until_idle()
    assert set((await _states(container, mirror.id)).values()) == {ArchiveState.archived}

    change = MirrorUpdate(backfill={"mode": BackfillMode.rolling, "latest_n": 1})
    preview = await container.services.preview_mirror_update(mirror.id, change)
    assert preview.would_delete_count == 2 and preview.would_delete_bytes > 0
    assert preview.would_archive_count == 0  # the newest is archived already
    # Nothing changed by previewing.
    assert set((await _states(container, mirror.id)).values()) == {ArchiveState.archived}
    # Growing the window only archives; nothing to delete.
    wider = await container.services.preview_mirror_update(
        mirror.id, MirrorUpdate(backfill={"mode": BackfillMode.all})
    )
    assert wider.would_delete_count == 0 and wider.would_archive_count == 0

    updated = await container.services.update_mirror(mirror.id, change)
    assert updated.backfill.mode is BackfillMode.rolling and updated.backfill.latest_n == 1
    assert await _states(container, mirror.id) == {
        1: ArchiveState.deleted,
        2: ArchiveState.deleted,
        3: ArchiveState.archived,
    }
    assert updated.counts.archived == 1


async def test_automatic_archives_nothing_and_expires_idle_episodes(
    container: Container, source: Source, runner: Runner
) -> None:
    url = source.write_rss("automatic", items=[2, 1], artwork=False)
    mirror = await create_mirror(container, url, mode=BackfillMode.automatic)
    assert mirror.backfill.mode is BackfillMode.automatic
    assert mirror.backfill.retention_days == 7  # the default Retention
    await runner.run_until_idle()
    assert set((await _states(container, mirror.id)).values()) == {ArchiveState.available}

    # A client fetching the media archives it (the route does this); listening keeps it.
    async with uow(container) as unit:
        rows = {i.source_number: i for i in await unit.catalog.for_feed(mirror.id)}
    await container.services.archive_item(mirror.id, rows[1].id)
    await runner.run_until_idle()
    assert (await _states(container, mirror.id))[1] == ArchiveState.archived

    ctx = container.services.ctx
    soon = datetime.now(UTC) + timedelta(days=6)
    assert (await policy.expire_automatic(ctx, mirror.id, now=soon)).deleted_count == 0
    later = datetime.now(UTC) + timedelta(days=8)
    expired = await policy.expire_automatic(ctx, mirror.id, now=later)
    assert expired.deleted_count == 1
    async with uow(container) as unit:
        row = await unit.catalog.require(rows[1].id, mirror.id)
        assert row.archive_state == ArchiveState.deleted
        feed = await unit.feeds.require(mirror.id)
        assert feed.last_autoprune_at == later
        # The scheduler's pass picks Automatic Mirrors up like Buckets.
        assert [f.id for f in await unit.feeds.mirrors_due_expiry(later + timedelta(days=1))] == [
            mirror.id
        ]
    # A Refresh never re-archives an expired Episode on its own.
    job = await container.services.request_refresh(mirror.id)
    assert job is not None
    await runner.run_until_idle()
    assert (await _states(container, mirror.id))[1] == ArchiveState.deleted

    # Retention can be set per Mirror; None keeps forever.
    kept = await container.services.update_mirror(
        mirror.id,
        MirrorUpdate(backfill={"mode": BackfillMode.automatic, "retention_days": None}),
    )
    assert kept.backfill.retention_days is None
    async with uow(container) as unit:
        assert await unit.feeds.mirrors_due_expiry(later + timedelta(days=30)) == []


async def test_archive_without_backfill_archives_only_what_comes_next(
    container: Container, source: Source, runner: Runner
) -> None:
    url = source.write_rss("new-only", items=[2, 1], artwork=False)
    mirror = await create_mirror(container, url, mode=BackfillMode.new)
    assert mirror.backfill.mode is BackfillMode.new and mirror.follow is True
    await runner.run_until_idle()
    assert await _states(container, mirror.id) == {
        1: ArchiveState.available,
        2: ArchiveState.available,
    }

    source.write_rss("new-only", items=[3, 2, 1], artwork=False)
    assert await container.services.request_refresh(mirror.id) is not None
    await runner.run_until_idle()
    assert (await _states(container, mirror.id))[3] == ArchiveState.archived
    assert (await _states(container, mirror.id))[2] == ArchiveState.available

    # Switching an Ephemeral Mirror to it never archives the backlog; saving it again keeps
    # the start, so an item first seen in between is not dropped.
    url = source.write_rss("switch-new", items=[2, 1], artwork=False)
    ephemeral = await create_mirror(container, url, mode=BackfillMode.automatic)
    await runner.run_until_idle()
    switched = await container.services.update_mirror(
        ephemeral.id, MirrorUpdate(backfill={"mode": BackfillMode.new})
    )
    async with uow(container) as unit:
        started = (await unit.feeds.require(ephemeral.id)).policy_applied_at
    await container.services.update_mirror(
        ephemeral.id, MirrorUpdate(backfill={"mode": BackfillMode.new})
    )
    async with uow(container) as unit:
        assert (await unit.feeds.require(ephemeral.id)).policy_applied_at == started
    assert switched.backfill.mode is BackfillMode.new
    assert await container.services.request_refresh(ephemeral.id) is not None
    await runner.run_until_idle()
    assert set((await _states(container, ephemeral.id)).values()) == {ArchiveState.available}


async def test_the_archive_flag_survives_retention_rolling_and_clearing(
    container: Container, source: Source, runner: Runner
) -> None:
    url = source.write_rss("flagged", items=[3, 2, 1], artwork=False)
    mirror = await create_mirror(container, url, mode=BackfillMode.automatic)
    await runner.run_until_idle()
    async with uow(container) as unit:
        rows = {i.source_number: i for i in await unit.catalog.for_feed(mirror.id)}
    for number in (1, 2, 3):
        await container.services.archive_item(mirror.id, rows[number].id)
    await runner.run_until_idle()
    flagged = await container.services.set_archive_flag(
        mirror.id, rows[1].id, ArchiveFlagUpdate(archive_flag=True)
    )
    assert flagged.archive_flag is True

    # Ephemeral Retention spares the flagged Episode.
    later = datetime.now(UTC) + timedelta(days=8)
    expired = await policy.expire_automatic(container.services.ctx, mirror.id, now=later)
    assert expired.deleted_count == 2
    assert (await _states(container, mirror.id))[1] == ArchiveState.archived

    # So does a Rolling window that leaves it out.
    for number in (2, 3):
        await container.services.archive_item(mirror.id, rows[number].id)
    await runner.run_until_idle()
    await container.services.update_mirror(
        mirror.id, MirrorUpdate(backfill={"mode": BackfillMode.rolling, "latest_n": 1})
    )
    assert await _states(container, mirror.id) == {
        1: ArchiveState.archived,
        2: ArchiveState.deleted,
        3: ArchiveState.archived,
    }

    # Clearing counts first, then deletes only what is not flagged.
    preview = await container.services.clear_feed(mirror.id, ClearFeedRequest())
    assert (preview.dry_run, preview.matched, preview.kept_count) == (True, 1, 1)
    assert preview.kept_bytes > 0 and preview.bytes_freed > 0
    assert (await _states(container, mirror.id))[3] == ArchiveState.archived
    cleared = await container.services.clear_feed(mirror.id, ClearFeedRequest(dry_run=False))
    assert (cleared.deleted_count, cleared.kept_count) == (1, 1)
    assert await _states(container, mirror.id) == {
        1: ArchiveState.archived,
        2: ArchiveState.deleted,
        3: ArchiveState.deleted,
    }

    # On an Archive Mirror only the Lock protects: unlocked by hand, clearing deletes it.
    await container.services.update_mirror(
        mirror.id, MirrorUpdate(backfill={"mode": BackfillMode.new})
    )
    await container.services.set_archive_flag(
        mirror.id, rows[1].id, ArchiveFlagUpdate(archive_flag=False)
    )
    cleared = await container.services.clear_feed(mirror.id, ClearFeedRequest(dry_run=False))
    assert (cleared.deleted_count, cleared.kept_count) == (1, 0)
    assert (await _states(container, mirror.id))[1] == ArchiveState.deleted


async def test_the_archive_policy_locks_what_it_archives(
    container: Container, source: Source, runner: Runner
) -> None:
    """Archived under the Archive policy: locked (feed.json too), spared by Sync, by a purge
    and by delete_item; an Ephemeral Mirror locks nothing. (A Mirror created with Sync is
    never locked automatically; Sync is switched on here after the archives are locked.)"""
    url = source.write_rss("auto-lock", items=[2, 1], artwork=False)
    mirror = await create_mirror(container, url, mode=BackfillMode.all)
    await runner.run_until_idle()
    async with uow(container) as unit:
        rows = {i.source_number: i for i in await unit.catalog.for_feed(mirror.id)}
    assert {n: (r.archive_state, r.archive_flag) for n, r in rows.items()} == {
        1: (ArchiveState.archived, True),
        2: (ArchiveState.archived, True),
    }
    await container.uow_factory.flush_exports()
    descriptor = container.layout.read_json(container.layout.descriptor_key(mirror.id))
    assert descriptor is not None
    assert {item["archive_flag"] for item in descriptor["items"]} == {True}

    # Sync spares a locked Episode the Source dropped: it is only Delisted.
    await container.services.update_mirror(mirror.id, MirrorUpdate(sync_deletions=True))
    source.write_rss("auto-lock", items=[2], artwork=False)
    assert await container.services.request_refresh(mirror.id) is not None
    await runner.run_until_idle()
    assert await _states(container, mirror.id) == {
        1: ArchiveState.archived,
        2: ArchiveState.archived,
    }

    # The operator's purge spares the locked; delete_item refuses them.
    preview = await container.services.purge_episodes(PurgeRequest(dry_run=True))
    assert preview.matched == 0
    purged = await container.services.purge_episodes(PurgeRequest(dry_run=False))
    assert purged.deleted_count == 0
    with pytest.raises(Conflict, match="locked"):
        await container.services.delete_item(mirror.id, rows[1].id)
    await container.services.set_archive_flag(
        mirror.id, rows[1].id, ArchiveFlagUpdate(archive_flag=False)
    )
    purged = await container.services.purge_episodes(PurgeRequest(dry_run=False))
    assert purged.deleted_count == 1
    assert (await _states(container, mirror.id))[1] == ArchiveState.deleted

    url = source.write_rss("no-lock", items=[1], artwork=False)
    ephemeral = await create_mirror(container, url, mode=BackfillMode.automatic)
    await runner.run_until_idle()
    async with uow(container) as unit:
        (item,) = await unit.catalog.for_feed(ephemeral.id)
    await container.services.archive_item(ephemeral.id, item.id)
    await runner.run_until_idle()
    async with uow(container) as unit:
        item = await unit.catalog.require(item.id)
    assert (item.archive_state, item.archive_flag) == (ArchiveState.archived, False)
