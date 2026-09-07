"""Mirror modes: a Rolling window, switching into one, and Automatic (on demand, expiring)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from copycast.app import Container
from copycast.application.models import MirrorUpdate
from copycast.application.services import policy
from copycast.domain.enums import ArchiveState, BackfillMode, WantedReason
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
    assert not list(container.layout.media_dir(mirror.id).glob(f"{rolled.id}.m4a"))
    # On purpose it comes back (and would roll out again at the next Refresh).
    await container.services.archive_item(mirror.id, rolled.id)
    await runner.run_until_idle()
    assert (await _states(container, mirror.id))[2] == ArchiveState.archived


async def test_switching_to_rolling_previews_and_prunes_at_once(
    container: Container, source: Source, runner: Runner
) -> None:
    url = source.write_rss("switch", items=[3, 2, 1], artwork=False)
    mirror = await create_mirror(container, url)
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
        # The scheduler's pass picks Automatic Mirrors up like Inboxes.
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
