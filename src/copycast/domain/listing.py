"""The single listing model returned by the RSS parser and yt-dlp flat extraction."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from copycast.domain.enums import ListingOrder


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SourceListingItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_key: str = Field(min_length=1)
    source_url: str | None = None
    title: str
    description: str | None = None
    published_at: datetime | None = None
    duration_seconds: int | None = Field(default=None, ge=0)
    artwork_url: str | None = None
    author: str | None = None
    source_number: int | None = None
    source_season: int | None = None
    position: int = Field(default=0, ge=0, description="0-based index in the listing as served")
    tab: str | None = None
    enclosure_url: str | None = None
    enclosure_type: str | None = None
    archivable: bool = True

    @field_validator("published_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return _ensure_utc(value)


class SourceListing(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    service: str
    extractor_key: str | None = None
    title: str | None = None
    description: str | None = None
    author: str | None = None
    artwork_url: str | None = None
    webpage_url: str | None = None
    language: str | None = None
    listing_order: ListingOrder = ListingOrder.newest_first
    items: list[SourceListingItem] = Field(default_factory=list[SourceListingItem])
    raw: dict[str, Any] | None = None

    @property
    def source_keys(self) -> list[str]:
        return [item.source_key for item in self.items]
