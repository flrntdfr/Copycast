"""Every Engine call carries the right User's cookie jar (§1.15, §3.6, D5).

A Feed's Refresh, archive, Request expansion, metadata fetch and Light Refresh carry its
owner's jar, also when an Admin acts on it; a probe, a search and the playlists listing
carry the acting User's. A probe made with one User's jar is never reused for another.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from copycast.app import PLAYLISTS_URL, Container
from copycast.worker.runner import Runner
from tests.support.factories import listing
from tests.support.fake_engine import FakeEngine
from tests.support.paths import api
from tests.support.world import BOB_REQUEST_URL, World

pytestmark = pytest.mark.integration

CHANNEL = "https://www.youtube.com/@bobschannel/videos"


def _engine(container: Container) -> FakeEngine:
    engine = container.engine
    assert isinstance(engine, FakeEngine)
    return engine


def _runner(container: Container) -> Runner:
    return Runner(
        container,
        worker_id="cookies-worker",
        concurrency=1,
        listen=False,
        claim_tick=0.05,
        monitor_tick=0.05,
    )


def _jars(engine: FakeEngine) -> set[str | None]:
    return {r.cookie_jar for r in engine.records.listings} | {
        r.cookie_jar for r in engine.records.fetches
    }


async def test_a_feeds_engine_calls_carry_its_owners_jar(
    world: World,
    users_container: Container,
    bob_client: httpx.AsyncClient,
    root_client: httpx.AsyncClient,
) -> None:
    container = users_container
    engine = _engine(container)
    bobs_jar = container.layout.user_cookies_key("bob")
    assert bobs_jar == "users/bob/cookies.txt"

    # The world's Request expansion and archive ran for Bob's Local feed.
    expanded = [r for r in engine.records.listings if r.url == BOB_REQUEST_URL]
    assert expanded and {r.cookie_jar for r in expanded} == {bobs_jar}
    assert engine.records.fetches and {r.cookie_jar for r in engine.records.fetches} == {bobs_jar}

    # Bob's own Mirror: its probe, first Refresh and archives.
    engine.records.listings.clear()
    engine.records.fetches.clear()
    engine.script_listing(CHANNEL, listing(2, service="YouTube", title="Bob's channel"))
    created = await bob_client.post(
        api("/mirrors"), json={"source_url": CHANNEL, "backfill": {"mode": "all"}}
    )
    assert created.status_code == 201, created.text
    mirror_id = created.json()["id"]
    await _runner(container).run_until_idle()
    assert engine.records.listings and engine.records.fetches
    assert _jars(engine) == {bobs_jar}

    # The Admin acts on Bob's Mirror: a manual Refresh, a metadata fetch. Bob's jar still.
    engine.records.listings.clear()
    engine.records.fetches.clear()
    refreshed = await root_client.post(api(f"/mirrors/{mirror_id}/refresh"))
    assert refreshed.status_code in (200, 202), refreshed.text
    await _runner(container).run_until_idle()
    items = (await root_client.get(api(f"/feeds/{mirror_id}/items"))).json()["items"]
    for n in (1, 2):  # the listing's items' pages (listing_item's source_url)
        engine.script_listing(f"https://podcast.example/episodes/{n}", listing(1))
    fetched = await root_client.post(api(f"/feeds/{mirror_id}/items/{items[0]['id']}/metadata"))
    assert fetched.status_code == 200, fetched.text
    assert engine.records.listings
    assert _jars(engine) == {bobs_jar}

    # A Light Refresh (a podcast app's fetch of the Mirror Feed) lists with Bob's jar too.
    ago = datetime.now(UTC) - timedelta(hours=1)
    async with container.uow_factory() as unit:
        await unit.feeds.set_refresh_attempt(mirror_id, ago)
        await unit.feeds.set_light_refresh_at(mirror_id, ago)
    engine.records.listings.clear()
    await container.services.light_refresh(mirror_id)
    shallow = engine.records.listings[-1]
    assert shallow.url == CHANNEL and shallow.limit is not None
    assert shallow.cookie_jar == bobs_jar


async def test_probes_searches_and_playlists_carry_the_acting_users_jar(
    world: World,
    users_container: Container,
    alice_client: httpx.AsyncClient,
    root_client: httpx.AsyncClient,
) -> None:
    del world
    engine = _engine(users_container)
    layout = users_container.layout
    engine.records.fetches.clear()
    engine.script_listing(CHANNEL, listing(2, service="YouTube", title="A channel"))
    engine.script_listing("ytsearch5:wwdc", listing(1, service="YouTube", title="search"))
    engine.script_listing(PLAYLISTS_URL, listing(0, service="YouTube", title="Playlists"))

    for client, user in ((alice_client, "alice"), (root_client, "root")):
        engine.records.listings.clear()
        probed = await client.post(api("/probe"), json={"url": CHANNEL})
        assert probed.status_code == 200, probed.text
        searched = await client.get(api("/search/videos"), params={"query": "wwdc", "limit": 5})
        assert searched.status_code == 200, searched.text
        playlists = await client.get(api("/youtube/playlists"))
        assert playlists.status_code == 200, playlists.text
        urls = [r.url for r in engine.records.listings]
        assert urls == [CHANNEL, "ytsearch5:wwdc", PLAYLISTS_URL]
        assert _jars(engine) == {layout.user_cookies_key(user)}


async def test_a_probe_is_never_reused_for_another_user(
    world: World,
    users_container: Container,
    alice_client: httpx.AsyncClient,
    bob_client: httpx.AsyncClient,
) -> None:
    del world
    engine = _engine(users_container)
    layout = users_container.layout
    engine.script_listing(CHANNEL, listing(2, service="YouTube", title="Shared channel"))
    engine.records.listings.clear()

    probed = await alice_client.post(api("/probe"), json={"url": CHANNEL})
    assert probed.status_code == 200, probed.text
    token = probed.json()["candidates"][0]["candidate_token"]
    assert [r.cookie_jar for r in engine.records.listings] == [layout.user_cookies_key("alice")]

    gateway = users_container.sources
    assert gateway.cached(token=token, url=None, owner="alice") is not None
    assert gateway.cached(token=token, url=None, owner="bob") is None
    assert gateway.cached(token=None, url=CHANNEL, owner="bob") is None

    # Bob creates a Mirror with Alice's token: no reuse, a fresh probe with Bob's jar.
    engine.records.listings.clear()
    created = await bob_client.post(
        api("/mirrors"),
        json={"source_url": CHANNEL, "candidate_token": token, "backfill": {"mode": "new"}},
    )
    assert created.status_code == 201, created.text
    assert [r.cookie_jar for r in engine.records.listings] == [layout.user_cookies_key("bob")]
