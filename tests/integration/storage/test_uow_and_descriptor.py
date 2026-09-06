"""UnitOfWork hooks, NOTIFY, descriptor export (one per transaction, newer-version guard)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import psycopg
import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from copycast.adapters.db.locks import WORKER_LOCK, SessionLock, advisory_xact_lock
from copycast.adapters.db.uow import UnitOfWork, UnitOfWorkFactory, is_retryable, uow_of
from copycast.adapters.storage.descriptor import (
    DESCRIPTOR_FORMAT,
    DescriptorExporter,
    FeedDescriptor,
    build_descriptor,
    export_descriptor,
    read_intent_version,
)
from copycast.adapters.storage.layout import Layout
from copycast.application.events import CHANNEL, FeedEvent, parse
from copycast.domain.enums import ArchiveState, AssetKind, RequestedVia
from copycast.domain.exceptions import ConcurrentUpdate
from copycast.settings import Settings
from tests.integration.storage.conftest import asset_row, inbox_row, item_row, mirror_row, seed
from tests.support.factories import listing

pytestmark = pytest.mark.integration


class SpyExporter(DescriptorExporter):
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], layout: Layout) -> None:
        super().__init__(sessionmaker, layout)
        self.calls: list[str] = []

    async def export(self, feed_id: str) -> Path | None:
        self.calls.append(feed_id)
        return await super().export(feed_id)


@pytest.fixture
def spy(sessionmaker: async_sessionmaker[AsyncSession], layout: Layout) -> SpyExporter:
    return SpyExporter(sessionmaker, layout)


@pytest.fixture
def spied_factory(
    sessionmaker: async_sessionmaker[AsyncSession], layout: Layout, spy: SpyExporter
) -> UnitOfWorkFactory:
    return UnitOfWorkFactory(sessionmaker, layout, exporter=spy, debounce_seconds=0.05)


async def test_hooks_run_after_commit_only(uow_factory: UnitOfWorkFactory) -> None:
    calls: list[str] = []

    async def async_hook() -> None:
        calls.append("async")

    def failing_hook() -> None:
        raise RuntimeError("hook failures are logged, never raised")

    async with uow_factory() as uow:
        await uow.feeds.add(mirror_row())
        uow.after_commit(lambda: calls.append("sync"))
        uow.after_commit(async_hook)
        uow.after_commit(failing_hook)
        uow.after_commit(lambda: calls.append("after failing"))
        assert calls == []
    assert calls == ["sync", "async", "after failing"]

    calls.clear()
    with pytest.raises(RuntimeError, match="boom"):
        async with uow_factory() as uow:
            await uow.feeds.add(inbox_row())
            uow.after_commit(lambda: calls.append("never"))
            raise RuntimeError("boom")
    assert calls == []
    async with uow_factory() as uow:
        assert await uow.feeds.count() == 1  # the second transaction rolled back
        assert uow_of(uow) is uow
        with pytest.raises(TypeError):
            uow_of(object())
        await uow.flush()
        await uow.commit()
        await uow.commit()  # idempotent


async def test_update_intent_exports_once_per_transaction(
    spied_factory: UnitOfWorkFactory, spy: SpyExporter, layout: Layout
) -> None:
    async with spied_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        await uow.catalog.upsert_listing(feed.id, listing(2))
        await uow.update_intent(feed.id)
        await uow.update_intent(feed.id)
        versions = await uow.update_intent(feed.id)
        assert versions == (4, 4)
        assert spy.calls == []
    assert spy.calls == [feed.id]
    path = layout.descriptor_path(feed.id)
    assert path.is_file()
    assert read_intent_version(path) == 4
    descriptor = FeedDescriptor.read(path)
    assert descriptor.format == DESCRIPTOR_FORMAT
    assert descriptor.feed.id == feed.id and len(descriptor.items) == 2
    assert descriptor.policy.follow is True
    raw = json.loads(path.read_text())
    assert "download_count" not in json.dumps(raw)  # no telemetry in the descriptor


async def test_export_skips_when_disk_carries_newer_intent(
    spied_factory: UnitOfWorkFactory, layout: Layout, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    async with spied_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        await uow.update_intent(feed.id)
    path = layout.descriptor_path(feed.id)
    newer = json.loads(path.read_text())
    newer["intent_version"] = 999
    newer["feed"]["title"] = "from a newer process"
    path.write_text(json.dumps(newer))
    async with spied_factory() as uow:
        await uow.update_intent(feed.id)
    assert json.loads(path.read_text())["feed"]["title"] == "from a newer process"
    async with sessionmaker() as session:
        assert await export_descriptor(session, layout, feed.id) is None
        written = await export_descriptor(session, layout, feed.id, force=True)
        assert written == path
        assert await export_descriptor(session, layout, "nope") is None
        assert await build_descriptor(session, "nope") is None
    assert json.loads(path.read_text())["feed"]["title"] == "Test Podcast"


async def _wait_for_file(path: Path) -> None:
    """Poll (up to 5 s) until ``path`` exists: the debounced export runs a DB read and
    an atomic write after its delay, which takes longer than the delay under coverage."""
    for _ in range(250):
        if await asyncio.to_thread(path.is_file):
            return
        await asyncio.sleep(0.02)


async def test_debounced_export_coalesces_and_flushes(
    spied_factory: UnitOfWorkFactory, layout: Layout
) -> None:
    async with spied_factory() as uow:
        feed = await uow.feeds.add(mirror_row())
        await uow.update_intent(feed.id, debounce=True)
    path = layout.descriptor_path(feed.id)
    assert not path.exists()
    assert spied_factory.debounced_exporter.pending == {feed.id}
    await _wait_for_file(path)  # the debounce fires on its own, without flush()
    assert path.is_file()
    assert spied_factory.debounced_exporter.pending == set()

    path.unlink()
    async with spied_factory() as uow:
        await uow.bump_revision(feed.id)
        uow.export_after_commit(feed.id, debounce=True)
        uow.export_after_commit(feed.id, debounce=True)
    assert not path.exists()
    await spied_factory.flush_exports()
    assert path.is_file()

    path.unlink()
    async with spied_factory() as uow:
        uow.export_after_commit(feed.id, debounce=True)
        uow.export_after_commit(feed.id)  # immediate wins over debounced
    assert path.is_file()
    await spied_factory.aclose()


async def test_publish_delivers_notify_on_commit(
    uow_factory: UnitOfWorkFactory, settings: Settings
) -> None:
    dsn = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as listener:
        await listener.execute(f"LISTEN {CHANNEL}")
        async with uow_factory() as uow:
            feed = await uow.feeds.add(mirror_row())
            await uow.publish(FeedEvent(feed_id=feed.id, revision=1, reason="test"))
            await uow.notify_jobs()
        received = [n async for n in listener.notifies(timeout=5, stop_after=1)]
    assert len(received) == 1
    event = parse(received[0].payload)
    assert isinstance(event, FeedEvent) and event.feed_id == feed.id


async def test_descriptor_carries_intent_not_telemetry(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        inbox = await uow.feeds.add(inbox_row(autoprune_days=7))
        item = item_row(inbox, 1, state=ArchiveState.archived, download_count=5)
        await seed(uow, item, asset_row(inbox, item, AssetKind.artwork))
        request = await uow.requests.add(inbox.id, "https://x.example/v", RequestedVia.mcp)
        await uow.requests.link_items(request.id, [item.id])
        descriptor = await build_descriptor(uow.session, inbox.id)
    assert descriptor is not None
    assert descriptor.policy.autoprune_days == 7
    assert descriptor.items[0].archive_state is ArchiveState.archived
    assert descriptor.items[0].media_path == item.media_path
    assert not hasattr(descriptor.items[0], "download_count")
    assert descriptor.assets[0].item_id == item.id
    assert descriptor.requests[0].item_ids == [item.id]
    assert descriptor.requests[0].requested_via is RequestedVia.mcp
    round_trip = FeedDescriptor.model_validate_json(descriptor.to_json())
    assert round_trip == descriptor


async def test_session_lock_and_xact_lock(uow_factory: UnitOfWorkFactory) -> None:
    engine = uow_factory.sessionmaker.kw["bind"]
    async with SessionLock(engine, WORKER_LOCK) as first:
        assert first.held is True
        assert await first.acquire() is True  # re-entrant no-op
        async with SessionLock(engine, WORKER_LOCK) as second:
            assert second.held is False
            with pytest.raises(RuntimeError):
                _ = SessionLock(engine, WORKER_LOCK).connection
        assert first.connection is not None
    async with SessionLock(engine, WORKER_LOCK) as third:
        assert third.held is True
    async with SessionLock(engine, WORKER_LOCK, wait=True) as waited:
        assert waited.held is True
    async with engine.connect() as conn:
        await advisory_xact_lock(conn, WORKER_LOCK)
        async with SessionLock(engine, WORKER_LOCK) as blocked:
            assert blocked.held is False
        await conn.rollback()
    async with SessionLock(engine, WORKER_LOCK) as free:
        assert free.held is True


async def test_uow_exposes_repositories(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        assert isinstance(uow, UnitOfWork)
        for name in ("feeds", "catalog", "assets", "requests", "jobs", "telemetry"):
            assert getattr(uow, name) is not None
        assert uow.layout is uow_factory.layout


async def test_deadlock_surfaces_as_concurrent_update(uow_factory: UnitOfWorkFactory) -> None:
    """Two transactions locking two feeds in opposite orders: Postgres aborts one with
    40P01, which the unit of work rolls back and raises as ``ConcurrentUpdate``."""
    async with uow_factory() as uow:
        await seed(uow, inbox_row("One"), inbox_row("Two"))
    first_locked = asyncio.Event()
    second_locked = asyncio.Event()

    async def lock_in_order(
        first: str, second: str, mine: asyncio.Event, other: asyncio.Event
    ) -> None:
        async with uow_factory() as uow:
            await uow.feeds.get_for_update(first)
            mine.set()
            await asyncio.wait_for(other.wait(), timeout=10)
            await uow.feeds.get_for_update(second)

    results = await asyncio.gather(
        lock_in_order("one-abc234", "two-abc234", first_locked, second_locked),
        lock_in_order("two-abc234", "one-abc234", second_locked, first_locked),
        return_exceptions=True,
    )
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(failures) == 1, results
    assert isinstance(failures[0], ConcurrentUpdate)
    assert isinstance(failures[0].__cause__, DBAPIError)
    assert failures[0].status == 409 and failures[0].slug == "conflict"
    # The session was rolled back and closed: the factory still hands out working units.
    async with uow_factory() as uow:
        assert await uow.feeds.get("one-abc234") is not None


def test_is_retryable_reads_the_sqlstate() -> None:
    deadlock = DBAPIError("DELETE", {}, psycopg.errors.DeadlockDetected("boom"))
    serialization = DBAPIError("UPDATE", {}, psycopg.errors.SerializationFailure("boom"))
    unique = DBAPIError("INSERT", {}, psycopg.errors.UniqueViolation("dup"))
    assert is_retryable(deadlock) and is_retryable(serialization)
    assert not is_retryable(unique)
    assert not is_retryable(RuntimeError("no sqlstate"))
    assert is_retryable(psycopg.errors.DeadlockDetected("raw driver error"))
