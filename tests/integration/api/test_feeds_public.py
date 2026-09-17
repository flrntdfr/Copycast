"""The public feed route: content type, ETag/304, HEAD, the light Refresh a fetch runs."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from copycast.app import Container
from copycast.domain.enums import JobKind, JobStatus, JobTrigger, ListingOrder
from copycast.worker.runner import Runner
from tests.integration.api.conftest import Source, archived_items, create_mirror, items_of
from tests.integration.worker.conftest import jobs_of
from tests.support.factories import EPOCH, listing, listing_item
from tests.support.fake_engine import FakeEngine
from tests.support.paths import FEED_URL, api
from tests.support.xml import parse

pytestmark = pytest.mark.integration

CHANNEL_RAW = {
    "extractor": "youtube:tab",
    "id": "UClight00000000000000000",
    "channel_id": "UClight00000000000000000",
}


async def refresh_jobs(container: Container, feed_id: str) -> list[Any]:
    return await jobs_of(container, feed_id, kind=JobKind.refresh)


async def fetch_refreshes(container: Container, feed_id: str) -> list[Any]:
    jobs = await refresh_jobs(container, feed_id)
    return [j for j in jobs if j.trigger == JobTrigger.feed_fetch]


async def light_runs(container: Container, feed_id: str) -> list[Any]:
    """The feed's light ``refresh_runs`` rows, newest first."""
    async with container.uow_factory() as uow:
        runs = await uow.telemetry.refresh_runs(feed_id)
        return [r for r in runs if r.light]


async def outside_cooldown(container: Container, feed_id: str) -> None:
    ago = datetime.now(UTC) - timedelta(hours=1)
    async with container.uow_factory() as uow:
        await uow.feeds.set_refresh_attempt(feed_id, ago)
        await uow.feeds.set_light_refresh_at(feed_id, ago)


def feed_items(response: httpx.Response) -> list[Any]:
    return parse(response.content).find("channel").findall("item")


async def test_feed_xml_headers_and_conditionals(
    client: httpx.AsyncClient, source: Source, runner: Runner
) -> None:
    url = source.write_rss("feed", items=[2, 1], artwork=False)
    mirror = await create_mirror(client, url)
    archived = await archived_items(client, runner, mirror["id"])
    assert len(archived) == 2

    response = await client.get(FEED_URL(mirror["id"]))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/rss+xml")
    assert response.headers["cache-control"] == "no-cache"
    etag = response.headers["etag"]
    assert etag.startswith('W/"') and len(etag) == len('W/""') + 20
    last_modified = response.headers["last-modified"]
    assert last_modified.endswith("GMT")
    root = parse(response.content)
    channel = root.find("channel")
    assert channel.findtext("title") == "Worker Test Podcast"
    items = channel.findall("item")
    assert len(items) == 2
    enclosure = items[0].find("enclosure")
    assert enclosure.get("url").startswith("http://testserver/feeds/")
    assert enclosure.get("type") == "audio/mp4"

    # Conditionals: If-None-Match (weak compare) and If-Modified-Since -> 304 with headers.
    cached = await client.get(FEED_URL(mirror["id"]), headers={"If-None-Match": etag})
    assert cached.status_code == 304 and cached.content == b""
    assert cached.headers["etag"] == etag
    strong = await client.get(
        FEED_URL(mirror["id"]), headers={"If-None-Match": etag.removeprefix("W/")}
    )
    assert strong.status_code == 304
    since = await client.get(FEED_URL(mirror["id"]), headers={"If-Modified-Since": last_modified})
    assert since.status_code == 304
    stale = await client.get(
        FEED_URL(mirror["id"]),
        headers={"If-Modified-Since": "Mon, 01 Jan 2001 00:00:00 GMT"},
    )
    assert stale.status_code == 200
    mismatch = await client.get(FEED_URL(mirror["id"]), headers={"If-None-Match": 'W/"nope"'})
    assert mismatch.status_code == 200

    # HEAD: headers only, same ETag, no body.
    head = await client.head(FEED_URL(mirror["id"]))
    assert head.status_code == 200 and head.content == b""
    assert head.headers["etag"] == etag
    assert int(head.headers["content-length"]) == len(response.content)


async def test_feed_xml_unknown_feed_is_404_problem(client: httpx.AsyncClient) -> None:
    response = await client.get(FEED_URL("nope"))
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["type"] == "urn:copycast:problem:not-found"


async def test_revision_changes_the_etag(
    client: httpx.AsyncClient, source: Source, runner: Runner
) -> None:
    url = source.write_rss("rev", items=[2, 1], artwork=False)
    mirror = await create_mirror(client, url)
    archived = await archived_items(client, runner, mirror["id"])
    before = await client.get(FEED_URL(mirror["id"]))
    etag = before.headers["etag"]
    # Deleting an Episode bumps the revision: new ETag, item gone from the feed.
    deleted = await client.delete(api(f"/feeds/{mirror['id']}/items/{archived[0]['id']}"))
    assert deleted.status_code == 204
    after = await client.get(FEED_URL(mirror["id"]), headers={"If-None-Match": etag})
    assert after.status_code == 200
    assert after.headers["etag"] != etag
    assert len(parse(after.content).find("channel").findall("item")) == 1
    feed = (await client.get(api(f"/feeds/{mirror['id']}"))).json()
    assert feed["revision"] > mirror["revision"]


# --------------------------------------------------------------------------- light Refresh


async def test_fetch_runs_a_light_refresh_inline_for_an_rss_mirror(
    client: httpx.AsyncClient, source: Source, runner: Runner, container: Container
) -> None:
    """A fetch outside the cooldown lists the Source's new item in that same response."""
    url = source.write_rss("light", items=[1], artwork=False)
    # Automatic: a listed item is in the feed as soon as it is known, archived or not.
    mirror = await create_mirror(client, url, mode="automatic")
    await runner.run_until_idle()  # the first (policy) Refresh
    assert len(await refresh_jobs(container, mirror["id"])) == 1
    # Right after that Refresh the cooldown applies: the fetch checks nothing.
    source.write_rss("light", items=[2, 1], artwork=False)
    inside = await client.get(FEED_URL(mirror["id"]))
    assert inside.status_code == 200 and len(feed_items(inside)) == 1
    assert await light_runs(container, mirror["id"]) == []

    await outside_cooldown(container, mirror["id"])
    hits = source.origin.hits("/rss/light.xml")
    response = await client.get(FEED_URL(mirror["id"]))
    assert response.status_code == 200
    assert len(feed_items(response)) == 2, "the new item is in the answer"
    assert source.origin.hits("/rss/light.xml") == hits + 1
    assert len(await refresh_jobs(container, mirror["id"])) == 1, "no worker Refresh was queued"
    runs = await light_runs(container, mirror["id"])
    assert len(runs) == 1
    assert runs[0].trigger == JobTrigger.feed_fetch and runs[0].status == "succeeded"
    assert (runs[0].listed_count, runs[0].new_count, runs[0].delisted_count) == (2, 1, 0)
    feed = (await client.get(api(f"/feeds/{mirror['id']}"))).json()
    assert feed["last_light_refresh_at"] is not None
    assert feed["last_error"] is None and feed["health"]["status"] == "ok"
    assert feed["counts"]["available"] == 2, "both listed, neither archived (Automatic)"
    # The verbatim Source document is rewritten (the archive job re-parses it).
    snapshot = container.layout.source_xml_path(mirror["id"]).read_bytes()
    assert b"urn:worker:episode:2" in snapshot

    # Outside the cooldown again, an unchanged document is a 304: the run is `unchanged`,
    # the revision stays, and so the client's conditional GET is a 304 too.
    await outside_cooldown(container, mirror["id"])
    conditional = await client.get(
        FEED_URL(mirror["id"]), headers={"If-None-Match": response.headers["etag"]}
    )
    assert conditional.status_code == 304
    assert source.origin.requests_for("/rss/light.xml")[-1].if_none_match is not None
    runs = await light_runs(container, mirror["id"])
    assert [r.status for r in runs] == ["unchanged", "succeeded"]
    assert runs[0].light and runs[0].trigger == JobTrigger.feed_fetch

    # Inside the cooldown the next fetch does nothing even though the Source changed.
    source.write_rss("light", items=[3, 2, 1], artwork=False)
    again = await client.get(FEED_URL(mirror["id"]))
    assert again.status_code == 200 and len(feed_items(again)) == 2
    assert len(await light_runs(container, mirror["id"])) == 2


async def test_light_refresh_applies_the_policy_and_queues_archives(
    client: httpx.AsyncClient, source: Source, runner: Runner, container: Container
) -> None:
    """A Following Mirror wants what the light Refresh found; the archive job is queued."""
    url = source.write_rss("follow", items=[1], artwork=False)
    mirror = await create_mirror(client, url)
    archived = await archived_items(client, runner, mirror["id"])
    assert len(archived) == 1
    await outside_cooldown(container, mirror["id"])
    source.write_rss("follow", items=[2, 1], artwork=False)
    response = await client.get(FEED_URL(mirror["id"]))
    assert response.status_code == 200
    assert len(feed_items(response)) == 1, "not archived yet: not in a non-Automatic feed"
    runs = await light_runs(container, mirror["id"])
    assert len(runs) == 1 and runs[0].status == "succeeded" and runs[0].wanted_count == 1
    archive_jobs = await jobs_of(container, mirror["id"], kind=JobKind.archive_item)
    queued = [j for j in archive_jobs if j.status == JobStatus.queued]
    assert len(queued) == 1 and queued[0].trigger == JobTrigger.policy
    assert len(await archived_items(client, runner, mirror["id"])) == 2
    assert len(feed_items(await client.get(FEED_URL(mirror["id"])))) == 2


async def test_fetch_never_refreshes_on_head_paused_or_inbox(
    client: httpx.AsyncClient, source: Source, runner: Runner, container: Container
) -> None:
    url = source.write_rss("never", items=[1], artwork=False)
    mirror = await create_mirror(client, url, mode="automatic")
    await runner.run_until_idle()
    await outside_cooldown(container, mirror["id"])
    source.write_rss("never", items=[2, 1], artwork=False)

    assert (await client.head(FEED_URL(mirror["id"]))).status_code == 200
    assert await light_runs(container, mirror["id"]) == []

    paused = await client.post(api(f"/mirrors/{mirror['id']}/pause"))
    assert paused.status_code == 200 and paused.json()["paused"] is True
    response = await client.get(FEED_URL(mirror["id"]))
    assert response.status_code == 200 and len(feed_items(response)) == 1
    assert await light_runs(container, mirror["id"]) == []
    assert len(await refresh_jobs(container, mirror["id"])) == 1

    # Resuming queues one manual Refresh; a fetch with Follow off still checks the Source
    # (a light Refresh lists new items even when the policy archives nothing).
    resumed = await client.post(api(f"/mirrors/{mirror['id']}/resume"))
    assert resumed.status_code == 200 and resumed.json()["paused"] is False
    jobs = await refresh_jobs(container, mirror["id"])
    assert len(jobs) == 2 and {j.trigger for j in jobs} == {"policy", "manual"}
    await runner.run_until_idle()
    await outside_cooldown(container, mirror["id"])
    unfollowed = await client.patch(api(f"/mirrors/{mirror['id']}"), json={"follow": False})
    assert unfollowed.status_code == 200 and unfollowed.json()["follow"] is False
    source.write_rss("never", items=[3, 2, 1], artwork=False)
    response = await client.get(FEED_URL(mirror["id"]))
    assert response.status_code == 200 and len(feed_items(response)) == 3
    runs = await light_runs(container, mirror["id"])
    assert len(runs) == 1 and runs[0].status == "succeeded" and runs[0].wanted_count == 0
    assert len(await refresh_jobs(container, mirror["id"])) == 2

    # An Inbox feed never triggers anything.
    inbox = (await client.get(api("/inboxes"))).json()["feeds"][0]
    assert (await client.get(FEED_URL(inbox["id"]))).status_code == 200
    assert await refresh_jobs(container, inbox["id"]) == []
    assert await light_runs(container, inbox["id"]) == []


async def test_origin_failure_leaves_the_feed_served_and_last_error_untouched(
    client: httpx.AsyncClient, source: Source, runner: Runner, container: Container
) -> None:
    url = source.write_rss("down", items=[1], artwork=False)
    mirror = await create_mirror(client, url)
    archived = await archived_items(client, runner, mirror["id"])
    assert len(archived) == 1
    await outside_cooldown(container, mirror["id"])
    source.origin.script("/rss/down.xml", status=503)
    response = await client.get(FEED_URL(mirror["id"]))
    assert response.status_code == 200 and len(feed_items(response)) == 1
    runs = await light_runs(container, mirror["id"])
    assert len(runs) == 1 and runs[0].status == "failed"
    assert runs[0].error and "503" in runs[0].error
    feed = (await client.get(api(f"/feeds/{mirror['id']}"))).json()
    assert feed["last_error"] is None and feed["health"]["status"] == "ok"
    assert feed["last_light_refresh_at"] is not None
    assert len(await refresh_jobs(container, mirror["id"])) == 1


async def test_slow_origin_hits_the_deadline_and_the_feed_is_still_served(
    client: httpx.AsyncClient,
    source: Source,
    runner: Runner,
    container: Container,
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from copycast.adapters.api.routes import public

    url = "https://www.youtube.com/@slow/videos"
    engine.script_listing(url, listing(1, service="YouTube", title="Slow", raw=CHANNEL_RAW))
    mirror = await create_mirror(client, url, mode="automatic")
    await runner.run_until_idle()
    await outside_cooldown(container, mirror["id"])
    gave_up = threading.Event()

    def slow_list_source(
        url: str, options: Any, cancel: Any, log: Any, *, limit: int | None = None
    ) -> Any:
        # A Source that answers only once the fetch has given up and set the token.
        while limit is not None and not cancel.cancelled:
            time.sleep(0.01)
        gave_up.set()
        return listing(1, service="YouTube", title="Slow", raw=CHANNEL_RAW)

    monkeypatch.setattr(engine, "list_source", slow_list_source)
    monkeypatch.setattr(public, "LIGHT_REFRESH_DEADLINE_SECONDS", 0.2)
    started = time.perf_counter()
    response = await client.get(FEED_URL(mirror["id"]))
    assert response.status_code == 200 and len(feed_items(response)) == 1
    assert time.perf_counter() - started < 2.0, "the feed is served at the deadline"
    runs = await light_runs(container, mirror["id"])
    assert len(runs) == 1 and runs[0].status == "failed"
    assert runs[0].error and "longer than 0.2 seconds" in runs[0].error
    assert gave_up.wait(2.0), "the token was set so the listing thread stopped"


async def test_simultaneous_fetches_check_the_source_once(
    client: httpx.AsyncClient, source: Source, runner: Runner, container: Container
) -> None:
    url = source.write_rss("twice", items=[1], artwork=False)
    mirror = await create_mirror(client, url, mode="automatic")
    await runner.run_until_idle()
    await outside_cooldown(container, mirror["id"])
    source.write_rss("twice", items=[2, 1], artwork=False)
    hits = source.origin.hits("/rss/twice.xml")
    responses = await asyncio.gather(*(client.get(FEED_URL(mirror["id"])) for _ in range(3)))
    assert {r.status_code for r in responses} == {200}
    assert {len(feed_items(r)) for r in responses} == {2}
    assert source.origin.hits("/rss/twice.xml") == hits + 1
    assert len(await light_runs(container, mirror["id"])) == 1


async def test_fetch_of_a_channel_mirror_lists_shallowly_without_delisting(
    client: httpx.AsyncClient,
    runner: Runner,
    container: Container,
    engine: FakeEngine,
) -> None:
    url = "https://www.youtube.com/@light/videos"
    engine.script_listing(url, listing(2, service="YouTube", title="Light", raw=CHANNEL_RAW))
    mirror = await create_mirror(client, url, mode="automatic", preferred_language="fr")
    await runner.run_until_idle()
    assert engine.records.listings[-1].limit is None, "the full Refresh lists everything"
    listing_json = container.layout.source_listing_path(mirror["id"])
    assert listing_json.is_file()
    written = listing_json.read_bytes()
    await outside_cooldown(container, mirror["id"])

    # The first page the light Refresh sees: the new video only; the rest is further down.
    first_page = listing(
        3, service="YouTube", title="Light", raw={**CHANNEL_RAW, "page": "first"}
    ).model_copy(
        update={"items": [listing_item(3, published_at=EPOCH + timedelta(days=3), position=0)]}
    )
    engine.script_listing(url, first_page)
    response = await client.get(FEED_URL(mirror["id"]))
    assert response.status_code == 200 and len(feed_items(response)) == 3
    shallow = engine.records.listings[-1]
    assert shallow.url == url and shallow.limit == 15
    assert shallow.options["extractor_args"]["youtube"]["lang"] == ["fr"], "merged like the worker"
    assert shallow.options["subtitleslangs"] == ["fr", "en", "-live_chat"]
    items = await items_of(client, mirror["id"])
    assert sorted(i["ordinal"] for i in items) == [1, 2, 3]
    assert all(i["listed"] for i in items), "a first page delists nothing"
    runs = await light_runs(container, mirror["id"])
    assert len(runs) == 1 and runs[0].status == "succeeded"
    assert (runs[0].listed_count, runs[0].new_count, runs[0].delisted_count) == (1, 1, 0)
    assert len(await refresh_jobs(container, mirror["id"])) == 1
    assert listing_json.read_bytes() == written, "source/listing.json keeps the full listing"


async def test_fetch_of_a_playlist_mirror_queues_a_full_refresh(
    client: httpx.AsyncClient,
    runner: Runner,
    container: Container,
    engine: FakeEngine,
) -> None:
    url = "https://www.youtube.com/playlist?list=PLlight"
    engine.script_listing(
        url,
        listing(
            2,
            service="YouTube",
            title="Playlist",
            order=ListingOrder.oldest_first,
            raw={"extractor": "youtube:playlist", "id": "PLlight"},
        ),
    )
    mirror = await create_mirror(client, url, mode="automatic")
    await runner.run_until_idle()
    await outside_cooldown(container, mirror["id"])
    listed_before = len(engine.records.listings)
    response = await client.get(FEED_URL(mirror["id"]))
    assert response.status_code == 200
    assert len(engine.records.listings) == listed_before, "nothing is listed inline"
    queued = await fetch_refreshes(container, mirror["id"])
    assert len(queued) == 1 and queued[0].status == JobStatus.queued
    assert await light_runs(container, mirror["id"]) == []
    feed = (await client.get(api(f"/feeds/{mirror['id']}"))).json()
    assert feed["last_light_refresh_at"] is not None
    # Inside the cooldown a second fetch adds nothing; the job runs when the worker gets to it.
    assert (await client.get(FEED_URL(mirror["id"]))).status_code == 200
    assert len(await fetch_refreshes(container, mirror["id"])) == 1
    await runner.run_until_idle()
    assert (await fetch_refreshes(container, mirror["id"]))[0].status == JobStatus.succeeded


async def test_shallow_listing_of_a_playlist_like_source_falls_back_to_a_full_refresh(
    client: httpx.AsyncClient,
    runner: Runner,
    container: Container,
    engine: FakeEngine,
) -> None:
    """A Source that lists oldest first (its first page is not the newest items) is queued."""
    url = "https://www.youtube.com/@sets/videos"
    engine.script_listing(url, listing(2, service="YouTube", title="Sets", raw=CHANNEL_RAW))
    mirror = await create_mirror(client, url, mode="automatic")
    await runner.run_until_idle()
    await outside_cooldown(container, mirror["id"])
    engine.script_listing(
        url,
        listing(
            3, service="YouTube", title="Sets", order=ListingOrder.oldest_first, raw=CHANNEL_RAW
        ),
    )
    response = await client.get(FEED_URL(mirror["id"]))
    assert response.status_code == 200 and len(feed_items(response)) == 2
    assert engine.records.listings[-1].limit == 15
    assert len(await items_of(client, mirror["id"])) == 2, "the shallow listing was discarded"
    queued = await fetch_refreshes(container, mirror["id"])
    assert len(queued) == 1 and queued[0].status == JobStatus.queued
    runs = await light_runs(container, mirror["id"])
    assert len(runs) == 1 and runs[0].status == "cancelled"


async def test_manual_refresh_ignores_paused_and_cooldown(
    client: httpx.AsyncClient, source: Source, runner: Runner, container: Container
) -> None:
    url = source.write_rss("manual", items=[1], artwork=False)
    mirror = await create_mirror(client, url)
    await runner.run_until_idle()
    await client.post(api(f"/mirrors/{mirror['id']}/pause"))
    queued = await client.post(api(f"/mirrors/{mirror['id']}/refresh"))
    assert queued.status_code == 202, queued.text
    job = queued.json()
    assert job["kind"] == "refresh" and job["trigger"] == "manual" and job["status"] == "queued"
    # A second manual request while one is queued returns the same job.
    again = await client.post(api(f"/mirrors/{mirror['id']}/refresh"))
    assert again.status_code == 202 and again.json()["id"] == job["id"]
    assert len(await refresh_jobs(container, mirror["id"])) == 2
