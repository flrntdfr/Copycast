"""Manual archives and quotas: the manual entry points refuse an Over quota Owner (507).

``archive_item``, ``archive_available``, ``retry_failed`` and ``select_items`` (unless a
dry run) answer ``507 over-quota`` and change nothing; Backfill, Follow, policy
switches and a podcast app's fetch of the feed (Light Refresh) queue as usual, and the
archive job blocks what does not fit. A manual archive of a blocked item lifts its
block once there is room.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from copycast.app import Container
from copycast.domain.enums import JobKind
from copycast.worker.runner import Runner
from tests.integration.api.conftest import Source, create_mirror, items_of
from tests.integration.worker.conftest import jobs_of
from tests.integration.worker.test_quota_blocking import set_quota, use_bytes
from tests.support.paths import FEED_URL, api

pytestmark = pytest.mark.integration


def over_quota(response: httpx.Response, *, used: int, quota: int) -> None:
    assert response.status_code == 507, response.text
    problem: dict[str, Any] = response.json()
    assert problem["type"] == "urn:copycast:problem:over-quota"
    assert (problem["used_bytes"], problem["quota_bytes"]) == (used, quota)
    assert "retry-after" not in response.headers


async def outside_cooldown(container: Container, feed_id: str) -> None:
    ago = datetime.now(UTC) - timedelta(hours=1)
    async with container.uow_factory() as uow:
        await uow.feeds.set_refresh_attempt(feed_id, ago)
        await uow.feeds.set_light_refresh_at(feed_id, ago)


async def test_manual_entry_points_refuse_an_over_quota_owner(
    client: httpx.AsyncClient, source: Source, runner: Runner, container: Container
) -> None:
    url = source.write_rss("manual", items=[3, 2, 1], artwork=False)
    mirror = await create_mirror(client, url, mode="new")
    await runner.run_until_idle()
    feed_id = mirror["id"]
    items = await items_of(client, feed_id)
    assert {item["state"] for item in items} == {"available"}
    set_quota(container, 1000)
    await use_bytes(container, 1500)

    first = items[0]["id"]
    over_quota(
        await client.post(api(f"/feeds/{feed_id}/items/{first}/archive")), used=1500, quota=1000
    )
    over_quota(await client.post(api(f"/feeds/{feed_id}/archive-available")), used=1500, quota=1000)
    over_quota(await client.post(api(f"/feeds/{feed_id}/retry-failed")), used=1500, quota=1000)
    over_quota(
        await client.post(api(f"/mirrors/{feed_id}/selections"), json={"selection": "1-2"}),
        used=1500,
        quota=1000,
    )
    dry = await client.post(
        api(f"/mirrors/{feed_id}/selections"), json={"selection": "1-2", "dry_run": True}
    )
    assert dry.status_code == 200, dry.text
    assert dry.json()["dry_run"] is True and len(dry.json()["resolved"]) == 2

    assert {item["state"] for item in await items_of(client, feed_id)} == {"available"}
    assert await jobs_of(container, feed_id, kind=JobKind.archive_item) == []


async def test_backfill_policy_switches_and_feed_fetches_queue_as_usual_while_over(
    client: httpx.AsyncClient, source: Source, runner: Runner, container: Container
) -> None:
    set_quota(container, 1000)
    await use_bytes(container, 1000)
    url = source.write_rss("over", items=[3, 2, 1], artwork=False)
    mirror = await create_mirror(client, url, selection="1-2")
    feed_id = mirror["id"]
    await runner.run_until_idle()
    blocked = [i for i in await items_of(client, feed_id) if i["blocked_reason"] == "quota"]
    assert len(blocked) == 2 and {i["state"] for i in blocked} == {"wanted"}

    switched = await client.patch(
        api(f"/mirrors/{feed_id}"), json={"backfill": {"mode": "selection", "selection": "3"}}
    )
    assert switched.status_code == 200, switched.text
    await runner.run_until_idle()
    states = {i["source_number"]: i for i in await items_of(client, feed_id)}
    assert states[3]["blocked_reason"] == "quota"

    switched = await client.patch(
        api(f"/mirrors/{feed_id}"), json={"backfill": {"mode": "all"}, "follow": True}
    )
    assert switched.status_code == 200, switched.text
    await runner.run_until_idle()
    jobs_before = len(await jobs_of(container, feed_id, kind=JobKind.archive_item))

    # A podcast app's fetch: the Light Refresh adds the new item and queues it alone.
    source.write_rss("over", items=[4, 3, 2, 1], artwork=False)
    await outside_cooldown(container, feed_id)
    response = await client.get(FEED_URL(feed_id))
    assert response.status_code == 200, response.text
    jobs = await jobs_of(container, feed_id, kind=JobKind.archive_item)
    assert len(jobs) == jobs_before + 1
    states = {i["source_number"]: i for i in await items_of(client, feed_id)}
    assert states[4]["state"] == "wanted" and states[4]["blocked_reason"] is None
    await runner.run_until_idle()
    states = {i["source_number"]: i for i in await items_of(client, feed_id)}
    assert {i["blocked_reason"] for i in states.values()} == {"quota"}


async def test_a_manual_archive_lifts_the_block_once_there_is_room(
    client: httpx.AsyncClient, source: Source, runner: Runner, container: Container
) -> None:
    set_quota(container, 10_000_000)
    await use_bytes(container, 10_000_000)
    url = source.write_rss("lift", items=[2, 1], artwork=False)
    mirror = await create_mirror(client, url, mode="all")
    feed_id = mirror["id"]
    await runner.run_until_idle()
    items = await items_of(client, feed_id)
    assert {i["blocked_reason"] for i in items} == {"quota"}

    set_quota(container, 20_000_000)
    target = items[0]["id"]
    response = await client.post(api(f"/feeds/{feed_id}/items/{target}/archive"))
    assert response.status_code == 202, response.text
    item = (await client.get(api(f"/feeds/{feed_id}/items/{target}"))).json()
    assert item["blocked_reason"] is None and item["state"] == "wanted"
    await runner.run_until_idle()
    states = {i["id"]: i for i in await items_of(client, feed_id)}
    assert states[target]["state"] == "archived"
    other = next(i for i in states.values() if i["id"] != target)
    assert other["blocked_reason"] == "quota", "only the item asked for was released"
