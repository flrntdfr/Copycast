"""SSE scoping: each stream carries its Principal's own events (an Admin's, everyone's).

The NOTIFY payload names the event's Owner (``uow.publish`` looks it up: the Feed's
Owner, else a feed-less job's requester); the EventHub hands an event to its Owner,
Admins and SYSTEM, and ``resync`` to everyone. ``?feed_id=`` of a Feed the Principal
cannot see is 404.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from copycast.adapters.api.events import Envelope, EventHub, Subscription, event_feed_id
from copycast.app import Container
from copycast.domain.enums import PrincipalVia
from copycast.domain.users import Principal
from copycast.worker.runner import Runner
from tests.integration.api.test_events import parse_frames
from tests.support.factories import EPOCH, listing, listing_item
from tests.support.fake_engine import FakeEngine
from tests.support.paths import EVENTS_PATH
from tests.support.world import World

pytestmark = pytest.mark.integration

BOB_SECOND_URL = "https://www.youtube.com/watch?v=bobsecond01"


def hub_of(app: Any) -> EventHub:
    hub: EventHub = app.state.event_hub
    return hub


def principal(container: Container, username: str) -> Principal:
    found = container.users.principal(username, via=PrincipalVia.session)
    assert found is not None
    return found


async def drain(subscription: Subscription, *, quiet: float = 0.5) -> list[Envelope]:
    """Everything delivered until nothing arrives for ``quiet`` seconds."""
    received: list[Envelope] = []
    while (envelope := await subscription.next(quiet)) is not None:
        received.append(envelope)
    return received


def feed_ids(envelopes: list[Envelope]) -> set[str | None]:
    return {event_feed_id(envelope.event) for envelope in envelopes if not envelope.is_resync}


async def bob_archives_another_video(
    container: Container, client: httpx.AsyncClient, world: World
) -> None:
    """Bob pushes a second video into his Local feed and the worker archives it."""
    engine = container.engine
    assert isinstance(engine, FakeEngine)
    engine.script_listing(
        BOB_SECOND_URL,
        listing(
            1,
            service="YouTube",
            title="Bob's channel",
            items=[
                listing_item(
                    1,
                    key="youtube:bobsecond01",
                    title="Bob's second video",
                    source_url=BOB_SECOND_URL,
                    published_at=EPOCH,
                )
            ],
        ),
    )
    response = await client.post(
        f"/api/buckets/{world.bucket_id}/requests", json={"url": BOB_SECOND_URL}
    )
    assert response.status_code == 202, response.text
    runner = Runner(
        container, worker_id="events-worker", concurrency=1, listen=False, claim_tick=0.05
    )
    await runner.run_until_idle()


async def test_each_stream_carries_its_principals_own_events(
    world: World,
    users_app: Any,
    users_container: Container,
    alice_client: httpx.AsyncClient,
    bob_client: httpx.AsyncClient,
    root_client: httpx.AsyncClient,
) -> None:
    hub = hub_of(users_app)
    assert await hub.wait_connected()
    alice_default = world.defaults["alice"]
    with (
        hub.subscribe(principal(users_container, "alice")) as alice,
        hub.subscribe(principal(users_container, "bob")) as bob,
        hub.subscribe(principal(users_container, "root")) as root,
    ):
        await bob_archives_another_video(users_container, bob_client, world)
        renamed = await alice_client.patch(
            f"/api/buckets/{alice_default}", json={"name": "Alice's"}
        )
        assert renamed.status_code == 200, renamed.text
        # A feed-less job belongs to whoever requested it (root's rebuild): Admins only here.
        rebuild = await root_client.post("/api/admin/rebuild", json={"dry_run": True})
        assert rebuild.status_code in (200, 201, 202), rebuild.text
        alices, bobs, roots = await drain(alice), await drain(bob), await drain(root)

    names = {envelope.name for envelope in bobs}
    assert {"request", "job", "item", "feed"} <= names, names
    assert all(envelope.owner == "bob" for envelope in bobs), "bob gets his own only"
    # The worker also ran his Mirror's queued first Refresh.
    assert world.bucket_id in feed_ids(bobs) <= {world.bucket_id, world.mirror_id}

    assert alices, "alice gets her own events"
    assert all(envelope.owner == "alice" for envelope in alices)
    assert feed_ids(alices) == {alice_default}

    owners = {envelope.owner for envelope in roots}
    assert {"alice", "bob", "root"} <= owners
    assert {world.bucket_id, alice_default} <= feed_ids(roots)
    rebuild_jobs = [e for e in roots if e.name == "job" and event_feed_id(e.event) is None]
    assert rebuild_jobs and rebuild_jobs[0].owner == "root"


async def test_replay_hands_back_only_the_principals_own_events(
    world: World,
    users_app: Any,
    users_container: Container,
    bob_client: httpx.AsyncClient,
) -> None:
    hub = hub_of(users_app)
    assert await hub.wait_connected()
    with hub.subscribe(principal(users_container, "bob")) as bob:
        await bob_archives_another_video(users_container, bob_client, world)
        assert await drain(bob)
    with hub.subscribe(principal(users_container, "alice"), last_event_id=0) as replay:
        replayed = await drain(replay, quiet=0.2)
    assert all(envelope.owner == "alice" or envelope.is_resync for envelope in replayed)
    with hub.subscribe(principal(users_container, "root"), last_event_id=0) as replay:
        assert world.bucket_id in feed_ids(await drain(replay, quiet=0.2))


async def test_a_hidden_feed_filter_is_404_and_the_streams_never_leak(
    world: World,
    users_app: Any,
    users_container: Container,
    alice_client: httpx.AsyncClient,
    bob_client: httpx.AsyncClient,
    root_client: httpx.AsyncClient,
) -> None:
    hub = hub_of(users_app)
    assert await hub.wait_connected()
    hidden = await alice_client.get(EVENTS_PATH, params={"feed_id": world.mirror_id})
    assert hidden.status_code == 404
    assert hidden.json()["type"].endswith("not-found")
    ghost = await alice_client.get(EVENTS_PATH, params={"feed_id": "0123456789abcdef"})
    assert (
        ghost.status_code == 404
        and ghost.json()["detail"].replace("0123456789abcdef", world.mirror_id)
        == hidden.json()["detail"]
    )

    async def stream(client: httpx.AsyncClient, **params: str) -> httpx.Response:
        return await client.get(EVENTS_PATH, params=params)

    alice_task = asyncio.create_task(stream(alice_client))
    root_task = asyncio.create_task(stream(root_client, feed_id=world.bucket_id))
    await asyncio.sleep(0.3)
    assert hub.subscriber_count == 2
    await bob_archives_another_video(users_container, bob_client, world)
    await asyncio.sleep(0.5)
    for subscription in list(hub._subscribers):
        subscription.close()
    alice_response = await asyncio.wait_for(alice_task, 10)
    root_response = await asyncio.wait_for(root_task, 10)
    assert alice_response.status_code == 200 and root_response.status_code == 200
    assert not world.leaks(alice_response.text)
    assert "Bob's second video" not in alice_response.text
    root_frames = [f for f in parse_frames(root_response.text) if "event" in f]
    assert root_frames and {f["event"] for f in root_frames} >= {"request", "item"}


async def test_keys_cannot_open_the_event_stream(
    world: World, users_client: httpx.AsyncClient
) -> None:
    """``subscribe_events`` is no tool: an API key on ``/api/events`` is refused."""
    response = await users_client.get(
        EVENTS_PATH, headers={"Authorization": f"Bearer {world.key_secret}"}
    )
    assert response.status_code == 403


async def test_a_deleted_feeds_event_still_reaches_its_owner_only(
    users_app: Any,
    users_container: Container,
    bob_client: httpx.AsyncClient,
) -> None:
    """``delete_feed`` publishes after the delete, with the Owner it read before."""
    hub = hub_of(users_app)
    assert await hub.wait_connected()
    created = await bob_client.post("/api/buckets", json={"name": "Short-lived"})
    assert created.status_code == 201, created.text
    feed_id = created.json()["id"]
    with (
        hub.subscribe(principal(users_container, "alice")) as alice,
        hub.subscribe(principal(users_container, "bob")) as bob,
    ):
        deleted = await bob_client.delete(f"/api/feeds/{feed_id}")
        assert deleted.status_code == 204, deleted.text
        bobs, alices = await drain(bob), await drain(alice, quiet=0.2)
    gone = [e for e in bobs if e.name == "feed" and getattr(e.event, "reason", "") == "deleted"]
    assert gone and gone[0].owner == "bob" and event_feed_id(gone[0].event) == feed_id
    assert not alices
