"""SQLAlchemy models; importing this package registers every table on ``Base.metadata``."""

from __future__ import annotations

from copycast.adapters.db.base import Base
from copycast.adapters.db.models.assets import Asset
from copycast.adapters.db.models.catalog import CatalogItem
from copycast.adapters.db.models.feeds import Feed
from copycast.adapters.db.models.jobs import ACTIVE_STATUSES, Job, JobLogLine
from copycast.adapters.db.models.keys import ApiKey
from copycast.adapters.db.models.requests import Request, RequestItem
from copycast.adapters.db.models.secrets import InstanceSecret
from copycast.adapters.db.models.telemetry import EngineVersion, RefreshRun, WorkerHeartbeat
from copycast.adapters.db.models.users import LoginAttempt, Session, User

__all__ = [
    "ACTIVE_STATUSES",
    "ApiKey",
    "Asset",
    "Base",
    "CatalogItem",
    "EngineVersion",
    "Feed",
    "InstanceSecret",
    "Job",
    "JobLogLine",
    "LoginAttempt",
    "RefreshRun",
    "Request",
    "RequestItem",
    "Session",
    "User",
    "WorkerHeartbeat",
]
