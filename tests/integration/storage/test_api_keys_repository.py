"""``ApiKeyRepository``: add, look up by digest, list newest first, touch, delete."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from copycast.adapters.db.uow import UnitOfWorkFactory
from copycast.domain.credentials import api_key_hash, api_key_prefix, new_api_key
from copycast.domain.enums import KeyScope
from copycast.domain.exceptions import NotFound

pytestmark = pytest.mark.integration


async def test_api_key_repository(uow_factory: UnitOfWorkFactory) -> None:
    first, second = new_api_key(), new_api_key()
    # Two transactions: ``created_at`` is the transaction time, and the list is newest first.
    async with uow_factory() as uow:
        a = await uow.api_keys.add(
            name="laptop",
            scope=KeyScope.read,
            key_hash=api_key_hash(first),
            prefix=api_key_prefix(first),
        )
        a_id = a.id
    async with uow_factory() as uow:
        b = await uow.api_keys.add(
            name="server",
            scope=KeyScope.full,
            key_hash=api_key_hash(second),
            prefix=api_key_prefix(second),
        )
        b_id = b.id
    async with uow_factory() as uow:
        rows = await uow.api_keys.list()
        assert [row.name for row in rows] == ["server", "laptop"]
        found = await uow.api_keys.by_hash(api_key_hash(first))
        assert found is not None and found.id == a_id and found.scope == "read"
        assert found.prefix == first[:12] and found.last_used_at is None
        assert await uow.api_keys.by_hash(api_key_hash("cck_nope")) is None
        at = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
        await uow.api_keys.touch(a_id, at)
    async with uow_factory() as uow:
        assert (await uow.api_keys.require(a_id)).last_used_at == at
        assert await uow.api_keys.delete(b_id) is True
        assert await uow.api_keys.delete(b_id) is False
        assert await uow.api_keys.get(b_id) is None
        with pytest.raises(NotFound):
            await uow.api_keys.require(uuid.uuid4())
        assert [row.id for row in await uow.api_keys.list()] == [a_id]


async def test_key_hash_is_unique(uow_factory: UnitOfWorkFactory) -> None:
    secret = new_api_key()
    async with uow_factory() as uow:
        await uow.api_keys.add(
            name="a",
            scope=KeyScope.read,
            key_hash=api_key_hash(secret),
            prefix=api_key_prefix(secret),
        )
    with pytest.raises(Exception, match="uq_api_keys_key_hash"):
        async with uow_factory() as uow:
            await uow.api_keys.add(
                name="b",
                scope=KeyScope.read,
                key_hash=api_key_hash(secret),
                prefix=api_key_prefix(secret),
            )
