"""The ``assets`` table: Artwork, chapters and transcripts of a feed or one of its items."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from copycast.adapters.db.base import Base, TimestampMixin, TZDateTime, enum_check
from copycast.domain.enums import AssetFormat, AssetKind, AssetProvenance, AssetState


class Asset(TimestampMixin, Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    feed_id: Mapped[str] = mapped_column(
        String(63), ForeignKey("feeds.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[str | None] = mapped_column(
        String(16), ForeignKey("catalog_items.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    provenance: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'mirrored'"),
        default=AssetProvenance.mirrored.value,
    )
    language: Mapped[str | None] = mapped_column(Text)
    format: Mapped[str | None] = mapped_column(Text)
    slot: Mapped[str | None] = mapped_column(Text)
    """Tells attachments of one item apart: a hash of the remote URL; null for other kinds."""
    remote_url: Mapped[str | None] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(Text)
    mime: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'wanted'"), default=AssetState.wanted.value
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    __table_args__ = (
        enum_check("kind", AssetKind, "kind"),
        enum_check("provenance", AssetProvenance, "provenance"),
        enum_check("format", AssetFormat, "format"),
        enum_check("state", AssetState, "state"),
        Index(
            "ix_assets_identity",
            "feed_id",
            func.coalesce(text("item_id"), text("''")),
            "kind",
            func.coalesce(text("language"), text("''")),
            func.coalesce(text("format"), text("''")),
            "provenance",
            func.coalesce(text("slot"), text("''")),
            unique=True,
        ),
        Index("ix_assets_item_id", "item_id"),
    )

    @property
    def basename(self) -> str | None:
        if not self.local_path:
            return None
        return self.local_path.rsplit("/", 1)[-1]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Asset {self.id} {self.kind} {self.state}>"
