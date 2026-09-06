"""Alembic environment: async when run from the CLI, on a borrowed connection from the app.

``ensure_schema`` passes its (already locked) connection through
``config.attributes["connection"]``; ``alembic upgrade head`` from a shell
opens its own async engine from ``sqlalchemy.url`` or the Copycast settings.
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from copycast.adapters.db.models import Base

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    from copycast.settings import get_settings

    return get_settings().database_url


def _configure(connection: Connection | None) -> None:
    context.configure(
        connection=connection,
        url=None if connection is not None else _database_url(),
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=False,
        render_as_batch=False,
        literal_binds=connection is None,
    )


def run_migrations_offline() -> None:
    _configure(None)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
        await connection.commit()
    await connectable.dispose()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        do_run_migrations(connection)
    else:
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
