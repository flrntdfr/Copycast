"""Telemetry tables: ``refresh_runs``, ``worker_heartbeat`` and ``engine_versions``.

Nothing here survives ``copycast rebuild``; it is observability, not intent.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from copycast.adapters.db.base import Base, TimestampMixin, TZDateTime, enum_check
from copycast.domain.enums import JobTrigger, RefreshRunStatus


class RefreshRun(TimestampMixin, Base):
    __tablename__ = "refresh_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    feed_id: Mapped[str] = mapped_column(
        String(63), ForeignKey("feeds.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'running'"),
        default=RefreshRunStatus.running.value,
    )
    started_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    listed_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    new_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    delisted_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    wanted_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    error: Mapped[str | None] = mapped_column(Text)
    engine_version: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        enum_check("trigger", JobTrigger, "trigger"),
        enum_check("status", RefreshRunStatus, "status"),
        Index("ix_refresh_runs_feed_started", "feed_id", text("started_at DESC")),
        Index("ix_refresh_runs_job_id", "job_id"),
    )


class WorkerHeartbeat(TimestampMixin, Base):
    __tablename__ = "worker_heartbeat"

    worker_id: Mapped[str] = mapped_column(Text, primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, server_default=func.now())
    status: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )


class EngineVersion(TimestampMixin, Base):
    __tablename__ = "engine_versions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    version: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    release_date: Mapped[date | None] = mapped_column(Date)
    channel: Mapped[str | None] = mapped_column(Text)
    git_head: Mapped[str | None] = mapped_column(Text)
    app_version: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now()
    )
