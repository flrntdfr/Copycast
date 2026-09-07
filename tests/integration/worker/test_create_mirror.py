"""create_mirror: probe cache reuse, duplicates, selection at create, the first Refresh."""

from __future__ import annotations

import json

import pytest

from copycast.adapters.storage.descriptor import read_intent_version
from copycast.app import Container
from copycast.application.models import MirrorCreate, ProbeRequest
from copycast.domain.enums import ArchiveState, BackfillMode, JobKind, JobStatus, WantedReason
from copycast.domain.exceptions import Ambiguous, FeedExists, NotFound, Unsupported
from copycast.worker.runner import Runner
from tests.integration.worker.conftest import Source, create_mirror, jobs_of, uow

pytestmark = pytest.mark.integration


async def test_create_reuses_the_probe_cache(container: Container, source: Source) -> None:
    url = source.write_rss("show", items=[3, 2, 1])
    probe = await container.services.probe_source(ProbeRequest(url=url))
    assert len(probe.candidates) == 1
    token = probe.candidates[0].candidate_token
    hits_before = source.origin.hits("/rss/show.xml")

    mirror = await container.services.create_mirror(
        MirrorCreate(source_url=url, candidate_token=token, backfill={"mode": "all"})
    )

    assert source.origin.hits("/rss/show.xml") == hits_before, "the cached listing was reused"
    assert mirror.feed_url.endswith(f"/feeds/{mirror.id}.xml")
    assert mirror.counts.available == 3 and mirror.episode_count == 0
    assert mirror.follow is True and mirror.backfill.mode is BackfillMode.all
    layout = container.layout
    assert layout.source_xml_path(mirror.id).is_file(), "source/feed.xml written after commit"
    assert read_intent_version(layout.descriptor_path(mirror.id)) is not None
    refreshes = await jobs_of(container, mirror.id, kind=JobKind.refresh)
    assert [job.status for job in refreshes] == [JobStatus.queued.value]
    async with uow(container) as unit:
        feed = await unit.feeds.require(mirror.id)
        assert feed.source_channel_xml and "<item" not in feed.source_channel_xml
        assert feed.source_etag is not None


async def test_duplicate_source_is_refused_with_the_existing_id(
    container: Container, source: Source
) -> None:
    url = source.write_rss("dup", items=[1])
    first = await create_mirror(container, url)
    with pytest.raises(FeedExists) as excinfo:
        await create_mirror(container, url + "?utm_source=newsletter")
    assert excinfo.value.existing_feed_id == first.id


async def test_selection_at_create(container: Container, source: Source, runner: Runner) -> None:
    url = source.write_rss("sel", items=[6, 5, 4, 3, 2, 1])
    mirror = await create_mirror(container, url, selection="1-3, 6")

    assert mirror.follow is False
    assert mirror.backfill.mode is BackfillMode.selection
    assert mirror.selection is not None
    assert mirror.selection.count == 4 and mirror.selection.applied_at is not None
    assert mirror.counts.wanted == 4 and mirror.counts.available == 2
    archives = await jobs_of(container, mirror.id, kind=JobKind.archive_item)
    assert len(archives) == 4 and all(job.priority == 50 for job in archives)
    assert not await jobs_of(container, mirror.id, kind=JobKind.refresh)

    await runner.run_until_idle()
    async with uow(container) as unit:
        items = await unit.catalog.for_feed(mirror.id)
        by_number = {item.source_number: item for item in items}
        for n in (1, 2, 3, 6):
            assert by_number[n].archive_state == ArchiveState.archived
            assert by_number[n].wanted_reason == WantedReason.manual
        for n in (4, 5):
            assert by_number[n].archive_state == ArchiveState.available

    # A later Refresh archives nothing new: the policy is applied and follow is off.
    job = await container.services.request_refresh(mirror.id)
    assert job is not None
    await runner.run_until_idle()
    async with uow(container) as unit:
        finished = await unit.jobs.require(job.id)
        assert finished.status == JobStatus.succeeded.value
        assert finished.result is not None and finished.result["wanted"] == 0
        assert finished.result["enqueued"] == 0


async def test_ambiguous_page_and_unsupported_media(
    container: Container, source: Source, tmp_path: object
) -> None:
    source.write_rss("a", items=[1])
    source.write_rss("b", items=[2])
    page = source.root / "page.html"
    page.write_text(
        "<html><head>"
        f'<link rel="alternate" type="application/rss+xml" href="{source.url_for("/rss/a.xml")}">'
        f'<link rel="alternate" type="application/rss+xml" href="{source.url_for("/rss/b.xml")}">'
        "</head><body>two feeds</body></html>",
        encoding="utf-8",
    )
    probe = await container.services.probe_source(ProbeRequest(url=source.url_for("/page.html")))
    assert len(probe.candidates) == 2
    with pytest.raises(Ambiguous) as excinfo:
        await create_mirror(container, source.url_for("/page.html"))
    assert len(excinfo.value.candidates) == 2
    mirror = await container.services.create_mirror(
        MirrorCreate(
            source_url=source.url_for("/page.html"),
            candidate_token=probe.candidates[1].candidate_token,
        )
    )
    assert mirror.source_url == source.url_for("/rss/b.xml")
    with pytest.raises(Unsupported):
        await container.services.probe_source(ProbeRequest(url=source.url_for("/media/tiny.mp3")))


async def test_ytdlp_source_writes_listing_json(
    container: Container, engine, runner: Runner
) -> None:
    from tests.support.factories import listing

    url = "https://www.youtube.com/@tester/videos"
    engine.script_listing(
        url, listing(2, service="YouTube", title="Tester", raw={"_type": "playlist"})
    )
    mirror = await create_mirror(container, url, mode=BackfillMode.latest, latest_n=1)
    assert mirror.source_kind.value == "ytdlp" and mirror.service == "YouTube"
    listing_path = container.layout.source_listing_path(mirror.id)
    assert json.loads(listing_path.read_text())["_type"] == "playlist"
    await runner.run_until_idle()
    async with uow(container) as unit:
        counts = await unit.catalog.count_by_state(mirror.id)
        assert counts.archived == 1 and counts.available == 1


async def test_get_and_list_and_delete_feed(container: Container, source: Source) -> None:
    url = source.write_rss("del", items=[2, 1])
    mirror = await create_mirror(container, url)
    read = await container.services.get_feed(mirror.id)
    assert read.id == mirror.id
    feeds = await container.services.list_feeds()
    assert [feed.id for feed in feeds.feeds] == [mirror.id]
    feed_dir = container.layout.feed_dir(mirror.id)
    assert feed_dir.is_dir()
    await container.services.delete_feed(mirror.id)
    assert not feed_dir.exists()
    with pytest.raises(NotFound):
        await container.services.get_feed(mirror.id)
    assert (await jobs_of(container, mirror.id)) == []


async def test_archive_fills_description_date_and_author_from_the_engine_info(
    container: Container, engine, runner: Runner
) -> None:
    """A flat YouTube listing carries no description or date; the fetched info does."""
    from datetime import UTC, datetime

    from tests.support.factories import listing

    url = "https://www.youtube.com/@tester/videos"
    engine.script_listing(
        url,
        listing(1, service="YouTube", title="Tester", with_dates=False, raw={"_type": "playlist"}),
    )
    engine.info_extra = {
        "description": "Full show notes from the video page",
        "timestamp": 1718136000,
        "uploader": "Tester Channel",
        "thumbnail": "https://i.ytimg.com/vi/x/maxres.jpg",
    }
    mirror = await create_mirror(container, url)
    async with uow(container) as unit:
        before = (await unit.catalog.for_feed(mirror.id))[0]
        assert before.published_at is None
        listed_description = before.description
    await runner.run_until_idle()
    async with uow(container) as unit:
        item = (await unit.catalog.for_feed(mirror.id))[0]
        assert item.archive_state == ArchiveState.archived
        assert item.published_at == datetime(2024, 6, 11, 20, 0, tzinfo=UTC)
        # The listing's description, when it had one, is kept; only gaps are filled.
        assert item.description == (listed_description or "Full show notes from the video page")
        assert item.author == "Tester Channel"  # the flat listing named no author
        assert item.artwork_url is not None
    page = await container.services.list_items(mirror.id)
    assert page.items[0].published_at is not None
