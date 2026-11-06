"""Repositories: the only place SQL is written."""

from __future__ import annotations

from copycast.adapters.db.repositories.assets import AssetRepository
from copycast.adapters.db.repositories.catalog import CatalogRepository, ListingUpsert
from copycast.adapters.db.repositories.feeds import DEFAULT_BUCKET_TITLE, FeedRepository
from copycast.adapters.db.repositories.jobs import (
    PRIORITY_BACKFILL,
    PRIORITY_FOLLOW,
    PRIORITY_MANUAL,
    JobRepository,
    dedup_key,
)
from copycast.adapters.db.repositories.keys import ApiKeyRepository
from copycast.adapters.db.repositories.login_attempts import LoginAttemptRepository
from copycast.adapters.db.repositories.quota import QuotaRepository
from copycast.adapters.db.repositories.requests import RequestRepository
from copycast.adapters.db.repositories.secrets import InstanceSecretRepository
from copycast.adapters.db.repositories.sessions import SessionRepository
from copycast.adapters.db.repositories.telemetry import TelemetryRepository
from copycast.adapters.db.repositories.uploads import StagedUploadRepository
from copycast.adapters.db.repositories.users import UserRepository

__all__ = [
    "DEFAULT_BUCKET_TITLE",
    "PRIORITY_BACKFILL",
    "PRIORITY_FOLLOW",
    "PRIORITY_MANUAL",
    "ApiKeyRepository",
    "AssetRepository",
    "CatalogRepository",
    "FeedRepository",
    "InstanceSecretRepository",
    "JobRepository",
    "ListingUpsert",
    "LoginAttemptRepository",
    "QuotaRepository",
    "RequestRepository",
    "SessionRepository",
    "StagedUploadRepository",
    "TelemetryRepository",
    "UserRepository",
    "dedup_key",
]
