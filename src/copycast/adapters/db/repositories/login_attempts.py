"""Login attempts: failures and locks per username; an unknown name only as its HMAC."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from copycast.adapters.db.base import rows_affected
from copycast.adapters.db.models import LoginAttempt
from copycast.application.services.auth import lock_seconds
from copycast.application.services.context import AttemptClaim

PRUNE_AFTER = timedelta(hours=24)


class LoginAttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, username: str) -> LoginAttempt | None:
        result = await self._session.execute(
            select(LoginAttempt).where(LoginAttempt.username == username)
        )
        return result.scalar_one_or_none()

    async def claim(self, username: str, now: datetime) -> AttemptClaim:
        """Check the lock and count one failure, atomically, before the verify runs.

        The row is created if needed, then read ``FOR UPDATE``: concurrent claims on one
        name (from any process) queue on that row lock, so each sees the count and lock
        the previous one left. A locked name is refused with nothing counted; otherwise
        the count goes up and, from the threshold on, the lock is set at once.
        """
        await self._session.execute(
            insert(LoginAttempt)
            .values(username=username, failed_count=0, updated_at=now)
            .on_conflict_do_nothing(index_elements=[LoginAttempt.username])
        )
        row = (
            await self._session.execute(
                select(LoginAttempt.failed_count, LoginAttempt.locked_until)
                .where(LoginAttempt.username == username)
                .with_for_update()
            )
        ).one()
        if row.locked_until is not None and row.locked_until > now:
            return AttemptClaim(allowed=False, locked_until=row.locked_until)
        failed = int(row.failed_count) + 1
        seconds = lock_seconds(failed)
        locked_until = now + timedelta(seconds=seconds) if seconds else None
        await self._session.execute(
            update(LoginAttempt)
            .where(LoginAttempt.username == username)
            .values(failed_count=failed, locked_until=locked_until, updated_at=now)
        )
        return AttemptClaim(allowed=True, locked_until=locked_until)

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
