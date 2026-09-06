"""Postgres advisory locks.

``MIGRATE_LOCK`` serializes ``ensure_schema`` across processes; ``WORKER_LOCK``
is held for the worker's lifetime so a second worker (or ``copycast rebuild``
while the worker runs) backs off instead of racing.
"""

from __future__ import annotations

from types import TracebackType
from typing import Final, Self

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

MIGRATE_LOCK: Final = 0x434F5059
WORKER_LOCK: Final = 0x434F5043


async def try_advisory_lock(conn: AsyncConnection, key: int) -> bool:
    """Session-level ``pg_try_advisory_lock``; held until released or the connection closes."""
    result = await conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
    return bool(result.scalar_one())


async def advisory_lock(conn: AsyncConnection, key: int) -> None:
    """Blocking session-level ``pg_advisory_lock``."""
    await conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})


async def advisory_unlock(conn: AsyncConnection, key: int) -> bool:
    result = await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
    return bool(result.scalar_one())


async def advisory_xact_lock(conn: AsyncConnection, key: int) -> None:
    """Transaction-level lock, released automatically at commit or rollback."""
    await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


class SessionLock:
    """A session-level advisory lock on a dedicated connection.

    ``async with SessionLock(db_engine, WORKER_LOCK) as lock:`` acquires (or,
    with ``wait=False``, tries to acquire) the lock; ``lock.held`` tells which.
    The connection stays open, and the lock held, until the block exits.
    """

    def __init__(self, db_engine: AsyncEngine, key: int, *, wait: bool = False) -> None:
        self._db_engine = db_engine
        self._key = key
        self._wait = wait
        self._conn: AsyncConnection | None = None
        self.held = False

    @property
    def connection(self) -> AsyncConnection:
        if self._conn is None:
            raise RuntimeError("SessionLock is not active")
        return self._conn

    async def acquire(self) -> bool:
        if self._conn is not None:
            return self.held
        conn = await self._db_engine.connect()
        try:
            if self._wait:
                await advisory_lock(conn, self._key)
                self.held = True
            else:
                self.held = await try_advisory_lock(conn, self._key)
            await conn.commit()
        except BaseException:
            await conn.close()
            raise
        self._conn = conn
        return self.held

    async def release(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            if self.held:
                await advisory_unlock(conn, self._key)
                await conn.commit()
        finally:
            self.held = False
            await conn.close()

    async def __aenter__(self) -> Self:
        await self.acquire()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.release()


__all__ = [
    "MIGRATE_LOCK",
    "WORKER_LOCK",
    "SessionLock",
    "advisory_lock",
    "advisory_unlock",
    "advisory_xact_lock",
    "try_advisory_lock",
]
