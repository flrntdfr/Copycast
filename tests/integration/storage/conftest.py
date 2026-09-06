"""Postgres-backed fixtures and row factories for the storage milestone.

Every test here gets its own database cloned from the migrated template (the
``db`` fixture in ``tests/conftest.py``) and a fresh data directory.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from copycast.adapters.db.models import Asset, CatalogItem, Feed, Job, Request
from copycast.adapters.db.repositories import dedup_key
from copycast.adapters.db.session import create_db_engine, create_sessionmaker
from copycast.adapters.db.uow import UnitOfWork, UnitOfWorkFactory
from copycast.adapters.storage.layout import Layout
from copycast.domain.enums import (
    ArchiveState,
    AssetFormat,
    AssetKind,
    AssetProvenance,
    AssetState,
    BackfillMode,
    FeedKind,
    JobKind,
    JobStatus,
    JobTrigger,
    RequestedVia,
    SourceKind,
)
from copycast.domain.ids import asset_id, item_id, mirror_id
from copycast.domain.urls import normalize_source_url
from copycast.settings import Settings

pytestmark = pytest.mark.integration

NOW = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
SOURCE_URL = "https://podcast.example/feed.xml"


@pytest.fixture
async def db_engine(settings: Settings, db: str) -> AsyncIterator[AsyncEngine]:
    engine = create_db_engine(settings, application_name="copycast-tests")
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_sessionmaker(db_engine)


@pytest.fixture
def layout(data_dir: Path) -> Layout:
    return Layout(data_dir)


@pytest.fixture
def uow_factory(
    sessionmaker: async_sessionmaker[AsyncSession], layout: Layout
) -> UnitOfWorkFactory:
    return UnitOfWorkFactory(sessionmaker, layout, debounce_seconds=0.05)


# --------------------------------------------------------------------------- row factories


def mirror_row(
    source_url: str = SOURCE_URL,
    *,
    title: str = "Test Podcast",
    source_kind: SourceKind = SourceKind.rss,
    backfill_mode: BackfillMode = BackfillMode.all,
    backfill_latest_n: int | None = None,
    follow: bool = True,
    paused: bool = False,
    **overrides: Any,
) -> Feed:
    dedup = normalize_source_url(source_url)
    fields: dict[str, Any] = {
        "id": mirror_id(dedup),
        "kind": FeedKind.mirror.value,
        "title": title,
        "description": "A podcast used in tests",
        "author": "Tester",
        "artwork_url": "https://podcast.example/artwork.jpg",
        "language": "en",
        "source_url": source_url,
        "source_dedup_key": dedup,
        "source_kind": source_kind.value,
        "service": "RSS" if source_kind is SourceKind.rss else "YouTube",
        "backfill_mode": backfill_mode.value,
        "backfill_latest_n": backfill_latest_n,
        "follow": follow,
        "paused": paused,
        "engine_options": {},
    }
    fields.update(overrides)
    return Feed(**fields)


def inbox_row(name: str = "Later", *, is_default: bool = False, **overrides: Any) -> Feed:
    fields: dict[str, Any] = {
        "id": f"{name.lower()}-abc234",
        "kind": FeedKind.inbox.value,
        "is_default": is_default,
        "title": name,
        "follow": False,
        "engine_options": {},
    }
    fields.update(overrides)
    return Feed(**fields)


def item_row(
    feed: Feed,
    n: int,
    *,
    state: ArchiveState = ArchiveState.available,
    listed: bool = True,
    **overrides: Any,
) -> CatalogItem:
    key = f"urn:test:item:{n}"
    fields: dict[str, Any] = {
        "id": item_id(feed.id, key),
        "feed_id": feed.id,
        "source_key": key,
        "ordinal": n,
        "source_number": n,
        "title": f"Episode {n}",
        "description": f"Description of episode {n}",
        "published_at": NOW - timedelta(days=100 - n),
        "duration_seconds": 60 * n,
        "source_url": f"https://podcast.example/episodes/{n}",
        "archivable": True,
        "listed": listed,
        "first_seen_at": NOW - timedelta(days=100 - n),
        "last_listed_at": NOW,
        "archive_state": state.value,
    }
    if state is ArchiveState.archived:
        fields.update(
            media_path=f"media/{fields['id']}.m4a",
            media_mime="audio/mp4",
            media_bytes=1000 * n,
            archived_at=NOW,
        )
    fields.update(overrides)
    return CatalogItem(**fields)


def asset_row(
    feed: Feed,
    item: CatalogItem | None,
    kind: AssetKind = AssetKind.artwork,
    *,
    language: str | None = None,
    format: AssetFormat | None = None,
    provenance: AssetProvenance = AssetProvenance.mirrored,
    state: AssetState = AssetState.archived,
    ext: str | None = None,
    size_bytes: int = 321,
) -> Asset:
    iid = item.id if item is not None else None
    fmt = format.value if format else None
    extension = ext or (fmt if fmt else "jpg")
    if kind is AssetKind.artwork:
        local = f"assets/{iid}.artwork.{extension}" if iid else f"assets/feed.artwork.{extension}"
    elif kind is AssetKind.chapters:
        local = f"assets/{iid}.chapters.json"
    else:
        local = f"assets/{iid}.transcript.{language}.{provenance.value}.{extension}"
    return Asset(
        id=asset_id(feed.id, iid, kind.value, language, fmt, provenance.value),
        feed_id=feed.id,
        item_id=iid,
        kind=kind.value,
        provenance=provenance.value,
        language=language,
        format=fmt,
        remote_url=f"https://podcast.example/{local}",
        local_path=local if state is AssetState.archived else None,
        mime="image/jpeg" if kind is AssetKind.artwork else "application/json",
        size_bytes=size_bytes if state is AssetState.archived else None,
        state=state.value,
        fetched_at=NOW if state is AssetState.archived else None,
    )


def request_row(
    feed: Feed, url: str = "https://www.youtube.com/watch?v=dQw4w9WgXcQ", **overrides: Any
) -> Request:
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "feed_id": feed.id,
        "url": url,
        "requested_via": RequestedVia.mcp.value,
    }
    fields.update(overrides)
    return Request(**fields)


def job_row(
    kind: JobKind = JobKind.refresh,
    *,
    feed: Feed | None = None,
    item: CatalogItem | None = None,
    trigger: JobTrigger = JobTrigger.manual,
    status: JobStatus = JobStatus.queued,
    priority: int = 100,
    **overrides: Any,
) -> Job:
    ident = item.id if item is not None else (feed.id if feed is not None else "global")
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "kind": kind.value,
        "trigger": trigger.value,
        "feed_id": feed.id if feed is not None else None,
        "item_id": item.id if item is not None else None,
        "status": status.value,
        "priority": priority,
        "dedup_key": dedup_key(kind, ident),
        "payload": {},
    }
    fields.update(overrides)
    return Job(**fields)


async def seed(uow: UnitOfWork, *rows: Any) -> None:
    """Add rows in order, flushing one by one so FK parents land before children."""
    for row in rows:
        uow.session.add(row)
        await uow.session.flush()


__all__ = [
    "NOW",
    "SOURCE_URL",
    "asset_row",
    "inbox_row",
    "item_row",
    "job_row",
    "mirror_row",
    "request_row",
    "seed",
]
