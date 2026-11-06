"""Mirror ids in users mode: every new Mirror and Capture is salted with its Owner (spec §3.2).

``owned_mirror_id(owner, key)`` for every new Mirror, whether or not another User mirrors
the Source, so an id reveals nothing about anyone else; ``FeedExists`` only for the
actor's own duplicate; a hash collision with a Feed the actor cannot see is a bare 409.
Open and legacy mode keep ``mirror_id(key)``, byte for byte as 2.3.0.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from copycast.adapters.db.uow import uow_of
from copycast.app import PLAYLISTS_URL, Container
from copycast.domain.ids import mirror_id, owned_mirror_id
from copycast.domain.urls import normalize_source_url
from copycast.settings import Settings
from tests.integration.conftest import running, settings_with
from tests.integration.storage.conftest import mirror_row
from tests.support.factories import listing, listing_item
from tests.support.fake_engine import FakeEngine
from tests.support.origin import Origin
from tests.support.paths import api
from tests.support.users import login
from tests.support.world import BOB_RSS_PATH, World, signed_in

pytestmark = pytest.mark.integration

WATCH_LATER = "https://www.youtube.com/playlist?list=WL"


async def create(client: httpx.AsyncClient, url: str, **extra: Any) -> httpx.Response:
    return await client.post(
        api("/mirrors"), json={"source_url": url, "backfill": {"mode": "new"}, **extra}
    )


async def test_alice_mirrors_bobs_source_under_her_own_id(
    world: World, alice_client: httpx.AsyncClient, bob_client: httpx.AsyncClient
) -> None:
    key = normalize_source_url(world.source_url)
    assert world.mirror_id == owned_mirror_id("bob", key)
    response = await create(alice_client, world.source_url)
    assert response.status_code == 201, response.text
    assert response.json()["id"] == owned_mirror_id("alice", key) != world.mirror_id
    assert response.json()["owner"] == "alice"
    # Each sees their own Mirror of the one Source, never the other's.
    for client, mine, theirs in (
        (alice_client, owned_mirror_id("alice", key), world.mirror_id),
        (bob_client, world.mirror_id, owned_mirror_id("alice", key)),
    ):
        ids = {feed["id"] for feed in (await client.get(api("/feeds"))).json()["feeds"]}
        assert mine in ids and theirs not in ids


async def test_alices_id_does_not_depend_on_anyone_else(
    users_client: httpx.AsyncClient, origin: Origin
) -> None:
    """The world above has Bob mirroring the Source; here nobody does: the same id."""
    url = origin.url_for(BOB_RSS_PATH)
    await login(users_client, "alice")
    response = await create(users_client, url)
    assert response.status_code == 201, response.text
    assert response.json()["id"] == owned_mirror_id("alice", normalize_source_url(url))


async def test_alices_duplicate_names_her_own_mirror_only(
    world: World, alice_client: httpx.AsyncClient
) -> None:
    first = await create(alice_client, world.source_url)
    assert first.status_code == 201
    again = await create(alice_client, world.source_url)
    assert again.status_code == 409, again.text
    body = again.json()
    assert body["type"].endswith("feed-exists")
    assert body["existing_feed_id"] == first.json()["id"]
    assert world.mirror_id not in again.text


async def test_a_collision_with_a_hidden_feed_is_a_bare_conflict(
    world: World, users_container: Container, alice_client: httpx.AsyncClient
) -> None:
    key = normalize_source_url(world.source_url)
    taken = owned_mirror_id("alice", key)
    async with uow_of(users_container.uow_factory()) as uow:
        await uow.feeds.add(mirror_row("https://elsewhere.example/feed.xml", id=taken, owner="bob"))
    response = await create(alice_client, world.source_url)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["type"].endswith(":conflict") and body["detail"] == "feed id collision"
    assert taken not in response.text and "bob" not in response.text


async def test_retargeting_to_a_source_another_user_mirrors_keeps_the_id(
    world: World, alice_client: httpx.AsyncClient, origin: Origin
) -> None:
    own_url = origin.url_for("rss/edge_cases.xml")
    created = await create(alice_client, own_url)
    assert created.status_code == 201, created.text
    feed_id = created.json()["id"]
    retargeted = await alice_client.patch(
        api(f"/mirrors/{feed_id}"), json={"source_url": world.source_url}
    )
    assert retargeted.status_code == 200, retargeted.text
    assert retargeted.json()["id"] == feed_id
    assert retargeted.json()["source_url"] == world.source_url


async def test_captures_are_salted_and_marked_for_their_owner_only(
    world: World,
    users_container: Container,
    alice_client: httpx.AsyncClient,
    bob_client: httpx.AsyncClient,
    root_client: httpx.AsyncClient,
) -> None:
    engine = users_container.engine
    assert isinstance(engine, FakeEngine)
    engine.script_listing(
        WATCH_LATER, listing(1, service="YouTube", title="Watch later", raw={"_type": "playlist"})
    )
    engine.script_listing(
        PLAYLISTS_URL,
        listing(
            1,
            service="YouTube",
            title="Playlists",
            items=[listing_item(1, key="YoutubeTab:PLx", title="Talks", source_url=None)],
        ),
    )
    capture = {"sync_deletions": True, "playlist_capture": True}
    ids: dict[str, str] = {}
    for name, client in (("alice", alice_client), ("bob", bob_client)):
        response = await client.post(api("/mirrors"), json={"source_url": WATCH_LATER, **capture})
        assert response.status_code == 201, response.text
        ids[name] = response.json()["id"]
        assert ids[name] == owned_mirror_id(name, normalize_source_url(WATCH_LATER))
    assert ids["alice"] != ids["bob"]
    for name, client in (("alice", alice_client), ("bob", bob_client), ("root", root_client)):
        playlists = (await client.get(api("/youtube/playlists"))).json()["playlists"]
        marked = next(p for p in playlists if p["id"] == "WL")["captured_feed_id"]
        assert marked == ids.get(name), name
    assert not world.leaks(str((await alice_client.get(api("/youtube/playlists"))).json()))


# --------------------------------------------------------------------------- open and legacy


async def test_open_mode_keeps_unsalted_ids(client: httpx.AsyncClient, origin: Origin) -> None:
    url = origin.url_for(BOB_RSS_PATH)
    response = await create(client, url)
    assert response.status_code == 201, response.text
    assert response.json()["id"] == mirror_id(normalize_source_url(url))


async def test_legacy_mode_keeps_unsalted_ids(
    settings: Settings, db: str, engine: FakeEngine, origin: Origin
) -> None:
    del db
    legacy = settings_with(settings, auth={"username": "Florent", "password": "legacy-password"})
    url = origin.url_for(BOB_RSS_PATH)
    async with running(legacy, engine) as (_, http):
        await login(http, "florent", "legacy-password")
        response = await create(http, url)
    assert response.status_code == 201, response.text
    assert response.json()["id"] == mirror_id(normalize_source_url(url))
    assert response.json()["owner"] == "florent"


async def test_a_second_user_signed_in_later_still_gets_a_salted_id(
    world: World, users_client_factory: Any
) -> None:
    async with signed_in(users_client_factory, "root") as root:
        response = await create(root, world.source_url)
    assert response.status_code == 201, response.text
    assert response.json()["id"] == owned_mirror_id("root", normalize_source_url(world.source_url))
