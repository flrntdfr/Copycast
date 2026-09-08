"""The public feed route: content type, ETag/304, HEAD, fetch-triggered Refresh rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from copycast.app import Container
from copycast.domain.enums import JobKind, JobStatus, JobTrigger
from copycast.worker.runner import Runner
from tests.integration.api.conftest import Source, archived_items, create_mirror
from tests.integration.worker.conftest import jobs_of
from tests.support.paths import FEED_URL, api
from tests.support.xml import parse

pytestmark = pytest.mark.integration


async def refresh_jobs(container: Container, feed_id: str) -> list[object]:
    return await jobs_of(container, feed_id, kind=JobKind.refresh)


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


async def test_fetch_triggers_refresh_only_in_follow_mode_under_cooldown(
    client: httpx.AsyncClient, source: Source, runner: Runner, container: Container
) -> None:
    url = source.write_rss("follow", items=[1], artwork=False)
    mirror = await create_mirror(client, url)
    await runner.run_until_idle()  # the first (policy) Refresh and its archive job
    assert len(await refresh_jobs(container, mirror["id"])) == 1
    # Right after that Refresh the cooldown applies: a fetch queues nothing.
    assert (await client.get(FEED_URL(mirror["id"]))).status_code == 200
    assert len(await refresh_jobs(container, mirror["id"])) == 1
    async with container.uow_factory() as uow:
        await uow.feeds.set_refresh_attempt(mirror["id"], datetime.now(UTC) - timedelta(hours=1))

    # Two fetches outside the cooldown: exactly one feed_fetch Refresh (dedup + cooldown).
    for _ in range(2):
        response = await client.get(FEED_URL(mirror["id"]))
        assert response.status_code == 200
    jobs = await refresh_jobs(container, mirror["id"])
    fetch_triggered = [j for j in jobs if j.trigger == JobTrigger.feed_fetch]  # type: ignore[attr-defined]
    assert len(fetch_triggered) == 1 and fetch_triggered[0].status == JobStatus.queued  # type: ignore[attr-defined]

    # A HEAD and a 304 conditional GET do not add another one inside the cooldown.
    await client.head(FEED_URL(mirror["id"]))
    etag = response.headers["etag"]
    assert (
        await client.get(FEED_URL(mirror["id"]), headers={"If-None-Match": etag})
    ).status_code == 304
    assert len(await refresh_jobs(container, mirror["id"])) == 2


async def test_fetch_does_not_trigger_refresh_when_paused(
    client: httpx.AsyncClient, source: Source, runner: Runner, container: Container
) -> None:
    url = source.write_rss("paused", items=[1], artwork=False)
    mirror = await create_mirror(client, url)
    await runner.run_until_idle()
    paused = await client.post(api(f"/mirrors/{mirror['id']}/pause"))
    assert paused.status_code == 200 and paused.json()["paused"] is True
    assert (await client.get(FEED_URL(mirror["id"]))).status_code == 200
    assert len(await refresh_jobs(container, mirror["id"])) == 1

    # Resuming queues one manual Refresh; a fetch with follow off still refreshes (pull to
    # refresh lists new items even when the policy archives nothing).
    resumed = await client.post(api(f"/mirrors/{mirror['id']}/resume"))
    assert resumed.status_code == 200 and resumed.json()["paused"] is False
    jobs = await refresh_jobs(container, mirror["id"])
    assert len(jobs) == 2 and {j.trigger for j in jobs} == {"policy", "manual"}  # type: ignore[attr-defined]
    await runner.run_until_idle()
    async with container.uow_factory() as uow:
        await uow.feeds.set_refresh_attempt(mirror["id"], datetime.now(UTC) - timedelta(hours=1))
    unfollowed = await client.patch(api(f"/mirrors/{mirror['id']}"), json={"follow": False})
    assert unfollowed.status_code == 200 and unfollowed.json()["follow"] is False
    assert (await client.get(FEED_URL(mirror["id"]))).status_code == 200
    assert len(await refresh_jobs(container, mirror["id"])) == 3

    # An Inbox feed never triggers a Refresh.
    inbox = (await client.get(api("/inboxes"))).json()["feeds"][0]
    assert (await client.get(FEED_URL(inbox["id"]))).status_code == 200
    assert await refresh_jobs(container, inbox["id"]) == []


async def test_fetch_waits_for_the_refresh_it_triggers(
    client: httpx.AsyncClient,
    source: Source,
    runner: Runner,
    container: Container,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pull to refresh: the fetch serves what the Refresh it triggered found."""
    import asyncio

    from copycast.adapters.api.routes import public

    url = source.write_rss("pull", items=[1], artwork=False)
    mirror = await create_mirror(client, url)
    await runner.run_until_idle()
    async with container.uow_factory() as uow:
        await uow.feeds.set_refresh_attempt(mirror["id"], datetime.now(UTC) - timedelta(hours=1))
    source.write_rss("pull", items=[2, 1], artwork=False)
    monkeypatch.setattr(public, "FEED_FETCH_WAIT_SECONDS", 10.0)
    monkeypatch.setattr(public, "FEED_FETCH_POLL_SECONDS", 0.05)

    async def work() -> None:
        # The worker picks the Refresh up while the fetch is waiting for it.
        await asyncio.sleep(0.2)
        await runner.run_until_idle()

    worker = asyncio.create_task(work())
    response = await client.get(FEED_URL(mirror["id"]))
    await worker
    assert response.status_code == 200
    assert len(parse(response.content).find("channel").findall("item")) == 2
    jobs = await refresh_jobs(container, mirror["id"])
    pulled = [j for j in jobs if j.trigger == JobTrigger.feed_fetch]  # type: ignore[attr-defined]
    assert len(pulled) == 1 and pulled[0].status == JobStatus.succeeded  # type: ignore[attr-defined]


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
