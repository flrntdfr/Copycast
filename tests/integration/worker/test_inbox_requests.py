"""Inboxes: Requests expanded by the worker, pruning on demand and by Retention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from copycast.adapters.db.models import Job
from copycast.app import Container
from copycast.application.models import InboxCreate, InboxUpdate, PruneRequest, RequestCreate
from copycast.application.ports import NotReady
from copycast.domain.enums import (
    ArchiveState,
    ErrorKind,
    JobKind,
    JobStatus,
    LiveStatus,
    RequestedVia,
    RequestStatus,
    WantedReason,
)
from copycast.domain.exceptions import Conflict, NotFound
from copycast.worker.constants import LIVE_RETRY
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


async def test_a_live_stream_request_waits_for_the_recording_then_archives_it(
    container: Container, default_inbox: str, runner: Runner, engine: FakeEngine
) -> None:
    """A stream on air pushed into an Inbox is wanted at once (an Inbox never Refreshes,
    so nothing would flip ``archivable`` later); its archive job meets ``NotReady`` and
    waits without counting the attempt, then archives the recording once published."""
    url = "https://www.youtube.com/watch?v=onair"
    on_air = listing(0, service="YouTube", title="On air").model_copy(
        update={
            "items": [
                listing_item(
                    1,
                    key="youtube:onair",
                    live_status=LiveStatus.is_live,
                    archivable=False,
                    enclosure_url=None,
                    enclosure_type=None,
                )
            ]
        }
    )
    engine.script_listing(url, on_air)
    engine.fail_next_fetch(NotReady("still live", live_status=LiveStatus.is_live))
    request = await container.services.add_request(
        default_inbox, RequestCreate(url=url), via=RequestedVia.mcp
    )
    before = datetime.now(UTC)
    await runner.run_until_idle()

    detail = await container.services.get_request(default_inbox, request.id)
    assert detail.status is RequestStatus.expanded and detail.item_count == 1
    (item,) = detail.items
    assert item.state is ArchiveState.wanted and item.live_status is LiveStatus.is_live
    async with uow(container) as unit:
        row = await unit.catalog.require(item.id)
        assert row.archivable is True, "a Request wants a stream whatever its status"
        assert row.wanted_reason == WantedReason.request and row.attempt_count == 0
        assert row.last_error == "still live"
    (archive,) = await jobs_of(container, default_inbox, kind=JobKind.archive_item)
    assert archive.request_id == request.id and archive.status == JobStatus.queued.value
    assert (archive.attempt, archive.error_kind) == (0, ErrorKind.transient.value)
    assert before + LIVE_RETRY <= archive.run_after <= datetime.now(UTC) + LIVE_RETRY
    assert engine.records.fetched_item_ids == [item.id]

    # The recording is published: the same job archives it on what counts as its first try.
    async with uow(container) as unit:
        await unit.session.execute(
            update(Job).where(Job.id == archive.id).values(run_after=datetime.now(UTC))
        )
    await runner.run_until_idle()
    detail = await container.services.get_request(default_inbox, request.id)
    assert detail.items[0].state is ArchiveState.archived
    async with uow(container) as unit:
        row = await unit.jobs.require(archive.id)
        assert row.status == JobStatus.succeeded.value and row.attempt == 1
    page = await container.services.list_items(default_inbox, listed=True)
    assert page.total == 1


async def test_a_playlist_request_wants_its_upcoming_stream_too(
    container: Container, default_inbox: str, runner: Runner, engine: FakeEngine
) -> None:
    """A flat listing flags an upcoming stream; a Request wants it like the rest and the
    archive job waits (the FakeEngine stands in for the extractor's refusal)."""
    url = "https://www.youtube.com/playlist?list=PLsoon"
    scripted = listing(0, service="YouTube", title="Soon").model_copy(
        update={
            "items": [
                listing_item(1, key="youtube:old", live_status=LiveStatus.was_live, position=0),
                listing_item(
                    2,
                    key="youtube:soon",
                    live_status=LiveStatus.is_upcoming,
                    archivable=False,
                    position=1,
                ),
            ]
        }
    )
    engine.script_listing(url, scripted)
    engine.fail_next_fetch(
        NotReady("not started yet", live_status=LiveStatus.is_upcoming),
        url=scripted.items[1].source_url,
    )
    request = await container.services.add_request(default_inbox, RequestCreate(url=url))
    await runner.run_until_idle()
    detail = await container.services.get_request(default_inbox, request.id)
    assert detail.status is RequestStatus.expanded and detail.item_count == 2
    by_title = {item.title: item for item in detail.items}
    assert by_title["Episode 1"].state is ArchiveState.archived
    soon = by_title["Episode 2"]
    assert soon.state is ArchiveState.wanted and soon.live_status is LiveStatus.is_upcoming
    assert soon.last_error == "not started yet" and soon.attempt_count == 0
    archives = {
        job.item_id: job
        for job in await jobs_of(container, default_inbox, kind=JobKind.archive_item)
    }
    assert archives[by_title["Episode 1"].id].status == JobStatus.succeeded.value
    waiting = archives[soon.id]
    assert (waiting.status, waiting.attempt) == (JobStatus.queued.value, 0)
    assert waiting.run_after > datetime.now(UTC) + LIVE_RETRY - timedelta(minutes=1)
    assert engine.records.fetched_item_ids.count(soon.id) == 1


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
