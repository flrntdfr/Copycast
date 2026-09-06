"""Async engine and session factory over ``postgresql+psycopg``."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from copycast.settings import Settings

POOL_SIZE = 5
MAX_OVERFLOW = 10


def create_db_engine(settings: Settings, *, application_name: str = "copycast") -> AsyncEngine:
    """One engine per process; ``pool_pre_ping`` survives Postgres restarts."""
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        connect_args={"application_name": application_name},
    )


def create_sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Sessions keep their loaded rows usable after commit (``expire_on_commit=False``)."""
    return async_sessionmaker(db_engine, expire_on_commit=False, autoflush=True)


__all__ = ["create_db_engine", "create_sessionmaker"]
