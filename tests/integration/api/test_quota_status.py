"""``GET /api/status`` in users mode: the caller's Storage (quota) and the Object store.

A Member gets their own quota as the ``quota`` component (no lines about anyone else)
and an Object store component without its instance-wide facts; an Admin gets one line
per User, removed Owners that still own Feeds included.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from copycast.app import Container
from copycast.settings import Settings
from tests.integration.conftest import settings_with
from tests.support.paths import STATUS_PATH
from tests.support.users import USERS, user_entry
from tests.support.world import World

pytestmark = pytest.mark.integration


@pytest.fixture
def users_settings(settings: Settings, db: str) -> Settings:
    """The world's users, alice with a 1 MB quota, everyone else the 10 GB default."""
    entries = [
        user_entry(USERS["root"]),
        user_entry(USERS["alice"], quota="1 MB"),
        user_entry(USERS["bob"]),
    ]
    return settings_with(settings, auth={"users": entries, "default_quota": "10 GB"})


async def components(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    response = await client.get(STATUS_PATH)
    assert response.status_code == 200, response.text
    return {c["id"]: c for c in response.json()["components"]}


async def test_a_member_sees_their_own_storage_only(
    world: World, alice_client: httpx.AsyncClient
) -> None:
    found = await components(alice_client)
    mine = found["quota"]
    assert (mine["name"], mine["state"], mine["facts"]) == ("Storage", "ok", [])
    assert mine["meter"] == {"used_bytes": 0, "quota_bytes": 1_000_000, "ratio": 0.0}
    assert mine["summary"] == "0 B of 1.0 MB (0 %)"
    store = found["storage"]
    assert store["name"] == "Object store"
    labels = {fact["label"] for fact in store["facts"]}
    assert labels.isdisjoint({"Bucket", "Endpoint", "Used by Feeds"})


async def test_an_admin_sees_every_user_and_removed_owners(
    world: World,
    root_client: httpx.AsyncClient,
    bob_client: httpx.AsyncClient,
    users_container: Container,
) -> None:
    await users_container.services.ensure_default_bucket("gone")
    found = await components(root_client)
    quota = found["quota"]
    assert quota["meter"]["quota_bytes"] == 10_000_000_000
    lines = {fact["label"]: fact for fact in quota["facts"]}
    assert list(lines) == ["root", "alice", "bob", "gone (removed)"]
    assert lines["alice"]["meter"]["quota_bytes"] == 1_000_000
    assert lines["bob"]["meter"]["used_bytes"] > 0, "Bob's archived item counts"
    assert all(fact["state"] == "ok" for fact in quota["facts"])
    store_labels = {fact["label"] for fact in found["storage"]["facts"]}
    assert {"Bucket", "Used by Feeds"} <= store_labels

    bob = (await components(bob_client))["quota"]
    assert bob["facts"] == [] and bob["meter"]["used_bytes"] == lines["bob"]["meter"]["used_bytes"]
