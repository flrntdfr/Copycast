"""The ``feeds`` table: one row per Mirror or Inbox."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from copycast.adapters.db.base import Base, TimestampMixin, TZDateTime, enum_check
from copycast.domain.credentials import new_feed_password, new_feed_username
from copycast.domain.enums import BackfillMode, FeedKind, SourceKind

KIND_SHAPE = (
    "(kind = 'mirror' AND source_url IS NOT NULL AND source_dedup_key IS NOT NULL "
    "AND source_kind IS NOT NULL AND backfill_mode IS NOT NULL AND autoprune_days IS NULL) "
    "OR (kind = 'inbox' AND source_url IS NULL AND source_dedup_key IS NULL "
    "AND source_kind IS NULL AND backfill_mode IS NULL)"
)


class Feed(TimestampMixin, Base):
    __tablename__ = "feeds"

    id: Mapped[str] = mapped_column(String(63), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    artwork_url: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)

    # The feed's own Basic auth pair, minted at creation and rotated on demand
    auth_username: Mapped[str] = mapped_column(
        String(16), nullable=False, default=new_feed_username
    )
    auth_password: Mapped[str] = mapped_column(Text, nullable=False, default=new_feed_password)

    # Mirror only
    source_url: Mapped[str | None] = mapped_column(Text)
    source_dedup_key: Mapped[str | None] = mapped_column(Text, unique=True)
    source_kind: Mapped[str | None] = mapped_column(Text)
    service: Mapped[str | None] = mapped_column(Text)
    source_etag: Mapped[str | None] = mapped_column(Text)
    source_last_modified: Mapped[str | None] = mapped_column(Text)

    backfill_mode: Mapped[str | None] = mapped_column(Text)
    backfill_latest_n: Mapped[int | None] = mapped_column(Integer)
    follow: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    paused: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    policy_applied_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    engine_options: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )

    # Inbox only
    autoprune_days: Mapped[int | None] = mapped_column(Integer)
    last_autoprune_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    source_channel_xml: Mapped[str | None] = mapped_column(Text)
    last_refresh_attempt_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_refresh_success_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)

    storage_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), default=0
    )
    intent_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1"), default=1
    )
    revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1"), default=1
    )

    __table_args__ = (
        enum_check("kind", FeedKind, "kind"),
        enum_check("source_kind", SourceKind, "source_kind"),
        enum_check("backfill_mode", BackfillMode, "backfill_mode"),
        CheckConstraint(KIND_SHAPE, name="kind_shape"),
        CheckConstraint("jsonb_typeof(engine_options) = 'object'", name="engine_options_object"),
        CheckConstraint("autoprune_days IS NULL OR autoprune_days > 0", name="autoprune_days"),
        CheckConstraint(
            "backfill_latest_n IS NULL OR backfill_latest_n > 0", name="backfill_latest_n"
        ),
        Index(
            "ix_feeds_is_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
        ),
        Index("ix_feeds_lower_title", func.lower(text("title"))),
    )

    @property
    def is_mirror(self) -> bool:
        return self.kind == FeedKind.mirror

    @property
    def is_inbox(self) -> bool:
        return self.kind == FeedKind.inbox

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Feed {self.id} {self.kind} {self.title!r}>"
