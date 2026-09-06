"""Inboxes: Requests expanded by the worker, pruning on demand and by Retention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from copycast.app import Container
from copycast.application.models import InboxCreate, InboxUpdate, PruneRequest, RequestCreate
from copycast.domain.enums import (
    ArchiveState,
    JobKind,
    JobStatus,
    RequestedVia,
    RequestStatus,
    WantedReason,
)
from copycast.domain.exceptions import Conflict, NotFound
from copycast.worker.runner import Runner
from tests.integration.worker.conftest import Source, jobs_of, uow
from tests.support.factories import listing, listing_item
from tests.support.fake_engine import FakeEngine

pytestmark = pytest.mark.integration


async def test_default_inbox_is_idempotent_and_never_deleted(container: Container) -> None:
    first = await container.services.ensure_default_inbox()
    second = await container.services.ensure_default_inbox()
    assert first.id == second.id and first.name == "Copycast"
    assert container.layout.descriptor_path(first.id).is_file()
    by_name = await container.services.resolve_inbox("copycast")
    assert by_name.id == first.id
    with pytest.raises(Conflict):
        await container.services.delete_feed(first.id)
    with pytest.raises(NotFound):
        await container.services.resolve_inbox("nope")
    renamed = await container.services.update_inbox(first.id, InboxUpdate(name="Later"))
    assert renamed.name == "Later"
    assert (await container.services.resolve_inbox("later")).id == first.id


async def test_request_expands_into_wanted_items_and_archives(
    container: Container, default_inbox: str, runner: Runner, engine: FakeEngine
) -> None:
    url = "https://www.youtube.com/playlist?list=PLtest"
    engine.script_listing(url, listing(3, service="YouTube", title="A playlist"))
    request = await container.services.add_request(
        "Copycast", RequestCreate(url=url), via=RequestedVia.mcp
    )
    assert request.status is RequestStatus.queued and request.requested_via is RequestedVia.mcp
    assert request.job is not None and request.job.kind is JobKind.expand_request

    await runner.run_until_idle()

    detail = await container.services.get_request(default_inbox, request.id)
    assert detail.status is RequestStatus.expanded and detail.item_count == 3
    assert {item.state for item in detail.items} == {ArchiveState.archived}
    assert all(request.id in item.request_ids for item in detail.items)
    page = await container.services.list_requests(default_inbox)
    assert page.total == 1
    async with uow(container) as unit:
        rows = await unit.catalog.for_feed(default_inbox)
        assert {row.wanted_reason for row in rows} == {WantedReason.request}
        assert all(row.listed for row in rows)
    archives = await jobs_of(container, default_inbox, kind=JobKind.archive_item)
    assert {job.priority for job in archives} == {50}
    assert {job.request_id for job in archives} == {request.id}

    # A second Request must not hide the first Request's items.
    other = "https://www.youtube.com/watch?v=single"
    single = listing(0, service="YouTube", title="Single").model_copy(
        update={"items": [listing_item(9, key="youtube:single")]}
    )
    engine.script_listing(other, single)
    second = await container.services.add_request(default_inbox, RequestCreate(url=other))
    await runner.run_until_idle()
    async with uow(container) as unit:
        rows = await unit.catalog.for_feed(default_inbox)
        assert len(rows) == 4 and all(row.listed for row in rows)
    inbox = await container.services.resolve_inbox(default_inbox)
    assert inbox.request_count == 2 and inbox.episode_count == 4
    assert (await container.services.get_request(default_inbox, second.id)).item_count == 1


async def test_feed_url_request_fails_with_create_mirror_hint(
    container: Container, default_inbox: str, runner: Runner, source: Source
) -> None:
    url = source.write_rss("inboxfeed", items=[1])
    request = await container.services.add_request(default_inbox, RequestCreate(url=url))
    await runner.run_until_idle()
    detail = await container.services.get_request(default_inbox, request.id)
    assert detail.status is RequestStatus.failed
    assert detail.error and "create_mirror" in detail.error
    assert detail.job is not None and detail.job.status is JobStatus.failed


async def test_media_url_request_becomes_one_item(
    container: Container, default_inbox: str, runner: Runner, source: Source
) -> None:
    url = source.url_for("/media/tiny.mp3")
    request = await container.services.add_request(default_inbox, RequestCreate(url=url))
    await runner.run_until_idle()
    detail = await container.services.get_request(default_inbox, request.id)
    assert detail.status is RequestStatus.expanded and detail.item_count == 1
    assert detail.items[0].title == "tiny.mp3"
    assert detail.items[0].state is ArchiveState.archived


async def test_prune_on_demand_and_autoprune(
    container: Container, runner: Runner, engine: FakeEngine
) -> None:
    inbox = await container.services.create_inbox(InboxCreate(name="Later", autoprune_days=7))
    assert inbox.autoprune_days == 7 and inbox.id.startswith("later-")
    url = "https://www.youtube.com/playlist?list=PLprune"
    engine.script_listing(url, listing(3, service="YouTube"))
    await container.services.add_request(inbox.id, RequestCreate(url=url))
    await runner.run_until_idle()
    async with uow(container) as unit:
        rows = await unit.catalog.for_feed(inbox.id)
        assert len(rows) == 3
        downloaded, old_download, _never = rows
        await unit.catalog.record_download(downloaded.id)
        await unit.catalog.record_download(
            old_download.id, at=datetime.now(UTC) - timedelta(days=30)
        )

    dry = await container.services.prune_inbox("later", PruneRequest(downloaded=True, dry_run=True))
    assert dry.dry_run and dry.matched == 2 and dry.deleted_count == 0 and dry.bytes_freed > 0
    with pytest.raises(ValueError):
        PruneRequest()
    result = await container.services.prune_inbox(
        inbox.id, PruneRequest(downloaded=True, older_than_days=0)
    )
    assert result.deleted_count == 2 and result.bytes_freed > 0
    async with uow(container) as unit:
        rows = await unit.catalog.for_feed(inbox.id)
        hidden = [row for row in rows if row.archive_state == ArchiveState.deleted]
        assert len(hidden) == 2 and all(not row.listed for row in hidden)
        assert not container.layout.resolve(inbox.id, f"media/{hidden[0].id}.m4a").exists()
    page = await container.services.list_items(inbox.id, listed=True)
    assert page.total == 1

    # Autoprune: only Episodes first downloaded 7+ days ago; never-downloaded stay.
    engine.script_listing(url, listing(3, service="YouTube"))
    await container.services.add_request(inbox.id, RequestCreate(url=url))
    await runner.run_until_idle()
    async with uow(container) as unit:
        rows = [
            r
            for r in await unit.catalog.for_feed(inbox.id)
            if r.archive_state == ArchiveState.archived
        ]
        assert len(rows) == 3, "deleted rows flipped back to wanted and got archived again"
        await unit.catalog.record_download(rows[0].id, at=datetime.now(UTC) - timedelta(days=10))
        await unit.catalog.record_download(rows[1].id)
    auto = await container.services.autoprune(inbox.id)
    assert auto.deleted_count == 1
    async with uow(container) as unit:
        feed = await unit.feeds.require(inbox.id)
        assert feed.last_autoprune_at is not None
        archived = [
            r
            for r in await unit.catalog.for_feed(inbox.id)
            if r.archive_state == ArchiveState.archived
        ]
        assert len(archived) == 2
    switched_off = await container.services.update_inbox(inbox.id, InboxUpdate(autoprune_days=None))
    assert switched_off.autoprune_days is None
    with pytest.raises(Conflict):
        await container.services.autoprune(inbox.id)
