"""The ``jobs`` and ``job_log_lines`` tables: the worker's queue and its append-only log."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from copycast.adapters.db.base import Base, TimestampMixin, TZDateTime, enum_check
from copycast.domain.enums import ErrorKind, JobKind, JobStatus, JobTrigger, LogLevel

ACTIVE_STATUSES: tuple[str, ...] = (JobStatus.queued.value, JobStatus.running.value)


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    feed_id: Mapped[str | None] = mapped_column(
        String(63), ForeignKey("feeds.id", ondelete="CASCADE")
    )
    item_id: Mapped[str | None] = mapped_column(
        String(16), ForeignKey("catalog_items.id", ondelete="CASCADE")
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("requests.id", ondelete="SET NULL")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )

    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'queued'"), default=JobStatus.queued.value
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("100"), default=100
    )
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("5"), default=5
    )
    run_after: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now()
    )
    dedup_key: Mapped[str | None] = mapped_column(Text)
    claimed_by: Mapped[str | None] = mapped_column(Text)
    claimed_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    error: Mapped[str | None] = mapped_column(Text)
    error_kind: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    engine_version: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        enum_check("kind", JobKind, "kind"),
        enum_check("trigger", JobTrigger, "trigger"),
        enum_check("status", JobStatus, "status"),
        enum_check("error_kind", ErrorKind, "error_kind"),
        Index(
            "ix_jobs_dedup_active",
            "dedup_key",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index("ix_jobs_queue", "status", "run_after", "priority"),
        Index("ix_jobs_feed_created", "feed_id", text("created_at DESC")),
        Index("ix_jobs_item_id", "item_id"),
        Index("ix_jobs_request_id", "request_id"),
    )

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Job {self.id} {self.kind} {self.status}>"


class JobLogLine(TimestampMixin, Base):
    __tablename__ = "job_log_lines"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, server_default=func.now())
    level: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (enum_check("level", LogLevel, "level"),)
