"""Login attempts: failures and locks per typed username, known or not."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from copycast.adapters.db.base import rows_affected
from copycast.adapters.db.models import LoginAttempt
from copycast.application.services.auth import lock_seconds

PRUNE_AFTER = timedelta(hours=24)


class LoginAttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, username: str) -> LoginAttempt | None:
        result = await self._session.execute(
            select(LoginAttempt).where(LoginAttempt.username == username)
        )
        return result.scalar_one_or_none()

    async def record_failure(self, username: str, now: datetime) -> datetime | None:
        """Count one failure; from the lock threshold on, lock the name and return until when."""
        stmt = (
            insert(LoginAttempt)
            .values(username=username, failed_count=1, updated_at=now)
            .on_conflict_do_update(
                index_elements=[LoginAttempt.username],
                set_={"failed_count": LoginAttempt.failed_count + 1, "updated_at": now},
            )
            .returning(LoginAttempt.failed_count)
        )
        failed = int((await self._session.execute(stmt)).scalar_one())
        seconds = lock_seconds(failed)
        locked_until = now + timedelta(seconds=seconds) if seconds else None
        await self._session.execute(
            update(LoginAttempt)
            .where(LoginAttempt.username == username)
            .values(locked_until=locked_until)
        )
        return locked_until

    async def reset(self, username: str) -> None:
        await self._session.execute(delete(LoginAttempt).where(LoginAttempt.username == username))

    async def prune(self, now: datetime) -> int:
        """Delete rows neither locked nor touched for 24 hours."""
        result = await self._session.execute(
            delete(LoginAttempt).where(
                LoginAttempt.locked_until.is_(None) | (LoginAttempt.locked_until < now),
                LoginAttempt.updated_at < now - PRUNE_AFTER,
            )
        )
        return rows_affected(result)


__all__ = ["PRUNE_AFTER", "LoginAttemptRepository"]
