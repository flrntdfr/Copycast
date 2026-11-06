"""API keys: minted from the UI, looked up by digest when an MCP client connects."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Select, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from copycast.adapters.db.base import rows_affected, utcnow
from copycast.adapters.db.models import ApiKey
from copycast.application.services.context import Scope
from copycast.domain.enums import KeyScope
from copycast.domain.exceptions import NotFound


class ApiKeyRepository:
    """API keys by actor: a User (an Admin too) lists, reads and revokes only their own.

    SYSTEM (``scope.actor`` None) sees every key; ``by_hash`` is unscoped and returns the
    key with its Owner, for the credential check.
    """

    def __init__(self, session: AsyncSession, scope: Scope = Scope.SYSTEM) -> None:
        self._session = session
        self._scope = scope

    def _own[T: tuple[Any, ...]](self, stmt: Select[T]) -> Select[T]:
        actor = self._scope.actor
        return stmt if actor is None else stmt.where(ApiKey.owner == actor)

    async def get(self, key_id: uuid.UUID) -> ApiKey | None:
        if self._scope.actor is None:
            return await self._session.get(ApiKey, key_id)
        result = await self._session.execute(self._own(select(ApiKey).where(ApiKey.id == key_id)))
        return result.scalar_one_or_none()

    async def require(self, key_id: uuid.UUID) -> ApiKey:
        key = await self.get(key_id)
        if key is None:
            raise NotFound("api key", str(key_id))
        return key

    async def by_hash(self, key_hash: str) -> ApiKey | None:
        result = await self._session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        return result.scalar_one_or_none()

    async def list(self) -> list[ApiKey]:
        stmt = self._own(select(ApiKey)).order_by(ApiKey.created_at.desc(), ApiKey.id)
        return list((await self._session.execute(stmt)).scalars())

    async def add(
        self, *, owner: str, name: str, scope: KeyScope, key_hash: str, prefix: str
    ) -> ApiKey:
        key = ApiKey(owner=owner, name=name, scope=scope.value, key_hash=key_hash, prefix=prefix)
        self._session.add(key)
        await self._session.flush()
        return key

    async def delete(self, key_id: uuid.UUID) -> bool:
        stmt = delete(ApiKey).where(ApiKey.id == key_id)
        if self._scope.actor is not None:
            stmt = stmt.where(ApiKey.owner == self._scope.actor)
        result = await self._session.execute(stmt)
        return rows_affected(result) > 0

    async def touch(self, key_id: uuid.UUID, at: datetime | None = None) -> None:
        """Record a use; callers throttle so a busy agent does not write on every call."""
        await self._session.execute(
            update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=at or utcnow())
        )


__all__ = ["ApiKeyRepository"]
