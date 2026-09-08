"""The per-feed ``feed.json`` descriptor: intent on disk, re-exported after every intent change.

It carries the feed, its policy, every Catalog item (tombstones included),
every asset row and every Request; never telemetry (jobs, refresh runs,
download counters, attempts, errors). ``copycast rebuild`` reads it back.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from copycast.adapters.db.base import utcnow
from copycast.adapters.db.models import Asset, CatalogItem, Feed, Request
from copycast.adapters.db.repositories import (
    AssetRepository,
    CatalogRepository,
    FeedRepository,
    RequestRepository,
)
from copycast.adapters.storage.atomic import write_atomic
from copycast.adapters.storage.layout import LAYOUT_VERSION, Layout
from copycast.domain.enums import (
    ArchiveState,
    AssetFormat,
    AssetKind,
    AssetProvenance,
    AssetState,
    BackfillMode,
    FeedKind,
    RequestedVia,
    RequestStatus,
    SourceKind,
    WantedReason,
)
from copycast.logging import get_logger
from copycast.version import APP_VERSION

DESCRIPTOR_FORMAT = "copycast-feed"
DEBOUNCE_SECONDS = 5.0

log = get_logger(__name__)


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", from_attributes=True)


class DescriptorFeed(_Strict):
    id: str
    kind: FeedKind
    is_default: bool = False
    title: str
    title_override: str | None = None
    description: str | None = None
    author: str | None = None
    artwork_url: str | None = None
    language: str | None = None
    source_url: str | None = None
    source_dedup_key: str | None = None
    source_kind: SourceKind | None = None
    service: str | None = None
    auth_username: str | None = None
    auth_password: str | None = None
    created_at: datetime


class DescriptorPolicy(_Strict):
    backfill_mode: BackfillMode | None = None
    backfill_latest_n: int | None = None
    min_duration_seconds: int | None = None
    preferred_language: str | None = None
    retention_days: int | None = None
    follow: bool = True
    paused: bool = False
    policy_applied_at: datetime | None = None
    engine_options: dict[str, Any] = Field(default_factory=dict[str, Any])
    autoprune_days: int | None = None


class DescriptorItem(_Strict):
    id: str
    source_key: str
    ordinal: int
    source_number: int | None = None
    source_season: int | None = None
    source_position: int | None = None
    tab: str | None = None
    title: str
    description: str | None = None
    author: str | None = None
    artwork_url: str | None = None
    published_at: datetime | None = None
    published_at_approximate: bool = False
    duration_seconds: int | None = None
    source_url: str | None = None
    archivable: bool = True
    listed: bool = True
    first_seen_at: datetime
    last_listed_at: datetime
    archive_state: ArchiveState = ArchiveState.available
    wanted_reason: WantedReason | None = None
    archived_at: datetime | None = None
    deleted_at: datetime | None = None
    media_path: str | None = None
    media_mime: str | None = None
    media_bytes: int | None = None


class DescriptorAsset(_Strict):
    id: str
    item_id: str | None = None
    kind: AssetKind
    provenance: AssetProvenance = AssetProvenance.mirrored
    language: str | None = None
    format: AssetFormat | None = None
    slot: str | None = None
    remote_url: str | None = None
    local_path: str | None = None
    mime: str | None = None
    size_bytes: int | None = None
    state: AssetState
    fetched_at: datetime | None = None
    created_at: datetime


class DescriptorRequest(_Strict):
    id: uuid.UUID
    url: str
    requested_via: RequestedVia
    status: RequestStatus
    item_count: int = 0
    error: str | None = None
    expanded_at: datetime | None = None
    item_ids: list[str] = Field(default_factory=list[str])
    created_at: datetime


class FeedDescriptor(_Strict):
    format: str = DESCRIPTOR_FORMAT
    layout_version: str = LAYOUT_VERSION
    app_version: str = APP_VERSION
    exported_at: datetime
    intent_version: int
    feed: DescriptorFeed
    policy: DescriptorPolicy
    items: list[DescriptorItem] = Field(default_factory=list[DescriptorItem])
    assets: list[DescriptorAsset] = Field(default_factory=list[DescriptorAsset])
    requests: list[DescriptorRequest] = Field(default_factory=list[DescriptorRequest])

    @classmethod
    def from_rows(
        cls,
        feed: Feed,
        items: Sequence[CatalogItem],
        assets: Sequence[Asset],
        requests: Sequence[Request],
        request_items: dict[uuid.UUID, list[str]],
        *,
        exported_at: datetime | None = None,
    ) -> FeedDescriptor:
        return cls(
            exported_at=exported_at or utcnow(),
            intent_version=feed.intent_version,
            feed=DescriptorFeed.model_validate(feed),
            policy=DescriptorPolicy.model_validate(feed),
            items=[DescriptorItem.model_validate(i) for i in items],
            assets=[DescriptorAsset.model_validate(a) for a in assets],
            requests=[
                DescriptorRequest(
                    id=r.id,
                    url=r.url,
                    requested_via=RequestedVia(r.requested_via),
                    status=RequestStatus(r.status),
                    item_count=r.item_count,
                    error=r.error,
                    expanded_at=r.expanded_at,
                    item_ids=request_items.get(r.id, []),
                    created_at=r.created_at,
                )
                for r in requests
            ],
        )

    def to_json(self) -> str:
        return self.model_dump_json(indent=2) + "\n"

    def write(self, path: Path) -> Path:
        return write_atomic(path, self.to_json())

    @classmethod
    def read(cls, path: Path) -> FeedDescriptor:
        """Parse a descriptor; raises :class:`DescriptorError` for anything unreadable."""
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise DescriptorError(f"{path}: {exc.strerror or exc}") from exc
        try:
            data: object = json.loads(raw)
        except ValueError as exc:
            raise DescriptorError(f"{path}: not JSON ({exc})") from exc
        if (
            not isinstance(data, dict)
            or cast("dict[str, Any]", data).get("format") != DESCRIPTOR_FORMAT
        ):
            raise DescriptorError(f"{path}: not a {DESCRIPTOR_FORMAT} descriptor")
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise DescriptorError(f"{path}: {exc.error_count()} invalid field(s)") from exc


class DescriptorError(ValueError):
    """A ``feed.json`` could not be read or does not validate."""


def read_intent_version(path: Path) -> int | None:
    """Only the ``intent_version`` of an existing descriptor, or ``None`` when unreadable."""
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    value = cast("dict[str, Any]", data).get("intent_version")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def build_descriptor(session: AsyncSession, feed_id: str) -> FeedDescriptor | None:
    """The descriptor of ``feed_id`` from the current session; ``None`` when the feed is gone."""
    feed = await FeedRepository(session).get(feed_id)
    if feed is None:
        return None
    items = await CatalogRepository(session).for_feed(feed_id)
    assets = await AssetRepository(session).for_feed(feed_id)
    requests_repo = RequestRepository(session)
    requests = await requests_repo.for_feed(feed_id)
    request_items = await requests_repo.item_ids_many([r.id for r in requests])
    return FeedDescriptor.from_rows(feed, items, assets, requests, request_items)


async def export_descriptor(
    session: AsyncSession, layout: Layout, feed_id: str, *, force: bool = False
) -> Path | None:
    """Write ``feed.json`` for ``feed_id`` unless the file already carries a newer intent.

    Returns the path written, or ``None`` when skipped (feed deleted, or the
    on-disk ``intent_version`` is newer than the database's).
    """
    descriptor = await build_descriptor(session, feed_id)
    if descriptor is None:
        return None
    path = layout.descriptor_path(feed_id)
    if not force:
        on_disk = read_intent_version(path)
        if on_disk is not None and on_disk > descriptor.intent_version:
            log.info(
                "descriptor.skip_newer",
                feed_id=feed_id,
                on_disk=on_disk,
                database=descriptor.intent_version,
            )
            return None
    layout.ensure_feed_dirs(feed_id)
    return await asyncio.to_thread(descriptor.write, path)


class DescriptorExporter:
    """Exports on its own session; the immediate policy used by ``UnitOfWork.after_commit``."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], layout: Layout) -> None:
        self._sessionmaker = sessionmaker
        self._layout = layout

    async def export(self, feed_id: str) -> Path | None:
        async with self._sessionmaker() as session:
            return await export_descriptor(session, self._layout, feed_id)

    async def flush(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


class DebouncedExporter(DescriptorExporter):
    """Coalesces exports per feed for ``delay`` seconds (archive jobs); ``flush`` on shutdown."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        layout: Layout,
        *,
        delay: float = DEBOUNCE_SECONDS,
    ) -> None:
        super().__init__(sessionmaker, layout)
        self._delay = delay
        self._pending: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def export(self, feed_id: str) -> Path | None:
        async with self._lock:
            if feed_id not in self._pending:
                self._pending[feed_id] = asyncio.create_task(self._later(feed_id))
        return None

    @property
    def pending(self) -> set[str]:
        return set(self._pending)

    async def _later(self, feed_id: str) -> None:
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            return
        await self._run(feed_id)

    async def _run(self, feed_id: str) -> None:
        async with self._lock:
            self._pending.pop(feed_id, None)
        try:
            await super().export(feed_id)
        except Exception:
            log.exception("descriptor.export_failed", feed_id=feed_id)

    async def flush(self) -> None:
        """Export every pending feed now (shutdown)."""
        async with self._lock:
            tasks = dict(self._pending)
            self._pending.clear()
        for task in tasks.values():
            task.cancel()
        for feed_id in tasks:
            await self._run(feed_id)

    async def aclose(self) -> None:
        await self.flush()


__all__ = [
    "DEBOUNCE_SECONDS",
    "DESCRIPTOR_FORMAT",
    "DebouncedExporter",
    "DescriptorAsset",
    "DescriptorError",
    "DescriptorExporter",
    "DescriptorFeed",
    "DescriptorItem",
    "DescriptorPolicy",
    "DescriptorRequest",
    "FeedDescriptor",
    "build_descriptor",
    "export_descriptor",
    "read_intent_version",
]
