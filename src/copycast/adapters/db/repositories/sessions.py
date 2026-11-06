"""Sessions: browser logins, stored by the SHA-256 of their token."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from copycast.adapters.db.base import rows_affected
from copycast.adapters.db.models import Session


class SessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        digest: bytes,
        username: str,
        *,
        now: datetime,
        expires_at: datetime,
        user_agent: str | None,
    ) -> None:
        self._session.add(
            Session(
                id_hash=digest,
                username=username,
                created_at=now,
                last_seen_at=now,
                expires_at=expires_at,
                user_agent=user_agent,
            )
        )
        await self._session.flush()

    async def get(self, digest: bytes) -> Session | None:
        result = await self._session.execute(select(Session).where(Session.id_hash == digest))
        return result.scalar_one_or_none()

    async def touch(self, digest: bytes, *, now: datetime, expires_at: datetime) -> None:
        await self._session.execute(
            update(Session)
            .where(Session.id_hash == digest)
            .values(last_seen_at=now, expires_at=expires_at)
        )

    async def delete(self, digest: bytes) -> None:
        await self._session.execute(delete(Session).where(Session.id_hash == digest))

    async def delete_expired(self, now: datetime) -> int:
        result = await self._session.execute(delete(Session).where(Session.expires_at < now))
        return rows_affected(result)

    async def delete_for_users_not_in(self, usernames: Collection[str]) -> int:
        result = await self._session.execute(
            delete(Session).where(Session.username.not_in(list(usernames)))
        )
        return rows_affected(result)

    async def delete_for_user(self, username: str) -> int:
        result = await self._session.execute(delete(Session).where(Session.username == username))
        return rows_affected(result)


__all__ = ["SessionRepository"]
