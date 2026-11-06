"""Users: the configured names (synced at startup, never deleted) and their TOTP state."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from copycast.adapters.db.base import rows_affected
from copycast.adapters.db.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure(self, usernames: Iterable[str]) -> None:
        """Insert the missing rows; an existing User (configured or not) is left alone."""
        rows = [{"username": name} for name in dict.fromkeys(usernames)]
        if not rows:
            return
        await self._session.execute(insert(User).values(rows).on_conflict_do_nothing())

    async def all(self) -> list[User]:
        result = await self._session.execute(select(User).order_by(User.username))
        return list(result.scalars())

    async def touch_login(self, username: str, at: datetime) -> None:
        await self._session.execute(
            update(User).where(User.username == username).values(last_login_at=at)
        )

    async def claim_totp_step(self, username: str, step: int) -> bool:
        """Store ``step`` unless a step as late is stored: a TOTP code is good once."""
        result = await self._session.execute(
            update(User)
            .where(
                User.username == username,
                User.totp_last_step.is_(None) | (User.totp_last_step < step),
            )
            .values(totp_last_step=step)
        )
        return rows_affected(result) > 0

    async def totp_last_step(self, username: str) -> int | None:
        result = await self._session.execute(
            select(User.totp_last_step).where(User.username == username)
        )
        return result.scalar_one_or_none()

    async def credential_digests(self) -> dict[str, bytes | None]:
        result = await self._session.execute(select(User.username, User.credential_digest))
        return {username: digest for username, digest in result.tuples()}

    async def set_credential_digest(
        self, username: str, digest: bytes, *, reset_totp: bool
    ) -> None:
        values: dict[str, object] = {"credential_digest": digest}
        if reset_totp:
            values["totp_last_step"] = None
        await self._session.execute(update(User).where(User.username == username).values(values))


__all__ = ["UserRepository"]
