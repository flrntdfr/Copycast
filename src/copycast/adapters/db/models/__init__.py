"""SQLAlchemy models; importing this package registers every table on ``Base.metadata``."""

from __future__ import annotations

from copycast.adapters.db.base import Base
from copycast.adapters.db.models.assets import Asset
from copycast.adapters.db.models.catalog import CatalogItem
from copycast.adapters.db.models.feeds import Feed
from copycast.adapters.db.models.jobs import ACTIVE_STATUSES, Job, JobLogLine
from copycast.adapters.db.models.keys import ApiKey
from copycast.adapters.db.models.requests import Request, RequestItem
from copycast.adapters.db.models.telemetry import EngineVersion, RefreshRun, WorkerHeartbeat

__all__ = [
    "ACTIVE_STATUSES",
    "ApiKey",
    "Asset",
    "Base",
    "CatalogItem",
    "EngineVersion",
    "Feed",
    "Job",
    "JobLogLine",
    "RefreshRun",
    "Request",
    "RequestItem",
    "WorkerHeartbeat",
]
