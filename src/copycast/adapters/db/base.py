"""Declarative base, naming convention and the shared column helpers.

Enumerations are stored as TEXT guarded by a named CHECK constraint (never a
native enum), every timestamp is TIMESTAMPTZ and every table carries
``created_at``/``updated_at`` through :class:`TimestampMixin`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import BigInteger, CheckConstraint, CursorResult, DateTime, MetaData, Result, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

TZDateTime = DateTime(timezone=True)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        datetime: TZDateTime,
        dict[str, Any]: JSONB,
    }


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


def enum_check(column: str, values: type[StrEnum] | tuple[str, ...], name: str) -> CheckConstraint:
    """``CHECK (column IN (...))`` named ``ck_<table>_<name>`` by the convention."""
    members = tuple(str(v) for v in values) if not isinstance(values, tuple) else values
    quoted = ", ".join(f"'{v}'" for v in members)
    return CheckConstraint(f"{column} IN ({quoted})", name=name)


def utcnow() -> datetime:
    return datetime.now(UTC)


def rows_affected(result: Result[Any]) -> int:
    """``rowcount`` of an executed INSERT/UPDATE/DELETE (``Session.execute`` is typed loosely)."""
    cursor = cast("CursorResult[Any]", result)
    return int(cursor.rowcount or 0)


async def refresh_loaded[T](
    session: AsyncSession, model: type[T], predicate: Callable[[T], bool] | None = None
) -> int:
    """Re-read every loaded ``model`` instance (matching ``predicate``) after a Core UPDATE.

    ``Session.expire_all`` would do the same lazily, but a lazy load from an
    ``AsyncSession`` raises ``MissingGreenlet``; refreshing eagerly keeps ORM
    instances the caller already holds usable. Returns how many were refreshed.
    """
    refreshed = 0
    for instance in list(session.identity_map.values()):
        if isinstance(instance, model) and (predicate is None or predicate(instance)):
            await session.refresh(instance)
            refreshed += 1
    return refreshed


__all__ = [
    "Base",
    "BigInteger",
    "TZDateTime",
    "TimestampMixin",
    "enum_check",
    "refresh_loaded",
    "rows_affected",
    "utcnow",
]
