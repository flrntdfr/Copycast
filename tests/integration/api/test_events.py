"""The EventHub and ``GET /api/events``: frames, replay, resync, filtering."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from copycast.adapters.api.events import EventHub
from copycast.app import Container
from copycast.application.events import FeedEvent, JobEvent, ResyncEvent, parse
from copycast.application.models import JobRead
from copycast.domain.enums import JobKind, JobStatus, JobTrigger
from copycast.worker.runner import Runner
from tests.integration.api.conftest import Source, create_mirror
from tests.support.paths import EVENTS_PATH

pytestmark = pytest.mark.integration


def parse_frames(body: str) -> list[dict[str, Any]]:
    """SSE text -> [{event, id, data, retry, comment}] (comments kept for the keepalive check)."""
    frames: list[dict[str, Any]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        frame: dict[str, Any] = {}
        for line in block.splitlines():
            if line.startswith(":"):
                frame["comment"] = line[1:].strip()
            elif ":" in line:
                key, _, value = line.partition(":")
                frame[key] = value.strip()
        if "data" in frame:
            frame["data"] = json.loads(frame["data"])
        frames.append(frame)
    return frames


def hub_of(app: FastAPI) -> EventHub:
    hub: EventHub = app.state.event_hub
    return hub


async def test_hub_receives_notify_from_commits(
    app: FastAPI, container: Container, client: httpx.AsyncClient, source: Source
) -> None:
    hub = hub_of(app)
    assert await hub.wait_connected()
    with hub.subscribe() as subscription:
        url = source.write_rss("hub", items=[1], artwork=False)
        mirror = await create_mirror(client, url)
        received: list[Any] = []
        async with asyncio.timeout(5):
            async for envelope in subscription:
                received.append(envelope)
                names = [e.name for e in received]
                if "feed" in names and "job" in names:
                    break
    feed_events = [e.event for e in received if e.name == "feed"]
    assert any(isinstance(e, FeedEvent) and e.feed_id == mirror["id"] for e in feed_events)
    job_events = [e.event for e in received if e.name == "job"]
    assert any(isinstance(e, JobEvent) and e.job.kind is JobKind.refresh for e in job_events)
    assert [e.id for e in received] == sorted(e.id for e in received)


async def test_sse_stream_frames_and_replay(
    app: FastAPI, client: httpx.AsyncClient, source: Source, runner: Runner
) -> None:
    hub = hub_of(app)
    assert await hub.wait_connected()

    async def stream(headers: dict[str, str] | None = None, **params: str) -> str:
        response = await client.get(EVENTS_PATH, params=params, headers=headers)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        return response.text

    # A stream that ends when the hub closes its subscribers (shutdown semantics).
    task = asyncio.create_task(stream())
    await asyncio.sleep(0.2)
    assert hub.subscriber_count == 1
    url = source.write_rss("sse", items=[2, 1], artwork=False)
    mirror = await create_mirror(client, url)
    await runner.run_until_idle()
    await asyncio.sleep(0.3)
    for subscription in list(hub._subscribers):
        subscription.close()
    body = await asyncio.wait_for(task, 10)
    frames = parse_frames(body)
    assert frames[0]["comment"] == "connected" and frames[0]["retry"] == "3000"
    events = [f for f in frames if "event" in f]
    assert [int(f["id"]) for f in events] == sorted(int(f["id"]) for f in events)
    kinds = {f["event"] for f in events}
    assert {"job", "feed", "item"} <= kinds, kinds
    job_frames = [f for f in events if f["event"] == "job"]
    statuses = {f["data"]["data"]["job"]["status"] for f in job_frames}
    assert {"queued", "succeeded"} <= statuses
    assert all(f["data"]["event"] == f["event"] for f in events)
    for frame in events:
        parse(frame["data"])  # every frame round-trips through the shared codec
    feed_ids = {f["data"]["data"].get("feed_id") for f in events if f["event"] == "feed"}
    assert feed_ids == {mirror["id"]}
    progress = [f for f in events if f["event"] == "progress"]
    assert all(f["data"]["data"]["feed_id"] == mirror["id"] for f in progress)

    # Replay from Last-Event-ID: everything after that id, nothing before.
    last = int(events[len(events) // 2]["id"])
    replay_task = asyncio.create_task(stream({"Last-Event-ID": str(last)}))
    await asyncio.sleep(0.2)
    for subscription in list(hub._subscribers):
        subscription.close()
    replayed = [f for f in parse_frames(await asyncio.wait_for(replay_task, 10)) if "event" in f]
    assert [int(f["id"]) for f in replayed] == [int(f["id"]) for f in events if int(f["id"]) > last]

    # A gap (an id older than the buffer or from another process) yields a resync first.
    gap_task = asyncio.create_task(stream({"Last-Event-ID": "999999"}))
    await asyncio.sleep(0.2)
    for subscription in list(hub._subscribers):
        subscription.close()
    gap = [f for f in parse_frames(await asyncio.wait_for(gap_task, 10)) if "event" in f]
    assert gap[0]["event"] == "resync" and gap[0]["data"]["data"]["reason"] == "replay-gap"

    # feed_id filter: only that feed's events (and resync) pass.
    other = await create_mirror(client, source.write_rss("sse2", items=[1], artwork=False))
    filtered_task = asyncio.create_task(stream({"Last-Event-ID": "0"}, feed_id=other["id"]))
    await asyncio.sleep(0.2)
    for subscription in list(hub._subscribers):
        subscription.close()
    filtered = [f for f in parse_frames(await asyncio.wait_for(filtered_task, 10)) if "event" in f]
    assert filtered and all(
        f["event"] == "resync"
        or f["data"]["data"].get("feed_id", f["data"]["data"].get("job", {}).get("feed_id"))
        == other["id"]
        for f in filtered
    )


async def test_hub_replay_buffer_and_lagging_subscriber() -> None:
    hub = EventHub("postgresql://unused", buffer_size=5, queue_size=2)

    def feed_event(n: int) -> FeedEvent:
        return FeedEvent(feed_id="f", revision=n, reason="test")

    for n in range(1, 8):
        hub.publish(feed_event(n))
    assert hub.last_id == 7
    with hub.subscribe(last_event_id=4) as replay:
        # 5, 6, 7 are buffered; 3 fits the queue of 2 only partially -> lagging closes it.
        got = [e async for e in replay]
    assert [e.id for e in got] == [5, 6]
    assert replay.closed and replay.dropped == 1

    with hub.subscribe(last_event_id=1) as gap:
        first = [e async for e in gap]
    assert first[0].name == "resync" and isinstance(first[0].event, ResyncEvent)

    with hub.subscribe(feed_id="other") as filtered:
        hub.publish(feed_event(8))
        hub.publish(FeedEvent(feed_id="other", revision=1, reason="x"))
        hub.publish(ResyncEvent(reason="manual"))
        filtered.close()
        seen = [e.name for e in [e async for e in filtered]]
    assert seen == ["feed", "resync"]
    job = JobRead(
        id="00000000-0000-0000-0000-000000000001",  # type: ignore[arg-type]
        kind=JobKind.refresh,
        feed_id="other",
        trigger=JobTrigger.manual,
        status=JobStatus.queued,
        created_at="2025-01-01T00:00:00Z",  # type: ignore[arg-type]
    )
    with hub.subscribe(feed_id="other") as by_job:
        hub.publish(JobEvent(job=job))
        by_job.close()
        assert [e.name for e in [e async for e in by_job]] == ["job"]
