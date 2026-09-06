"""The ``requests`` and ``request_items`` tables: URLs pushed into an Inbox and their items."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from copycast.adapters.db.base import Base, TimestampMixin, TZDateTime, enum_check
from copycast.domain.enums import RequestedVia, RequestStatus


class Request(TimestampMixin, Base):
    __tablename__ = "requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    feed_id: Mapped[str] = mapped_column(
        String(63), ForeignKey("feeds.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    requested_via: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'queued'"), default=RequestStatus.queued.value
    )
    item_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    error: Mapped[str | None] = mapped_column(Text)
    expanded_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    __table_args__ = (
        enum_check("requested_via", RequestedVia, "requested_via"),
        enum_check("status", RequestStatus, "status"),
        Index("ix_requests_feed_created", "feed_id", text("created_at DESC")),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Request {self.id} {self.status} {self.url!r}>"


class RequestItem(TimestampMixin, Base):
    __tablename__ = "request_items"

    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("requests.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("catalog_items.id", ondelete="CASCADE"), primary_key=True
    )

    __table_args__ = (Index("ix_request_items_item_id", "item_id"),)
