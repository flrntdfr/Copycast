"""API keys: minted from the UI, looked up by digest when an MCP client connects."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from copycast.adapters.db.base import rows_affected, utcnow
from copycast.adapters.db.models import ApiKey
from copycast.domain.enums import KeyScope
from copycast.domain.exceptions import NotFound


class ApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, key_id: uuid.UUID) -> ApiKey | None:
        return await self._session.get(ApiKey, key_id)

    async def require(self, key_id: uuid.UUID) -> ApiKey:
        key = await self.get(key_id)
        if key is None:
            raise NotFound("api key", str(key_id))
        return key

    async def by_hash(self, key_hash: str) -> ApiKey | None:
        result = await self._session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        return result.scalar_one_or_none()

    async def list(self) -> list[ApiKey]:
        stmt = select(ApiKey).order_by(ApiKey.created_at.desc(), ApiKey.id)
        return list((await self._session.execute(stmt)).scalars())

    async def add(self, *, name: str, scope: KeyScope, key_hash: str, prefix: str) -> ApiKey:
        key = ApiKey(name=name, scope=scope.value, key_hash=key_hash, prefix=prefix)
        self._session.add(key)
        await self._session.flush()
        return key

    async def delete(self, key_id: uuid.UUID) -> bool:
        result = await self._session.execute(delete(ApiKey).where(ApiKey.id == key_id))
        return rows_affected(result) > 0

    async def touch(self, key_id: uuid.UUID, at: datetime | None = None) -> None:
        """Record a use; callers throttle so a busy agent does not write on every call."""
        await self._session.execute(
            update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=at or utcnow())
        )


__all__ = ["ApiKeyRepository"]
