"""First plays and quotas: a podcast app gets 507 at once, never a 50 s wait or a Retry-After.

An Ephemeral (Automatic) feed archives an Episode when a podcast app first asks for
it. When the Owner is Over quota, ``archive_item`` refuses before anything is queued;
when the archive job blocks the item because the Engine's estimate does not fit, its
``ItemEvent`` wakes the wait, which answers 507 right away.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from copycast.adapters.api.routes import public
from copycast.app import Container
from copycast.domain.enums import JobKind
from copycast.worker.runner import Runner
from tests.integration.api.conftest import Source, create_mirror, items_of
from tests.integration.worker.conftest import jobs_of
from tests.integration.worker.test_quota_blocking import set_quota, use_bytes
from tests.support.factories import listing
from tests.support.fake_engine import FakeEngine

pytestmark = pytest.mark.integration

PATIENCE_SECONDS = 10.0
CHANNEL = "https://www.youtube.com/@ondemand/videos"


def local(url: str) -> str:
    return url.removeprefix("http://testserver")


def over_quota(response: httpx.Response) -> dict[str, object]:
    """A bare 507: no Retry-After, and nothing of the Owner's usage or quota, which a
    Feed's pair or media key must not open (it reaches one Feed, not the account)."""
    assert response.status_code == 507, response.text
    assert "retry-after" not in response.headers
    assert "www-authenticate" not in response.headers
    problem: dict[str, object] = response.json()
    assert problem["type"] == "urn:copycast:problem:over-quota"
    assert "used_bytes" not in problem and "quota_bytes" not in problem
    assert problem["detail"] == public.FEED_OVER_QUOTA_DETAIL
    return problem


async def test_the_first_play_of_an_over_quota_owner_is_507_at_once(
    client: httpx.AsyncClient,
    source: Source,
    runner: Runner,
    container: Container,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(public, "ON_DEMAND_POLL_SECONDS", 30.0)
    url = source.write_rss("ondemand", items=[1], artwork=False)
    mirror = await create_mirror(client, url, mode="automatic")
    await runner.run_until_idle()
    (item,) = await items_of(client, mirror["id"])
    assert item["media"] is None
    set_quota(container, 1000)
    await use_bytes(container, 2000)

    started = time.monotonic()
    for method in ("GET", "HEAD"):
        response = await client.request(method, local(item["public_media_url"]))
        assert response.status_code == 507
        if method == "GET":
            over_quota(response)
    assert time.monotonic() - started < PATIENCE_SECONDS
    again = (await items_of(client, mirror["id"]))[0]
    assert again["state"] != "wanted", "nothing was queued"
    assert await jobs_of(container, mirror["id"], kind=JobKind.archive_item) == []


async def test_a_first_play_the_job_blocks_during_the_wait_is_507_at_once(
    client: httpx.AsyncClient,
    runner: Runner,
    container: Container,
    engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(public, "ON_DEMAND_POLL_SECONDS", 30.0)
    engine.script_listing(CHANNEL, listing(1, service="YouTube", title="On demand"))
    mirror = await create_mirror(client, CHANNEL, mode="automatic")
    await runner.run_until_idle()
    (item,) = await items_of(client, mirror["id"])
    assert item["media"] is None
    set_quota(container, 10_000_000)
    await use_bytes(container, 1_000_000)
    engine.estimated_bytes = 50_000_000

    task = asyncio.create_task(runner.run())
    try:
        started = time.monotonic()
        response = await client.get(local(item["public_media_url"]))
        assert time.monotonic() - started < PATIENCE_SECONDS, "woken by the item's event"
        over_quota(response)
    finally:
        runner.request_stop()
        await asyncio.wait_for(task, timeout=15)
    blocked = (await items_of(client, mirror["id"]))[0]
    assert blocked["blocked_reason"] == "quota" and blocked["media"] is None
    (fetch,) = engine.records.fetches
    assert fetch.spec.max_bytes == 9_000_000
