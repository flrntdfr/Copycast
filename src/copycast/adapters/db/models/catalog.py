"""The ``catalog_items`` table: every item a Source advertised plus every archived Episode."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from copycast.adapters.db.base import Base, TimestampMixin, TZDateTime, enum_check
from copycast.domain.enums import ArchiveState, WantedReason


class CatalogItem(TimestampMixin, Base):
    __tablename__ = "catalog_items"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    feed_id: Mapped[str] = mapped_column(
        String(63), ForeignKey("feeds.id", ondelete="CASCADE"), nullable=False
    )
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

    source_number: Mapped[int | None] = mapped_column(Integer)
    source_season: Mapped[int | None] = mapped_column(Integer)
    source_position: Mapped[int | None] = mapped_column(Integer)
    tab: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    artwork_url: Mapped[str | None] = mapped_column(Text)

    published_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(Text)
    archivable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    source_item_xml: Mapped[str | None] = mapped_column(Text)

    listed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now()
    )
    last_listed_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    archive_state: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'available'"),
        default=ArchiveState.available.value,
    )
    wanted_reason: Mapped[str | None] = mapped_column(Text)

    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    media_path: Mapped[str | None] = mapped_column(Text)
    media_mime: Mapped[str | None] = mapped_column(Text)
    media_bytes: Mapped[int | None] = mapped_column(BigInteger)

    download_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    first_downloaded_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_downloaded_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    __table_args__ = (
        UniqueConstraint("feed_id", "source_key"),
        UniqueConstraint("feed_id", "ordinal"),
        enum_check("archive_state", ArchiveState, "archive_state"),
        enum_check("wanted_reason", WantedReason, "wanted_reason"),
        Index(
            "ix_catalog_items_feed_published",
            "feed_id",
            text("published_at DESC NULLS LAST"),
            text("ordinal DESC"),
        ),
        Index("ix_catalog_items_feed_state", "feed_id", "archive_state"),
        Index("ix_catalog_items_feed_source_number", "feed_id", "source_number"),
        Index(
            "ix_catalog_items_pending",
            "archive_state",
            postgresql_where=text("archive_state IN ('wanted', 'failed')"),
        ),
        Index(
            "ix_catalog_items_feed_first_downloaded",
            "feed_id",
            "first_downloaded_at",
            postgresql_where=text("archive_state = 'archived'"),
        ),
    )

    @property
    def state(self) -> ArchiveState:
        return ArchiveState(self.archive_state)

    @property
    def added_at(self) -> datetime:
        return self.first_seen_at

    @property
    def media_ext(self) -> str | None:
        if not self.media_path:
            return None
        _, _, ext = self.media_path.rpartition(".")
        return ext or None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CatalogItem {self.id} #{self.ordinal} {self.archive_state}>"
