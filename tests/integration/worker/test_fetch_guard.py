"""The fetch guard in the worker: a refused destination fails the run once, never retried."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from copycast.adapters.sources.guard import install_policy
from copycast.adapters.sources.http import close_client
from copycast.app import Container
from copycast.application.models import RequestCreate
from copycast.domain.destinations import DestinationPolicy
from copycast.domain.enums import (
    ArchiveState,
    BackfillMode,
    ErrorKind,
    JobKind,
    JobStatus,
    RequestedVia,
    RequestStatus,
)
from copycast.domain.exceptions import DestinationNotAllowed
from copycast.worker.runner import Runner
from tests.integration.worker.conftest import Source, create_mirror, jobs_of, uow
from tests.support.fake_engine import FakeEngine

pytestmark = pytest.mark.integration


Revoke = Callable[[], None]


@pytest.fixture
def revoke_exemptions() -> Iterator[Revoke]:
    """Call to drop the suite's loopback exemption mid-test, as a restart with a new config would.

    The shared client is closed too: its pooled keep-alive connections were checked
    under the old policy.
    """
    previous: list[DestinationPolicy] = []

    def revoke() -> None:
        previous.append(install_policy(DestinationPolicy()))
        close_client()

    yield revoke
    for policy in previous[:1]:
        install_policy(policy)


async def test_a_refused_source_fails_the_refresh_once_and_keeps_the_mirror(
    container: Container, source: Source, runner: Runner, revoke_exemptions: Revoke
) -> None:
    url = source.write_rss("guarded", items=[1], artwork=False)
    mirror = await create_mirror(container, url, mode=BackfillMode.selection)
    await runner.run_until_idle()
    revoke_exemptions()
    before = source.origin.hits("/rss/guarded.xml")

    job = await container.services.request_refresh(mirror.id)
    assert job is not None
    await runner.run_until_idle()

    async with uow(container) as unit:
        row = await unit.jobs.require(job.id)
        assert row.status == JobStatus.failed.value, row.error
        assert row.error_kind == ErrorKind.permanent.value and row.attempt == 1
        assert row.error and "not an allowed destination" in row.error and "non-public" in row.error
        feed = await unit.feeds.require(mirror.id)
        assert feed.last_error == row.error
        assert len(await unit.catalog.for_feed(mirror.id)) == 1, "the Catalog is untouched"
    assert source.origin.hits("/rss/guarded.xml") == before, "nothing reached the Source"
    refreshes = await jobs_of(container, mirror.id, kind=JobKind.refresh)
    assert sum(1 for j in refreshes if j.status == JobStatus.queued.value) == 0, "no retry queued"


async def test_a_refused_media_url_fails_the_item_permanently(
    container: Container, source: Source, runner: Runner, engine: FakeEngine
) -> None:
    url = source.write_rss("refused-media", items=[1], artwork=False)
    mirror = await create_mirror(container, url, mode=BackfillMode.selection)
    async with uow(container) as unit:
        item = (await unit.catalog.for_feed(mirror.id))[0]
    job = await container.services.archive_item(mirror.id, item.id)
    engine.fail_next(DestinationNotAllowed("10.0.0.7:80", "it resolves to a private address"))
    await runner.run_until_idle()
    async with uow(container) as unit:
        row = await unit.jobs.require(job.id)
        assert row.status == JobStatus.failed.value
        assert row.error_kind == ErrorKind.permanent.value and row.attempt == 1
        failed = await unit.catalog.require(item.id)
        assert failed.archive_state == ArchiveState.failed
        assert failed.last_error == "10.0.0.7:80 is not an allowed destination: " + (
            "it resolves to a private address"
        )


async def test_a_refused_request_fails_with_the_reason(
    container: Container,
    source: Source,
    default_bucket: str,
    runner: Runner,
    engine: FakeEngine,
    revoke_exemptions: Revoke,
) -> None:
    revoke_exemptions()
    request = await container.services.add_request(
        "Copycast", RequestCreate(url=source.url_for("/media/tiny.mp3")), via=RequestedVia.mcp
    )
    await runner.run_until_idle()
    detail = await container.services.get_request(default_bucket, request.id)
    assert detail.status is RequestStatus.failed
    assert detail.error and "not an allowed destination" in detail.error
    assert detail.job is not None and detail.job.status is JobStatus.failed
    assert source.origin.requests == [] and engine.records.listings == []


async def test_a_refused_next_page_leaves_the_first_full_refresh_with_page_one(
    container: Container, source: Source, runner: Runner
) -> None:
    """The first full Refresh follows rel="next"; a refused page must not fail it, or every
    later Refresh would follow it again and fail the same way."""
    url = source.write_rss("paged-refused", items=[2, 1], artwork=False)
    path = source.root / "rss" / "paged-refused.xml"
    next_link = '<atom:link href="http://10.1.2.3/page2.xml" rel="next"/>'
    path.write_text(
        path.read_text(encoding="utf-8").replace("<itunes:author>", f"{next_link}<itunes:author>"),
        encoding="utf-8",
    )
    mirror = await create_mirror(container, url, mode=BackfillMode.selection)
    await runner.run_until_idle()
    job = await container.services.request_refresh(mirror.id)
    assert job is not None
    await runner.run_until_idle()

    async with uow(container) as unit:
        row = await unit.jobs.require(job.id)
        assert row.status == JobStatus.succeeded.value, row.error
        feed = await unit.feeds.require(mirror.id)
        assert feed.last_error is None
        assert feed.last_refresh_success_at is not None
        assert len(await unit.catalog.for_feed(mirror.id)) == 2
