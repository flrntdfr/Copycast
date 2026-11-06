"""``storage_summary`` counts the caller's own Feeds only, an Admin's too (spec A27)."""

from __future__ import annotations

import httpx
import pytest

from copycast.app import Container
from copycast.domain.users import Principal
from tests.support.paths import api
from tests.support.world import World

pytestmark = pytest.mark.integration


async def test_storage_summary_covers_the_callers_own_feeds_only(
    world: World,
    users_container: Container,
    root_client: httpx.AsyncClient,
    bob_client: httpx.AsyncClient,
) -> None:
    bob_feeds = {world.mirror_id, world.bucket_id, world.defaults["bob"]}

    mine = (await bob_client.get(api("/storage"))).json()
    assert {feed["feed_id"] for feed in mine["feeds"]} == bob_feeds
    archived = next(feed for feed in mine["feeds"] if feed["feed_id"] == world.bucket_id)
    assert archived["file_count"] > 0 and mine["total_bytes"] > 0

    # The Admin sees Bob's Feeds everywhere else, but this is root's own storage.
    root = (await root_client.get(api("/storage"))).json()
    assert {feed["feed_id"] for feed in root["feeds"]} == {world.defaults["root"]}
    (own,) = root["feeds"]
    assert (root["file_count"], root["total_bytes"]) == (own["file_count"], own["total_bytes"])
    assert world.leaks(str(root)) == []

    # SYSTEM (no User) still counts every Feed.
    everything = await users_container.services_for(Principal.SYSTEM).storage_summary()
    assert bob_feeds | {world.defaults["root"], world.defaults["alice"]} <= {
        feed.feed_id for feed in everything.feeds
    }
