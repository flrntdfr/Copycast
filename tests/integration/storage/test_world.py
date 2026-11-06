"""The world's rows stay in the database for repository tests (``tests.support.world``)."""

from __future__ import annotations

import pytest

from copycast.adapters.db.uow import UnitOfWorkFactory
from copycast.application.services.context import Scope
from tests.support.world import World

pytestmark = pytest.mark.integration


async def test_the_world_is_in_the_database(world: World, uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory(Scope("alice", "alice")) as uow:
        assert uow.scope == Scope("alice", "alice")
        mirror = await uow.feeds.by_dedup_key(
            "bob", (await uow.feeds.require(world.mirror_id)).source_dedup_key or ""
        )
        assert mirror is not None and mirror.id == world.mirror_id
        assert (await uow.feeds.default_bucket("alice")) is not None
        assert {u.username for u in await uow.users.all()} >= {"root", "alice", "bob"}
        item = await uow.catalog.require(world.item_id, world.bucket_id)
        assert item.archive_state == "archived"
