"""The smaller capabilities: about, jobs, get_item, download counts, retargeting, prune payloads."""

from __future__ import annotations

import dataclasses
import threading
import uuid

import pytest

from copycast.app import Container
from copycast.application.models import MirrorUpdate, RebuildRequest, RequestCreate
from copycast.domain.enums import (
    ArchiveState,
    BackfillMode,
    ErrorKind,
    JobKind,
    JobStatus,
    JobTrigger,
    PruneMode,
    RequestedVia,
)
from copycast.domain.exceptions import Conflict, FeedExists, NotFound, SourceKindChange
from copycast.version import APP_VERSION
from copycast.worker.runner import Runner
from tests.integration.worker.conftest import Source, create_mirror, jobs_of, uow
from tests.support.factories import listing
from tests.support.fake_engine import FakeEngine

pytestmark = pytest.mark.integration


async def test_about_reports_normalized_engine_version_and_totals(
    container: Container, source: Source, runner: Runner, engine: FakeEngine
) -> None:
    engine.version_info = dataclasses.replace(engine.version_info, version="2026.08.19")
    url = source.write_rss("about", items=[1], artwork=False)
    mirror = await create_mirror(container, url)
    await runner.run_until_idle()
    about = await container.services.about()
    assert about.version == APP_VERSION
    assert about.engine.name == "fake-engine" and about.engine.version == "2026.8.19"
    assert about.ffmpeg_version == "fake 0.0" and about.layout_version == "1"
    assert about.base_url == container.settings.base_url
    assert about.totals.feeds == 1 and about.totals.episodes == 1
    assert about.totals.storage_bytes > 0
    archives = await jobs_of(container, mirror.id, kind=JobKind.archive_item)
    assert {j.engine_version for j in archives} == {"2026.8.19"}


async def test_jobs_list_get_cancel(
    container: Container, source: Source, runner: Runner, engine: FakeEngine
) -> None:
    url = source.write_rss("jobs", items=[2, 1], artwork=False)
    mirror = await create_mirror(container, url, selection="1-2")
    page = await container.services.list_jobs(feed_id=mirror.id, kind=JobKind.archive_item)
    assert page.total == 2 and {j.status for j in page.jobs} == {JobStatus.queued}
    first, second = page.jobs
    assert (await container.services.get_job(first.id)).id == first.id
    with pytest.raises(NotFound):
        await container.services.get_job(uuid.UUID(int=0))

    # Queued -> cancelled at once.
    cancelled = await container.services.cancel_job(first.id)
    assert cancelled.status is JobStatus.cancelled and cancelled.error_kind is ErrorKind.cancelled
    assert (await container.services.cancel_job(first.id)).status is JobStatus.cancelled

    # Running -> cancel_requested, honoured by the worker within its poll interval.
    gate = threading.Event()
    engine.block_fetches(gate)
    claimed = await runner.claim_available()
    assert [j.id for j in claimed] == [second.id]
    requested = await container.services.cancel_job(second.id)
    assert requested.status is JobStatus.running
    await runner.run_until_idle()
    done = await container.services.get_job(second.id)
    assert done.status is JobStatus.cancelled and done.error_kind is ErrorKind.cancelled
    gate.set()
    by_status = await container.services.list_jobs(
        feed_id=mirror.id, status=[JobStatus.cancelled], limit=1
    )
    assert by_status.total == 2 and len(by_status.jobs) == 1 and by_status.limit == 1
    async with uow(container) as unit:
        states = {i.archive_state for i in await unit.catalog.for_feed(mirror.id)}
    assert states == {ArchiveState.wanted}, "cancelled downloads go back to wanted"


async def test_get_item_and_record_download(
    container: Container, source: Source, runner: Runner
) -> None:
    url = source.write_rss("dl", items=[1], artwork=False)
    mirror = await create_mirror(container, url, selection="1")
    page = await container.services.list_items(mirror.id, limit=1000, offset=-5)
    assert page.limit == 500 and page.offset == 0 and page.total == 1
    item = page.items[0]
    assert item.media is None and item.state is ArchiveState.wanted
    assert await container.services.record_download(mirror.id, item.id) is False
    await runner.run_until_idle()
    read = await container.services.get_item(mirror.id, item.id)
    assert read.media is not None and read.media.url.endswith(f"/media/{item.id}.m4a")
    assert await container.services.record_download(mirror.id, item.id) is True
    assert await container.services.record_download("other-feed", item.id) is False
    read = await container.services.get_item(mirror.id, item.id)
    assert read.download_count == 1 and read.first_downloaded_at is not None
    with pytest.raises(NotFound):
        await container.services.get_item(mirror.id, "0000000000000000")


async def test_update_mirror_retarget_and_policy_changes(
    container: Container, source: Source, runner: Runner, engine: FakeEngine
) -> None:
    a_url = source.write_rss("ra", items=[2, 1], artwork=False, title="A")
    b_url = source.write_rss("rb", items=[1], artwork=False, title="B")
    a = await create_mirror(container, a_url, selection="1")
    b = await create_mirror(container, b_url, selection="1")
    await runner.run_until_idle()

    with pytest.raises(FeedExists) as exists:
        await container.services.update_mirror(a.id, MirrorUpdate(source_url=b_url))
    assert exists.value.existing_feed_id == b.id

    yt_url = "https://www.youtube.com/@retarget/videos"
    engine.script_listing(yt_url, listing(2, service="YouTube", title="Tube"))
    with pytest.raises(SourceKindChange):
        await container.services.update_mirror(a.id, MirrorUpdate(source_url=yt_url))

    # A retarget to another RSS Source of the same kind: Catalog merged, a Refresh queued.
    c_url = source.write_rss("rc", items=[3, 2, 1], artwork=False, title="C")
    updated = await container.services.update_mirror(a.id, MirrorUpdate(source_url=c_url))
    assert updated.id == a.id and updated.source_url == c_url
    assert updated.counts.archived == 1 and updated.counts.available == 2
    refreshes = await jobs_of(container, a.id, kind=JobKind.refresh)
    assert [j.status for j in refreshes] == [JobStatus.queued.value]
    assert refreshes[0].trigger == JobTrigger.manual.value
    assert container.layout.source_xml_path(a.id).read_bytes().count(b"<item>") == 3
    async with uow(container) as unit:
        feed = await unit.feeds.require(a.id)
        assert feed.source_dedup_key and feed.source_dedup_key.endswith("/rss/rc.xml")
        assert feed.source_channel_xml and "<item" not in feed.source_channel_xml

    # Switching the Backfill to latest N re-applies the policy on the next Refresh.
    changed = await container.services.update_mirror(
        a.id,
        MirrorUpdate(backfill={"mode": BackfillMode.latest, "latest_n": 1}, follow=True),
    )
    assert changed.backfill.mode is BackfillMode.latest and changed.backfill.latest_n == 1
    assert changed.follow is True and changed.selection is None
    await runner.run_until_idle()
    async with uow(container) as unit:
        feed = await unit.feeds.require(a.id)
        assert feed.policy_applied_at is not None
        states = {i.source_number: i.archive_state for i in await unit.catalog.for_feed(a.id)}
    assert states[3] == ArchiveState.archived and states[1] == ArchiveState.archived
    assert states[2] == ArchiveState.available

    # Back to a selection: the expression is applied synchronously.
    selected = await container.services.update_mirror(
        a.id, MirrorUpdate(backfill={"mode": BackfillMode.selection, "selection": "2"})
    )
    assert selected.selection is not None and selected.selection.count == 3
    await runner.run_until_idle()
    async with uow(container) as unit:
        states = {i.source_number: i.archive_state for i in await unit.catalog.for_feed(a.id)}
    assert states[2] == ArchiveState.archived

    # Engine options are validated at feed scope and stored verbatim.
    opts = await container.services.update_mirror(
        a.id, MirrorUpdate(engine_options={"ratelimit": 1000})
    )
    assert opts.engine_options == {"ratelimit": 1000}
    with pytest.raises(NotFound):
        await container.services.update_mirror("missing", MirrorUpdate(follow=False))


async def test_manual_prune_job_payload_and_rebuild_dedup(
    container: Container, default_inbox: str, runner: Runner, engine: FakeEngine
) -> None:
    url = "https://www.youtube.com/playlist?list=PLmanual"
    engine.script_listing(url, listing(2, service="YouTube"))
    await container.services.add_request(default_inbox, RequestCreate(url=url), via=RequestedVia.ui)
    await runner.run_until_idle()
    async with uow(container) as unit:
        rows = await unit.catalog.for_feed(default_inbox)
        await unit.catalog.record_download(rows[0].id)
        job = await unit.jobs.enqueue(
            JobKind.prune,
            JobTrigger.manual,
            feed_id=default_inbox,
            payload={"mode": PruneMode.manual.value, "downloaded": True, "older_than_days": 0},
            dedup=f"prune:{default_inbox}",
        )
        assert job is not None
        bad = await unit.jobs.enqueue(
            JobKind.prune, JobTrigger.manual, feed_id=default_inbox, payload={"mode": "manual"}
        )
        assert bad is not None
    await runner.run_until_idle()
    async with uow(container) as unit:
        done = await unit.jobs.require(job.id)
        assert done.status == JobStatus.succeeded.value
        assert done.result == {
            "mode": "manual",
            "matched": 1,
            "deleted_count": 1,
            "bytes_freed": len(engine.audio_bytes),
        }
        failed = await unit.jobs.require(bad.id)
        assert failed.status == JobStatus.failed.value
        assert failed.error_kind == ErrorKind.permanent.value
        assert failed.error and "criterion" in failed.error

    queued = await container.services.rebuild(RebuildRequest())
    assert queued.kind is JobKind.rebuild and queued.status is JobStatus.queued
    with pytest.raises(Conflict):
        await container.services.rebuild(RebuildRequest(dry_run=False))
    assert (await container.services.cancel_job(queued.id)).status is JobStatus.cancelled
