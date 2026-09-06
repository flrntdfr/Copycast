"""Schema management: migrate to head under ``MIGRATE_LOCK`` or wait for another process to.

``COPYCAST_AUTO_MIGRATE=true`` (the default, and the api on Kubernetes) runs
:func:`ensure_schema`; ``false`` (the Kubernetes worker) runs
:func:`await_schema`, which polls for up to five minutes and then gives up with
:class:`SchemaNotCurrent` so the process exits 2 instead of crash-looping.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from copycast.adapters.db.locks import MIGRATE_LOCK, advisory_lock, advisory_unlock

SCRIPT_LOCATION = "copycast:adapters/db/migrations"
SCHEMA_WAIT_SECONDS = 300.0
SCHEMA_POLL_SECONDS = 5.0


class SchemaNotCurrent(RuntimeError):
    """The database schema is not at head and this process may not migrate it."""


def alembic_config(url: str | None = None) -> Config:
    """An Alembic ``Config`` that needs no ``alembic.ini`` on disk."""
    cfg = Config()
    cfg.set_main_option("script_location", SCRIPT_LOCATION)
    cfg.set_main_option("file_template", "%%(rev)s_%%(slug)s")
    if url is not None:
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def head_revision() -> str | None:
    """The single head of the migration scripts shipped in the package."""
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    if not heads:
        return None
    if len(heads) > 1:
        raise RuntimeError(f"migration scripts have several heads: {heads}")
    return heads[0]


def _current_revision(conn: Connection) -> str | None:
    heads = MigrationContext.configure(conn).get_current_heads()
    return heads[0] if heads else None


def _run_command(conn: Connection, action: Callable[[Config], Any]) -> None:
    cfg = alembic_config()
    cfg.attributes["connection"] = conn
    action(cfg)


def _upgrade(conn: Connection, revision: str = "head") -> None:
    _run_command(conn, lambda cfg: command.upgrade(cfg, revision))


def _downgrade(conn: Connection, revision: str = "base") -> None:
    _run_command(conn, lambda cfg: command.downgrade(cfg, revision))


async def current_revision(db_engine: AsyncEngine) -> str | None:
    async with db_engine.connect() as conn:
        return await conn.run_sync(_current_revision)


async def schema_is_current(db_engine: AsyncEngine) -> bool:
    return await current_revision(db_engine) == head_revision()


async def ensure_schema(db_engine: AsyncEngine) -> None:
    """Migrate to head, serialized across processes by ``MIGRATE_LOCK``. Idempotent."""
    async with db_engine.connect() as conn:
        await advisory_lock(conn, MIGRATE_LOCK)
        try:
            if await conn.run_sync(_current_revision) != head_revision():
                await conn.run_sync(_upgrade)
            await conn.commit()
        finally:
            await advisory_unlock(conn, MIGRATE_LOCK)
            await conn.commit()


async def downgrade_schema(db_engine: AsyncEngine, revision: str = "base") -> None:
    """Downgrade (tests and operators only); serialized like :func:`ensure_schema`."""
    async with db_engine.connect() as conn:
        await advisory_lock(conn, MIGRATE_LOCK)
        try:
            await conn.run_sync(_downgrade, revision)
            await conn.commit()
        finally:
            await advisory_unlock(conn, MIGRATE_LOCK)
            await conn.commit()


async def await_schema(
    db_engine: AsyncEngine,
    *,
    wait_seconds: float = SCHEMA_WAIT_SECONDS,
    poll: float = SCHEMA_POLL_SECONDS,
) -> None:
    """Wait until another process has migrated to head.

    Raises :class:`SchemaNotCurrent` once ``wait_seconds`` have elapsed.
    """
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            current = await current_revision(db_engine)
        except Exception as exc:  # database still starting: keep waiting
            current = None
            last_error: str | None = str(exc)
        else:
            last_error = None
        if current == head_revision():
            return
        if time.monotonic() >= deadline:
            detail = f"database schema is at {current or 'no revision'}, head is {head_revision()}"
            if last_error:
                detail += f" ({last_error})"
            raise SchemaNotCurrent(
                f"{detail}; run `copycast migrate` or set COPYCAST_AUTO_MIGRATE=true"
            )
        await asyncio.sleep(min(poll, max(0.0, deadline - time.monotonic())))


async def prepare_schema(
    db_engine: AsyncEngine, *, auto_migrate: bool, wait_seconds: float = SCHEMA_WAIT_SECONDS
) -> None:
    """The ``COPYCAST_AUTO_MIGRATE`` policy at process start."""
    if auto_migrate:
        await ensure_schema(db_engine)
    else:
        await await_schema(db_engine, wait_seconds=wait_seconds)


__all__ = [
    "SCRIPT_LOCATION",
    "SchemaNotCurrent",
    "alembic_config",
    "await_schema",
    "current_revision",
    "downgrade_schema",
    "ensure_schema",
    "head_revision",
    "prepare_schema",
    "schema_is_current",
]
