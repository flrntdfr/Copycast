"""Repositories: the only place SQL is written."""

from __future__ import annotations

from copycast.adapters.db.repositories.assets import AssetRepository
from copycast.adapters.db.repositories.catalog import CatalogRepository, ListingUpsert
from copycast.adapters.db.repositories.feeds import DEFAULT_INBOX_TITLE, FeedRepository
from copycast.adapters.db.repositories.jobs import (
    PRIORITY_BACKFILL,
    PRIORITY_FOLLOW,
    PRIORITY_MANUAL,
    JobRepository,
    dedup_key,
)
from copycast.adapters.db.repositories.keys import ApiKeyRepository
from copycast.adapters.db.repositories.requests import RequestRepository
from copycast.adapters.db.repositories.telemetry import TelemetryRepository

__all__ = [
    "DEFAULT_INBOX_TITLE",
    "PRIORITY_BACKFILL",
    "PRIORITY_FOLLOW",
    "PRIORITY_MANUAL",
    "ApiKeyRepository",
    "AssetRepository",
    "CatalogRepository",
    "FeedRepository",
    "JobRepository",
    "ListingUpsert",
    "RequestRepository",
    "TelemetryRepository",
    "dedup_key",
]
