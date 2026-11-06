"""The archive job and quotas: an Over quota Owner's items are blocked, never fetched.

Blocked items stay ``wanted`` with ``blocked_reason = quota``: when the Owner is already
over, nothing is fetched and ``blocked_bytes`` stays null; when the Engine's estimate
does not fit the quota left (``QuotaExceeded``), the estimate is kept. Either way no
attempt is counted, the job succeeds with ``{"blocked": "quota"}``, an ``ItemEvent``
says the item is wanted again, and a Refresh does not queue it again.

The quota is set on the container's ``UserDirectory`` (the open-mode Admin ``copycast``
owns everything here); usage is a filler Local feed's ``storage_bytes``.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from copycast.adapters.db.uow import UnitOfWork
from copycast.app import Container
from copycast.application.events import ItemEvent
from copycast.application.models import BucketCreate, RequestCreate
from copycast.domain.enums import ArchiveState, JobKind, JobStatus, JobTrigger
from copycast.worker.runner import Runner
from tests.integration.worker.conftest import create_mirror, jobs_of, uow
from tests.support.factories import listing
from tests.support.fake_engine import FakeEngine

pytestmark = pytest.mark.integration

PLAYLIST = "https://www.youtube.com/playlist?list=PLquota"
CHANNEL = "https://www.youtube.com/@quota/videos"


def set_quota(container: Container, quota: int | None) -> None:
    """Every User's quota is ``quota`` bytes (None: unlimited) from now on."""
    container.users.quota_bytes = lambda username: quota  # type: ignore[method-assign]


async def use_bytes(container: Container, used: int) -> str:
    """Make the Owner's usage ``used`` bytes through a filler Local feed; returns its id."""
    filler = await container.services.create_bucket(BucketCreate(name="Filler"))
    async with uow(container) as unit:
        feed = await unit.feeds.require(filler.id)
        feed.storage_bytes = used
    return filler.id


class Published:
    """Every event published through a unit of work, in order."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.events: list[BaseModel] = []
        original = UnitOfWork.publish

        async def spy(unit: UnitOfWork, event: BaseModel, **kwargs: Any) -> None:
            self.events.append(event)
            await original(unit, event, **kwargs)

        monkeypatch.setattr(UnitOfWork, "publish", spy)

    def items(self, state: ArchiveState) -> list[str]:
        return [e.item_id for e in self.events if isinstance(e, ItemEvent) and e.state is state]


async def request_three(container: Container, engine: FakeEngine) -> str:
    """Push a three-video playlist into the default Local feed; returns its id."""
    bucket = await container.services.ensure_default_bucket()
    engine.script_listing(PLAYLIST, listing(3, service="YouTube", title="A playlist"))
    await container.services.add_request(bucket.id, RequestCreate(url=PLAYLIST))
    return bucket.id


async def test_an_over_quota_owner_gets_items_blocked_without_a_fetch(
    container: Container, runner: Runner, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_quota(container, 1000)
    await use_bytes(container, 1000)  # exactly the quota: over
    published = Published(monkeypatch)
    bucket = await request_three(container, engine)

    await runner.run_until_idle()

    assert engine.records.fetches == [], "nothing is downloaded for an Over quota Owner"
    async with uow(container) as unit:
        rows = await unit.catalog.for_feed(bucket)
    assert len(rows) == 3
    for row in rows:
        assert row.archive_state == ArchiveState.wanted
        assert (row.blocked_reason, row.blocked_bytes) == ("quota", None)
        assert row.attempt_count == 0 and row.last_error is None
    archives = await jobs_of(container, bucket, kind=JobKind.archive_item)
    assert len(archives) == 3
    assert {job.status for job in archives} == {JobStatus.succeeded.value}
    assert all(job.result == {"blocked": "quota"} for job in archives)
    assert sorted(published.items(ArchiveState.wanted)) == sorted(row.id for row in rows)

    page = await container.services.list_items(bucket)
    assert {item.blocked_reason for item in page.items} == {"quota"}


async def test_an_estimate_above_the_quota_left_blocks_with_the_estimate(
    container: Container, runner: Runner, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_quota(container, 10_000_000)
    await use_bytes(container, 4_000_000)
    engine.estimated_bytes = 7_000_000
    published = Published(monkeypatch)
    bucket = await request_three(container, engine)

    await runner.run_until_idle()

    assert len(engine.records.fetches) == 3
    assert {f.spec.max_bytes for f in engine.records.fetches} == {6_000_000}
    async with uow(container) as unit:
        rows = await unit.catalog.for_feed(bucket)
    for row in rows:
        assert row.archive_state == ArchiveState.wanted
        assert (row.blocked_reason, row.blocked_bytes) == ("quota", 7_000_000)
        assert row.attempt_count == 0
    archives = await jobs_of(container, bucket, kind=JobKind.archive_item)
    assert {job.status for job in archives} == {JobStatus.succeeded.value}
    assert all(job.result == {"blocked": "quota", "estimate_bytes": 7_000_000} for job in archives)
    assert all(job.attempt == 1 for job in archives), "never retried"
    # archiving, then wanted again, for each item
    assert sorted(published.items(ArchiveState.wanted)) == sorted(row.id for row in rows)


async def test_a_fetch_carries_the_quota_left_and_an_unlimited_owner_none(
    container: Container, runner: Runner, engine: FakeEngine
) -> None:
    engine.estimated_bytes = 50_000_000
    bucket = await request_three(container, engine)
    await runner.run_until_idle()
    assert {f.spec.max_bytes for f in engine.records.fetches} == {None}
    async with uow(container) as unit:
        rows = await unit.catalog.for_feed(bucket)
    assert {row.archive_state for row in rows} == {ArchiveState.archived}
    assert {row.blocked_reason for row in rows} == {None}

    # With a quota, the estimate fits: archived, and the fetch saw what was left.
    set_quota(container, 10**12)
    engine.records.fetches.clear()
    engine.script_listing(CHANNEL, listing(1, service="YouTube", title="Fits"))
    mirror = await create_mirror(container, CHANNEL)
    await runner.run_until_idle()
    used = (await container.services.get_feed(bucket)).storage_bytes
    (fetch,) = engine.records.fetches
    assert fetch.spec.max_bytes == 10**12 - used
    page = await container.services.list_items(mirror.id)
    assert [item.state for item in page.items] == [ArchiveState.archived]


async def test_a_refresh_does_not_queue_blocked_items_again(
    container: Container, runner: Runner, engine: FakeEngine
) -> None:
    set_quota(container, 1000)
    await use_bytes(container, 5000)
    engine.script_listing(CHANNEL, listing(2, service="YouTube", title="Blocked"))
    mirror = await create_mirror(container, CHANNEL)
    await runner.run_until_idle()
    archives = await jobs_of(container, mirror.id, kind=JobKind.archive_item)
    assert len(archives) == 2 and all(j.result == {"blocked": "quota"} for j in archives)
    read: Any = await container.services.get_feed(mirror.id)
    assert (read.counts.blocked, read.counts.wanted) == (2, 2)

    await container.services.request_refresh(mirror.id, JobTrigger.manual)
    await runner.run_until_idle()

    refreshes = await jobs_of(container, mirror.id, kind=JobKind.refresh)
    assert {job.status for job in refreshes} == {JobStatus.succeeded.value}
    assert len(await jobs_of(container, mirror.id, kind=JobKind.archive_item)) == 2
    assert engine.records.fetches == []


async def test_a_paused_mirror_is_cancelled_before_the_quota_check(
    container: Container, runner: Runner, engine: FakeEngine
) -> None:
    set_quota(container, 1000)
    await use_bytes(container, 5000)
    engine.script_listing(CHANNEL, listing(1, service="YouTube", title="Paused"))
    mirror = await create_mirror(container, CHANNEL)
    await container.services.set_paused(mirror.id, True)
    await runner.run_until_idle()
    async with uow(container) as unit:
        rows = await unit.catalog.for_feed(mirror.id)
    assert all(row.blocked_reason is None for row in rows)
