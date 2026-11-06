"""The isolation world builds what it promises (``tests.support.world``)."""

from __future__ import annotations

import httpx
import pytest

from copycast.adapters.db.uow import uow_of
from copycast.app import Container
from copycast.application.services.engine import cookie_jar_key
from tests.support.paths import api
from tests.support.world import World

pytestmark = pytest.mark.integration


async def test_the_world_holds_bobs_things(
    world: World, users_container: Container, root_client: httpx.AsyncClient
) -> None:
    feeds = {feed["id"]: feed for feed in (await root_client.get(api("/feeds"))).json()["feeds"]}
    assert feeds[world.mirror_id]["owner"] == "bob"
    assert feeds[world.mirror_id]["title"] == world.mirror_title
    assert feeds[world.mirror_id]["source_url"] == world.source_url
    assert feeds[world.bucket_id]["owner"] == "bob"
    assert feeds[world.bucket_id]["name"] == world.bucket_title
    assert {feeds[world.defaults[name]]["owner"] for name in ("root", "alice", "bob")} == {
        "root",
        "alice",
        "bob",
    }
    assert sum(1 for feed in feeds.values() if feed["owner"] == "alice") == 1

    item = (await root_client.get(api(f"/feeds/{world.bucket_id}/items/{world.item_id}"))).json()
    assert item["state"] == "archived" and item["title"] == world.item_title
    assert item["media"]["play_url"].endswith(
        f"/api/feeds/{world.bucket_id}/items/{world.item_id}/media"
    )
    job = (await root_client.get(api(f"/jobs/{world.job_id}"))).json()
    assert job["kind"] == "refresh" and job["status"] == "queued"
    request = (
        await root_client.get(api(f"/buckets/{world.bucket_id}/requests/{world.request_id}"))
    ).json()
    assert request["status"] == "expanded"
    assert world.source_url in world.refresh_error
    assert feeds[world.mirror_id]["last_error"] == world.refresh_error

    async with uow_of(users_container.uow_factory()) as uow:
        assert {user.username for user in await uow.users.all()} >= {"root", "alice", "bob"}
        keys = {key.id: key.owner for key in await uow.api_keys.list()}
    assert str(next(k for k, owner in keys.items() if owner == "bob")) == world.key_id


async def test_each_client_is_signed_in_as_its_user(
    alice_client: httpx.AsyncClient,
    bob_client: httpx.AsyncClient,
    root_client: httpx.AsyncClient,
) -> None:
    for client, name, admin in (
        (alice_client, "alice", False),
        (bob_client, "bob", False),
        (root_client, "root", True),
    ):
        me = (await client.get(api("/me"))).json()
        assert me["username"] == name and me["admin"] is admin


async def test_bobs_key_acts_as_bob(world: World, users_client: httpx.AsyncClient) -> None:
    headers = {"Authorization": f"Bearer {world.key_secret}"}
    feeds = await users_client.get(api("/feeds"), headers=headers)
    assert feeds.status_code == 200
    created = await users_client.post(
        api("/buckets"), json={"name": "From Bob's agent"}, headers=headers
    )
    assert created.status_code == 201 and created.json()["owner"] == "bob"


def test_leaks_finds_bobs_ids_and_titles(world: World) -> None:
    assert world.leaks('{"feeds": []}') == []
    assert world.leaks(f'{{"id": "{world.mirror_id}"}}') == [world.mirror_id]
    assert world.source_url in world.leaks(f"error: {world.refresh_error}")


async def test_every_engine_call_carries_its_owners_cookie_jar(
    world: World, users_container: Container, bob_client: httpx.AsyncClient
) -> None:
    from tests.support.factories import listing
    from tests.support.fake_engine import FakeEngine

    engine = users_container.engine
    assert isinstance(engine, FakeEngine)
    url = "https://www.youtube.com/@bobschannel/videos"
    engine.script_listing(url, listing(2, service="YouTube", title="Bob's channel"))
    probed = await bob_client.post(api("/probe"), json={"url": url})
    assert probed.status_code == 200, probed.text
    # Every call of Bob's carries Bob's jar: the seam, whatever key it names (the global
    # jar until per-user Cookie files exist, users/bob/cookies.txt after).
    jar = cookie_jar_key(users_container.layout, "bob")
    assert engine.records.listings and engine.records.fetches
    assert {record.cookie_jar for record in engine.records.listings} == {jar}
    assert {record.cookie_jar for record in engine.records.fetches} == {jar}
