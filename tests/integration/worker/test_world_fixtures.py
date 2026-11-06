"""The users-mode fixtures and the world reach this suite (``tests/integration/conftest.py``)."""

from __future__ import annotations

import httpx
import pytest

from tests.support.paths import api
from tests.support.world import World

pytestmark = pytest.mark.integration


async def test_the_world_and_its_clients_are_available_here(
    world: World, alice_client: httpx.AsyncClient, bob_client: httpx.AsyncClient
) -> None:
    assert (await alice_client.get(api("/me"))).json()["username"] == "alice"
    feeds = (await bob_client.get(api("/feeds"))).json()["feeds"]
    assert world.mirror_id in {feed["id"] for feed in feeds}
