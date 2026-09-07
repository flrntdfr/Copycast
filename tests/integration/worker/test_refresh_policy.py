"""The Refresh job: policies all / latest / follow, 304 still applies the policy, failures."""

from __future__ import annotations

import pytest

from copycast.adapters.storage.descriptor import FeedDescriptor
from copycast.app import Container
from copycast.application.models import MirrorUpdate
from copycast.domain.enums import (
    ArchiveState,
    AssetKind,
    AssetState,
    BackfillMode,
    ErrorKind,
    JobKind,
    JobStatus,
    JobTrigger,
    RefreshRunStatus,
    WantedReason,
)
from copycast.worker.runner import Runner
from tests.integration.worker.conftest import Source, create_mirror, jobs_of, uow

pytestmark = pytest.mark.integration


async def _run_refresh(container: Container, runner: Runner, feed_id: str) -> object:
    job = await container.services.request_refresh(feed_id)
    assert job is not None
    await runner.run_until_idle()
    async with uow(container) as unit:
        return await unit.jobs.require(job.id)


async def test_backfill_all_wants_every_archivable_item(
    container: Container, source: Source, runner: Runner
) -> None:
    url = source.write_rss("all", items=[3, 2, 1], assets=True)
    mirror = await create_mirror(container, url)
    await runner.run_until_idle()  # the first Refresh then the three archive jobs

    async with uow(container) as unit:
        items = await unit.catalog.for_feed(mirror.id)
        assert [item.ordinal for item in items] == [1, 2, 3]
        assert {item.archive_state for item in items} == {ArchiveState.archived}
        assert {item.wanted_reason for item in items} == {WantedReason.backfill}
        feed = await unit.feeds.require(mirror.id)
        assert feed.policy_applied_at is not None
        assert feed.last_refresh_success_at is not None and feed.last_error is None
        assert feed.storage_bytes > 0
        artwork = await unit.assets.feed_artwork(mirror.id)
        assert artwork is not None and artwork.state == AssetState.archived
        assert artwork.local_path == "assets/feed.artwork.jpg"
        runs = await unit.telemetry.refresh_runs(mirror.id)
        assert runs[0].status == RefreshRunStatus.succeeded and runs[0].wanted_count == 3
        for item in items:
            kinds = {(a.kind, a.state) for a in await unit.assets.for_item(item.id)}
            assert (AssetKind.artwork, AssetState.archived) in kinds
            assert (AssetKind.chapters, AssetState.archived) in kinds
            assert (AssetKind.transcript, AssetState.archived) in kinds
            assert item.media_path == f"media/{item.id}.m4a"
            assert container.layout.item_xml_path(mirror.id, item.id).is_file()
            assert item.source_item_xml and "<item" in item.source_item_xml
    layout = container.layout
    assert (layout.assets_dir(mirror.id) / f"{items[0].id}.chapters.json").is_file()
    assert not list(layout.media_dir(mirror.id).glob("*.jpg")), "artwork moved out of media/"
    archives = await jobs_of(container, mirror.id, kind=JobKind.archive_item)
    assert {job.priority for job in archives} == {200}
    assert {job.engine_version for job in archives} == {"0.0.0"}


async def test_backfill_latest_n_and_follow(
    container: Container, source: Source, runner: Runner
) -> None:
    url = source.write_rss("latest", items=[4, 3, 2, 1], artwork=False)
    mirror = await create_mirror(container, url, mode=BackfillMode.latest, latest_n=2)
    await runner.run_until_idle()
    async with uow(container) as unit:
        states = {i.source_number: i.archive_state for i in await unit.catalog.for_feed(mirror.id)}
    assert states == {
        1: ArchiveState.available,
        2: ArchiveState.available,
        3: ArchiveState.archived,
        4: ArchiveState.archived,
    }

    # Follow: a new item at the Source is archived on the next Refresh; old ones stay Available.
    source.write_rss("latest", items=[5, 4, 3, 2, 1], artwork=False)
    job = await _run_refresh(container, runner, mirror.id)
    assert job.status == JobStatus.succeeded.value and job.result["new"] == 1
    async with uow(container) as unit:
        items = {i.source_number: i for i in await unit.catalog.for_feed(mirror.id)}
        assert items[5].archive_state == ArchiveState.archived
        assert items[5].wanted_reason == WantedReason.follow and items[5].ordinal == 5
        assert items[1].archive_state == ArchiveState.available

    # Follow off: nothing new gets archived.
    await container.services.update_mirror(mirror.id, MirrorUpdate(follow=False))
    source.write_rss("latest", items=[6, 5, 4, 3, 2, 1], artwork=False)
    await runner.run_until_idle()
    job = await _run_refresh(container, runner, mirror.id)
    assert job.result["wanted"] == 0
    async with uow(container) as unit:
        items = {i.source_number: i for i in await unit.catalog.for_feed(mirror.id)}
        assert items[6].archive_state == ArchiveState.available


async def test_unchanged_304_still_applies_policy_and_delists(
    container: Container, source: Source, runner: Runner
) -> None:
    url = source.write_rss("etag", items=[2, 1], artwork=False)
    mirror = await create_mirror(container, url)
    await runner.run_until_idle()
    async with uow(container) as unit:
        feed = await unit.feeds.require(mirror.id)
        assert feed.source_etag
        feed.policy_applied_at = None  # pretend the policy never ran
        await unit.flush()
        # Put one item back to Available so the policy has something to want.
        items = await unit.catalog.for_feed(mirror.id)
        await unit.catalog.tombstone(items[0].id, listed=True)
        await unit.catalog.mark_state(items[0].id, ArchiveState.available)

    job = await _run_refresh(container, runner, mirror.id)
    assert job.status == JobStatus.succeeded.value
    assert job.result["status"] == RefreshRunStatus.unchanged.value
    assert job.result["wanted"] == 1
    conditional = [r for r in source.origin.requests_for("/rss/etag.xml") if r.if_none_match]
    assert conditional, "the second listing was a conditional GET"

    # A shorter listing delists the missing item but keeps its media.
    source.write_rss("etag", items=[2], artwork=False)
    await _run_refresh(container, runner, mirror.id)
    async with uow(container) as unit:
        by_number = {i.source_number: i for i in await unit.catalog.for_feed(mirror.id)}
        assert by_number[1].listed is False
        assert by_number[1].archive_state == ArchiveState.archived
        assert by_number[2].listed is True
    descriptor = FeedDescriptor.read(container.layout.descriptor_path(mirror.id))
    assert {i.listed for i in descriptor.items} == {True, False}


async def test_listing_failure_keeps_the_mirror_and_retries(
    container: Container, source: Source, runner: Runner
) -> None:
    url = source.write_rss("fail", items=[1], artwork=False)
    mirror = await create_mirror(container, url)
    await runner.run_until_idle()
    source.origin.script("/rss/fail.xml", status=503, times=5)
    job = await _run_refresh(container, runner, mirror.id)
    assert job.status == JobStatus.queued.value, "5xx is transient: re-queued with backoff"
    assert job.error_kind == ErrorKind.transient.value and job.attempt == 1
    async with uow(container) as unit:
        feed = await unit.feeds.require(mirror.id)
        assert feed.last_error and "503" in feed.last_error
        assert feed.last_refresh_success_at is not None
        runs = await unit.telemetry.refresh_runs(mirror.id)
        assert runs[0].status == RefreshRunStatus.failed
        counts = await unit.catalog.count_by_state(mirror.id)
        assert counts.archived == 1, "the archived Episode is untouched"
    read = await container.services.get_feed(mirror.id)
    assert read.health.status.value == "error"

    source.origin.clear()
    source.origin.script("/rss/fail.xml", status=404, times=1)
    async with uow(container) as unit:
        await unit.jobs.cancel(job.id)
    job = await _run_refresh(container, runner, mirror.id)
    assert job.status == JobStatus.failed.value
    assert job.error_kind == ErrorKind.permanent.value


async def test_scheduled_and_fetch_triggers_respect_pause_follow_and_cooldown(
    container: Container, source: Source, runner: Runner
) -> None:
    url = source.write_rss("trig", items=[1], artwork=False)
    mirror = await create_mirror(container, url)
    await runner.run_until_idle()
    # Inside the cooldown: a feed fetch enqueues nothing; a manual request does.
    assert await container.services.request_refresh(mirror.id, JobTrigger.feed_fetch) is None
    manual = await container.services.request_refresh(mirror.id, JobTrigger.manual)
    assert manual is not None and manual.trigger is JobTrigger.manual
    again = await container.services.request_refresh(mirror.id, JobTrigger.manual)
    assert again is not None and again.id == manual.id, "dedup returns the active job"
    assert await container.services.request_refresh(mirror.id, JobTrigger.scheduled) is None

    paused = await container.services.set_paused(mirror.id, True)
    assert paused.paused and paused.health.status.value == "paused"
    async with uow(container) as unit:
        cancelled = await unit.jobs.require(manual.id)
        assert cancelled.status == JobStatus.cancelled.value
    assert await container.services.request_refresh(mirror.id, JobTrigger.scheduled) is None
    assert await container.services.request_refresh(mirror.id, JobTrigger.feed_fetch) is None

    resumed = await container.services.set_paused(mirror.id, False)
    assert not resumed.paused
    refreshes = await jobs_of(container, mirror.id, kind=JobKind.refresh)
    assert any(j.status == JobStatus.queued.value for j in refreshes), "resume queues a Refresh"


async def test_minimum_length_keeps_short_items_available(
    container: Container, engine, runner: Runner
) -> None:
    """Shorts stay Available under a Mirror's own minimum, the default, or the follow path."""
    from copycast.application.models import MirrorDefaults
    from tests.support.factories import listing, listing_item

    url = "https://www.youtube.com/@shorts/videos"
    items = [
        listing_item(1, duration_seconds=45, position=2, source_number=1),
        listing_item(2, duration_seconds=None, position=1, source_number=2),
        listing_item(3, duration_seconds=1800, position=0, source_number=3),
    ]
    engine.script_listing(
        url, listing(3, service="YouTube", raw={"_type": "playlist"}, items=items)
    )
    mirror = await create_mirror(container, url, min_duration_seconds=60)
    assert mirror.min_duration_seconds == 60
    await runner.run_until_idle()
    async with uow(container) as unit:
        states = {i.source_number: i.archive_state for i in await unit.catalog.for_feed(mirror.id)}
    assert states == {
        1: ArchiveState.available,  # 45 s: a Short
        2: ArchiveState.archived,  # unknown length passes
        3: ArchiveState.archived,
    }

    # Follow honours the default once the Mirror's own value is cleared.
    await container.services.update_mirror(mirror.id, MirrorUpdate(min_duration_seconds=None))
    await container.services.set_mirror_defaults(MirrorDefaults(min_duration_seconds=600))
    items.append(listing_item(4, duration_seconds=120, position=0, source_number=4))
    engine.script_listing(
        url, listing(4, service="YouTube", raw={"_type": "playlist"}, items=items)
    )
    await _run_refresh(container, runner, mirror.id)
    async with uow(container) as unit:
        states = {i.source_number: i.archive_state for i in await unit.catalog.for_feed(mirror.id)}
    assert states[4] == ArchiveState.available
    # An explicit selection still archives whatever is named.
    from copycast.application.models import SelectionRequest

    await container.services.select_items(mirror.id, SelectionRequest(selection="1"))
    await runner.run_until_idle()
    async with uow(container) as unit:
        states = {i.source_number: i.archive_state for i in await unit.catalog.for_feed(mirror.id)}
    assert states[1] == ArchiveState.archived


async def test_mirror_language_reaches_the_engine(
    container: Container, engine, runner: Runner
) -> None:
    from copycast.application.models import MirrorDefaults
    from tests.support.factories import listing

    url = "https://www.youtube.com/@french/videos"
    engine.script_listing(url, listing(1, service="YouTube", raw={"_type": "playlist"}))
    await container.services.set_mirror_defaults(MirrorDefaults(language="de"))
    mirror = await create_mirror(container, url, preferred_language="fr_fr")
    assert mirror.preferred_language == "fr-FR" and mirror.language == "en"
    await runner.run_until_idle()
    seen = [o.get("extractor_args") for o in engine.records.options_seen if o.get("extractor_args")]
    assert seen and all(args["youtube"]["lang"] == ["fr-FR"] for args in seen), seen

    # Clearing the Mirror's value falls back to the default from Settings.
    await container.services.update_mirror(mirror.id, MirrorUpdate(preferred_language=None))
    engine.records.options_seen.clear()
    await _run_refresh(container, runner, mirror.id)
    seen = [o["extractor_args"] for o in engine.records.options_seen if o.get("extractor_args")]
    assert seen and all(args["youtube"]["lang"] == ["de"] for args in seen), seen
