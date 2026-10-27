"""``copycast rebuild``: reconstruct the database from the Object store.

Postgres is authoritative at runtime, but everything except telemetry can be
recovered from ``feeds/*/feed.json`` plus the objects next to it. Each feed's
prefix is listed once (:class:`FeedObjects`) and every media, sidecar and asset
lookup is answered from that listing. The rebuild is idempotent: running it on
a healthy database changes nothing.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from lxml import etree
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from copycast.adapters.db.locks import WORKER_LOCK, SessionLock
from copycast.adapters.db.migrate import ensure_schema
from copycast.adapters.db.models import Asset, CatalogItem, Feed, Request, RequestItem
from copycast.adapters.db.repositories import CatalogRepository, FeedRepository
from copycast.adapters.db.session import create_db_engine, create_sessionmaker
from copycast.adapters.storage.descriptor import (
    DescriptorError,
    DescriptorItem,
    FeedDescriptor,
    export_descriptor,
)
from copycast.adapters.storage.layout import (
    INFO_JSON_SUFFIX,
    ITEM_XML_SUFFIX,
    Layout,
    is_media_name,
)
from copycast.adapters.storage.objects import open_store
from copycast.application.ports import StoredObject
from copycast.domain.credentials import new_feed_password, new_feed_username
from copycast.domain.enums import (
    ArchiveState,
    AssetKind,
    AssetState,
    SourceKind,
    WantedReason,
)
from copycast.domain.exceptions import Conflict
from copycast.domain.ids import asset_id as make_asset_id
from copycast.domain.media import predicted_ext
from copycast.logging import get_logger
from copycast.settings import Settings

if TYPE_CHECKING:
    from lxml.etree import XmlElement as Element
else:  # lxml exposes no public element type at runtime
    Element = object

log = get_logger(__name__)

MEDIA_MIME: dict[str, str] = {
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "aac": "audio/aac",
    "mp3": "audio/mpeg",
    "opus": "audio/opus",
    "ogg": "audio/ogg",
    "oga": "audio/ogg",
    "webm": "audio/webm",
    "flac": "audio/flac",
    "wav": "audio/wav",
}

_XML_PARSER = etree.XMLParser(
    resolve_entities=False, no_network=True, load_dtd=False, recover=True, huge_tree=False
)
_ITEM_XML_NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
}


class RebuildRefused(Conflict):
    """The worker holds ``WORKER_LOCK``; a rebuild would race its jobs."""


class SkippedDir(BaseModel):
    model_config = ConfigDict(frozen=True)

    feed_id: str
    reason: str


class RebuildReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    layout_version: str
    yes: bool
    feeds_on_disk: int = 0
    feeds_upserted: int = 0
    items_upserted: int = 0
    assets_upserted: int = 0
    requests_upserted: int = 0
    media_archived: int = Field(default=0, description="rows set archived from a media file")
    media_reset_to_wanted: int = Field(default=0, description="rows lacking their media file")
    rows_from_files: int = Field(default=0, description="items created from orphan media files")
    assets_from_files: int = 0
    deleted_feeds: list[str] = Field(default_factory=list[str])
    deleted_items: int = 0
    deleted_assets: int = 0
    would_delete_feeds: list[str] = Field(default_factory=list[str])
    would_delete_items: int = 0
    would_delete_assets: int = 0
    skipped: list[SkippedDir] = Field(default_factory=list[SkippedDir])
    default_bucket_id: str = ""


@dataclass(frozen=True, slots=True)
class FeedObjects:
    """One listing of ``feeds/{feed_id}/``: every lookup of a rebuild answered from memory."""

    layout: Layout
    feed_id: str
    objects: dict[str, StoredObject]

    @classmethod
    def list(cls, layout: Layout, feed_id: str) -> FeedObjects:
        listed = layout.store.list(layout.feed_prefix(feed_id))
        return cls(layout, feed_id, {o.key: o for o in listed})

    def get(self, key: str) -> StoredObject | None:
        return self.objects.get(key)

    def resolve(self, relative: str) -> StoredObject | None:
        return self.objects.get(self.layout.resolve(self.feed_id, relative))

    def find_media(self, item_id: str) -> StoredObject | None:
        prefix = self.layout.media_prefix(self.feed_id) + f"{item_id}."
        for key in sorted(self.objects):
            if key.startswith(prefix) and is_media_name(self.objects[key].name):
                return self.objects[key]
        return None

    def media(self) -> list[StoredObject]:
        prefix = self.layout.media_prefix(self.feed_id)
        return [
            o
            for k, o in sorted(self.objects.items())
            if k.startswith(prefix) and is_media_name(o.name)
        ]

    def assets(self) -> list[StoredObject]:
        prefix = self.layout.assets_prefix(self.feed_id)
        return [
            o
            for k, o in sorted(self.objects.items())
            if k.startswith(prefix) and not o.name.startswith(".")
        ]

    def read(self, key: str) -> bytes | None:
        return self.layout.store.get_bytes(key) if key in self.objects else None


def _ext_of(obj: StoredObject) -> str:
    return obj.name.rsplit(".", 1)[-1].lower()


class _Counters:
    def __init__(self, layout_version: str, yes: bool) -> None:
        self.data: dict[str, Any] = {"layout_version": layout_version, "yes": yes}
        self.deleted_feeds: list[str] = []
        self.would_delete_feeds: list[str] = []
        self.skipped: list[SkippedDir] = []

    def add(self, key: str, count: int = 1) -> None:
        self.data[key] = self.data.get(key, 0) + count

    def report(self) -> RebuildReport:
        return RebuildReport(
            **self.data,
            deleted_feeds=self.deleted_feeds,
            would_delete_feeds=self.would_delete_feeds,
            skipped=self.skipped,
        )


async def rebuild(
    settings: Settings,
    yes: bool,
    lock_held: bool = False,
    *,
    db_engine: AsyncEngine | None = None,
) -> RebuildReport:
    """Rebuild the database from the Object store (``[storage] url``).

    ``yes`` also deletes database rows that have no trace in the Object store; without it
    they are only counted. ``lock_held`` says the caller (the worker's rebuild
    job) already holds ``WORKER_LOCK``; otherwise it is taken here and the
    rebuild is refused while the worker runs.
    """
    layout = Layout(open_store(settings.storage))
    version = await asyncio.to_thread(layout.ensure_version)
    own_engine = db_engine is None
    engine = db_engine or create_db_engine(settings, application_name="copycast-rebuild")
    try:
        await ensure_schema(engine)
        sessionmaker = create_sessionmaker(engine)
        if lock_held:
            return await _rebuild(sessionmaker, layout, version, yes)
        async with SessionLock(engine, WORKER_LOCK) as lock:
            if not lock.held:
                raise RebuildRefused(
                    "the worker is running (WORKER_LOCK is held); stop it or start the "
                    "rebuild through POST /api/admin/rebuild"
                )
            return await _rebuild(sessionmaker, layout, version, yes)
    finally:
        if own_engine:
            await engine.dispose()


async def _rebuild(
    sessionmaker: async_sessionmaker[AsyncSession], layout: Layout, version: str, yes: bool
) -> RebuildReport:
    counters = _Counters(version, yes)
    on_disk = await asyncio.to_thread(layout.feed_ids)
    counters.data["feeds_on_disk"] = len(on_disk)
    rebuilt: set[str] = set()
    # Descriptors written before feeds carried a Basic pair: the row's pair (kept or minted)
    # is written back at the end so the next rebuild finds it on disk.
    stale_pair: set[str] = set()

    for feed_id in on_disk:
        objects = await asyncio.to_thread(FeedObjects.list, layout, feed_id)
        key = layout.descriptor_key(feed_id)
        if objects.get(key) is None:
            counters.skipped.append(SkippedDir(feed_id=feed_id, reason="no feed.json"))
            continue
        try:
            descriptor = await asyncio.to_thread(FeedDescriptor.read, layout.store, key)
        except DescriptorError as exc:
            counters.skipped.append(SkippedDir(feed_id=feed_id, reason=str(exc)))
            continue
        if descriptor.feed.id != feed_id:
            counters.skipped.append(
                SkippedDir(feed_id=feed_id, reason=f"feed.json belongs to {descriptor.feed.id!r}")
            )
            continue
        try:
            async with sessionmaker() as session, session.begin():
                await _rebuild_feed(session, objects, descriptor, yes, counters)
            rebuilt.add(feed_id)
            if not _carries_pair(descriptor):
                stale_pair.add(feed_id)
        except Exception as exc:
            log.exception("rebuild.feed_failed", feed_id=feed_id)
            counters.skipped.append(SkippedDir(feed_id=feed_id, reason=f"error: {exc}"))

    async with sessionmaker() as session, session.begin():
        feeds = FeedRepository(session)
        listed = set(on_disk)
        for feed_id in await feeds.ids():
            if feed_id in rebuilt or feed_id in listed:
                continue
            if yes:
                await feeds.delete(feed_id)
                counters.deleted_feeds.append(feed_id)
            else:
                counters.would_delete_feeds.append(feed_id)
        default = await feeds.ensure_default_bucket()
        counters.data["default_bucket_id"] = default.id

    async with sessionmaker() as session:
        for feed_id in [*rebuilt, counters.data["default_bucket_id"]]:
            if not feed_id:
                continue
            key = layout.descriptor_key(feed_id)
            described = await asyncio.to_thread(layout.store.exists, key)
            if feed_id in stale_pair or not described:
                await export_descriptor(session, layout, feed_id, force=True)
    return counters.report()


def _carries_pair(descriptor: FeedDescriptor) -> bool:
    return descriptor.feed.auth_username is not None and descriptor.feed.auth_password is not None


# --------------------------------------------------------------------------- one feed


async def _rebuild_feed(
    session: AsyncSession,
    objects: FeedObjects,
    descriptor: FeedDescriptor,
    yes: bool,
    counters: _Counters,
) -> None:
    feed_id = descriptor.feed.id
    layout = objects.layout
    await _upsert_feed(session, descriptor)
    counters.add("feeds_upserted")

    counters.add("items_upserted", await _upsert_items(session, objects, descriptor))
    counters.add("assets_upserted", await _upsert_assets(session, descriptor))
    counters.add("requests_upserted", await _upsert_requests(session, descriptor))

    catalog = CatalogRepository(session)
    rows = {row.id: row for row in await catalog.for_feed(feed_id)}

    # Media: an object makes the row archived; a missing one makes archiving/archived rows
    # wanted.
    known_stems: set[str] = set()
    for item in rows.values():
        media = objects.find_media(item.id)
        if media is not None:
            known_stems.add(item.id)
            await _mark_archived_from_file(session, layout, feed_id, item, media)
            counters.add("media_archived")
        elif item.archive_state in (ArchiveState.archiving.value, ArchiveState.archived.value):
            await session.execute(
                update(CatalogItem)
                .where(CatalogItem.id == item.id)
                .values(
                    archive_state=ArchiveState.wanted.value,
                    wanted_reason=item.wanted_reason or WantedReason.backfill.value,
                    media_path=None,
                    media_mime=None,
                    media_bytes=None,
                    archived_at=None,
                )
            )
            counters.add("media_reset_to_wanted")
        item_xml = await asyncio.to_thread(objects.read, layout.item_xml_key(feed_id, item.id))
        if item_xml is not None:
            await session.execute(
                update(CatalogItem)
                .where(CatalogItem.id == item.id)
                .values(source_item_xml=item_xml.decode("utf-8", errors="replace"))
            )

    orphans = [
        obj
        for obj in objects.media()
        if (stem := Layout.media_stem(obj.name)) is not None and stem not in rows
    ]
    counters.add("rows_from_files", await _rows_from_files(session, objects, orphans))

    counters.add("assets_from_files", await _reconcile_assets(session, objects))

    source_xml = await asyncio.to_thread(objects.read, layout.source_xml_key(feed_id))
    if source_xml is not None:
        await session.execute(
            update(Feed)
            .where(Feed.id == feed_id)
            .values(source_channel_xml=_channel_xml(source_xml))
        )

    # Rows absent from the Object store: neither in the descriptor nor backed by an object.
    described_items = {i.id for i in descriptor.items}
    stale_items = [
        iid for iid in rows if iid not in described_items and objects.find_media(iid) is None
    ]
    described_assets = {a.id for a in descriptor.assets}
    stale_assets = [
        a.id
        for a in (await session.execute(select(Asset).where(Asset.feed_id == feed_id))).scalars()
        if a.id not in described_assets
        and (not a.local_path or objects.resolve(a.local_path) is None)
    ]
    if yes:
        if stale_items:
            await session.execute(delete(CatalogItem).where(CatalogItem.id.in_(stale_items)))
            counters.add("deleted_items", len(stale_items))
        if stale_assets:
            await session.execute(delete(Asset).where(Asset.id.in_(stale_assets)))
            counters.add("deleted_assets", len(stale_assets))
    else:
        counters.add("would_delete_items", len(stale_items))
        counters.add("would_delete_assets", len(stale_assets))

    feeds = FeedRepository(session)
    await feeds.recount_storage(feed_id)
    await feeds.bump_revision(feed_id)


async def _upsert_feed(session: AsyncSession, descriptor: FeedDescriptor) -> None:
    feed, policy = descriptor.feed, descriptor.policy
    if feed.is_default:
        await session.execute(
            update(Feed)
            .where(Feed.is_default.is_(True), Feed.id != feed.id)
            .values(is_default=False)
        )
    values: dict[str, Any] = {
        "id": feed.id,
        "kind": feed.kind.value,
        "is_default": feed.is_default,
        "title": feed.title,
        "title_override": feed.title_override,
        "description": feed.description,
        "author": feed.author,
        "artwork_url": feed.artwork_url,
        "language": feed.language,
        "source_url": feed.source_url,
        "source_dedup_key": feed.source_dedup_key,
        "source_kind": feed.source_kind.value if feed.source_kind else None,
        "service": feed.service,
        # A descriptor without a pair (written before ADR 0011) mints one for a new row only:
        # an existing row keeps the pair podcast clients already hold (see ``set_`` below).
        "auth_username": feed.auth_username or new_feed_username(),
        "auth_password": feed.auth_password or new_feed_password(),
        "backfill_mode": policy.backfill_mode.value if policy.backfill_mode else None,
        "backfill_latest_n": policy.backfill_latest_n,
        "min_duration_seconds": policy.min_duration_seconds,
        "preferred_language": policy.preferred_language,
        "retention_days": policy.retention_days,
        "refresh_interval_hours": policy.refresh_interval_hours,
        "sync_deletions": policy.sync_deletions,
        "playlist_capture": policy.playlist_capture,
        "serial": policy.serial,
        "follow": policy.follow,
        "paused": policy.paused,
        "policy_applied_at": policy.policy_applied_at,
        "engine_options": policy.engine_options,
        "autoprune_days": policy.autoprune_days,
        "intent_version": descriptor.intent_version,
        "created_at": feed.created_at,
    }
    untouched = ("id", "created_at")
    if not _carries_pair(descriptor):
        untouched += ("auth_username", "auth_password")
    stmt = insert(Feed).values(**values)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Feed.id],
            set_={k: v for k, v in values.items() if k not in untouched},
        )
    )


async def _upsert_items(
    session: AsyncSession, objects: FeedObjects, descriptor: FeedDescriptor
) -> int:
    if not descriptor.items:
        return 0
    feed_id = descriptor.feed.id
    source_kind = descriptor.feed.source_kind
    rows = [
        _item_values(feed_id, item, _restored_public_ext(objects, source_kind, item))
        for item in descriptor.items
    ]
    # Ordinals are unique per feed: park conflicting existing rows first so an
    # upsert never trips over a row that moved (rebuild never renumbers).
    ordinals = {row["ordinal"] for row in rows}
    ids = {row["id"] for row in rows}
    await session.execute(
        update(CatalogItem)
        .where(
            CatalogItem.feed_id == feed_id,
            CatalogItem.ordinal.in_(ordinals),
            CatalogItem.id.not_in(ids),
        )
        .values(ordinal=-CatalogItem.ordinal - 1_000_000)
    )
    for chunk in _chunks(rows, 500):
        stmt = insert(CatalogItem).values(chunk)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[CatalogItem.id],
                set_={k: getattr(stmt.excluded, k) for k in chunk[0] if k not in ("id", "feed_id")},
            )
        )
    return len(rows)


def _restored_public_ext(
    objects: FeedObjects, source_kind: SourceKind | None, item: DescriptorItem
) -> str:
    """The item's stable URL extension: as written, else its media file's, else predicted.

    Descriptors written before 1.3 carry no ``public_ext``; the media file (when there
    is one) is what apps have been downloading, so its extension is the URL they hold.
    The rest get ``predicted_ext`` by Source kind alone (``mp3`` for RSS, the 1.2
    placeholder; ``m4a`` for an Engine Source and a Bucket), the rule migration 0010
    applied to the same rows in Postgres, so a rebuild advertises the same URLs.
    """
    if item.public_ext:
        return item.public_ext
    media = objects.find_media(item.id)
    if media is not None:
        return _ext_of(media)
    return predicted_ext(source_kind)


def _item_values(feed_id: str, item: DescriptorItem, public_ext: str) -> dict[str, Any]:
    return {
        "id": item.id,
        "feed_id": feed_id,
        "source_key": item.source_key,
        "ordinal": item.ordinal,
        "source_number": item.source_number,
        "source_season": item.source_season,
        "source_position": item.source_position,
        "tab": item.tab,
        "title": item.title,
        "description": item.description,
        "author": item.author,
        "artwork_url": item.artwork_url,
        "published_at": item.published_at,
        "published_at_approximate": item.published_at_approximate,
        "duration_seconds": item.duration_seconds,
        "source_url": item.source_url,
        "archivable": item.archivable,
        "live_status": item.live_status.value if item.live_status else None,
        "listed": item.listed,
        "first_seen_at": item.first_seen_at,
        "last_listed_at": item.last_listed_at,
        "archive_state": item.archive_state.value,
        "wanted_reason": item.wanted_reason.value if item.wanted_reason else None,
        "archive_flag": item.archive_flag,
        "archived_at": item.archived_at,
        "deleted_at": item.deleted_at,
        "media_path": item.media_path,
        "media_mime": item.media_mime,
        "media_bytes": item.media_bytes,
        "public_ext": public_ext,
    }


async def _upsert_assets(session: AsyncSession, descriptor: FeedDescriptor) -> int:
    if not descriptor.assets:
        return 0
    feed_id = descriptor.feed.id
    known_items = {i.id for i in descriptor.items}
    rows = [
        {
            "id": a.id,
            "feed_id": feed_id,
            "item_id": a.item_id,
            "kind": a.kind.value,
            "provenance": a.provenance.value,
            "language": a.language,
            "format": a.format.value if a.format else None,
            "slot": a.slot,
            "remote_url": a.remote_url,
            "local_path": a.local_path,
            "mime": a.mime,
            "size_bytes": a.size_bytes,
            "state": a.state.value,
            "fetched_at": a.fetched_at,
            "created_at": a.created_at,
        }
        for a in descriptor.assets
        if a.item_id is None or a.item_id in known_items
    ]
    if not rows:
        return 0
    for chunk in _chunks(rows, 500):
        stmt = insert(Asset).values(chunk)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[Asset.id],
                set_={
                    k: getattr(stmt.excluded, k)
                    for k in chunk[0]
                    if k not in ("id", "feed_id", "created_at")
                },
            )
        )
    return len(rows)


async def _upsert_requests(session: AsyncSession, descriptor: FeedDescriptor) -> int:
    if not descriptor.requests:
        return 0
    feed_id = descriptor.feed.id
    known_items = {i.id for i in descriptor.items}
    rows = [
        {
            "id": r.id,
            "feed_id": feed_id,
            "url": r.url,
            "requested_via": r.requested_via.value,
            "status": r.status.value,
            "item_count": r.item_count,
            "error": r.error,
            "expanded_at": r.expanded_at,
            "created_at": r.created_at,
        }
        for r in descriptor.requests
    ]
    stmt = insert(Request).values(rows)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Request.id],
            set_={k: getattr(stmt.excluded, k) for k in rows[0] if k not in ("id", "created_at")},
        )
    )
    links = [
        {"request_id": r.id, "item_id": iid}
        for r in descriptor.requests
        for iid in r.item_ids
        if iid in known_items
    ]
    if links:
        await session.execute(insert(RequestItem).values(links).on_conflict_do_nothing())
    return len(rows)


async def _mark_archived_from_file(
    session: AsyncSession, layout: Layout, feed_id: str, item: CatalogItem, media: StoredObject
) -> None:
    relative = layout.relative(feed_id, media.key)
    ext = _ext_of(media)
    mime = item.media_mime if item.media_path == relative and item.media_mime else mime_for(ext)
    await session.execute(
        update(CatalogItem)
        .where(CatalogItem.id == item.id)
        .values(
            archive_state=ArchiveState.archived.value,
            media_path=relative,
            media_mime=mime,
            media_bytes=media.size,
            archived_at=item.archived_at or media.last_modified,
            deleted_at=None,
        )
    )


async def _rows_from_files(
    session: AsyncSession, objects: FeedObjects, files: Sequence[StoredObject]
) -> int:
    """Catalog rows for media objects nobody knows about, ordinals appended by ``published_at``."""
    if not files:
        return 0
    feed_id = objects.feed_id
    catalog = CatalogRepository(session)
    drafts = [await asyncio.to_thread(_draft_from_sidecars, objects, obj) for obj in files]
    drafts.sort(key=lambda d: (d["published_at"] or datetime.min.replace(tzinfo=UTC), d["id"]))
    next_ordinal = await catalog.max_ordinal(feed_id) + 1
    now = datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    for offset, draft in enumerate(drafts):
        rows.append(
            {
                **draft,
                "feed_id": feed_id,
                "ordinal": next_ordinal + offset,
                "archivable": True,
                "listed": False,
                "first_seen_at": draft["published_at"] or now,
                "last_listed_at": draft["published_at"] or now,
                "archive_state": ArchiveState.archived.value,
                "archived_at": draft.pop("archived_at"),
            }
        )
    await session.execute(insert(CatalogItem).values(rows).on_conflict_do_nothing())
    return len(rows)


def _draft_from_sidecars(objects: FeedObjects, media: StoredObject) -> dict[str, Any]:
    layout, feed_id = objects.layout, objects.feed_id
    stem = Layout.media_stem(media.name) or media.name.partition(".")[0]
    info: dict[str, Any] = {}
    info_raw = objects.read(layout.media_prefix(feed_id) + f"{stem}{INFO_JSON_SUFFIX}")
    if info_raw is not None:
        try:
            loaded: object = json.loads(info_raw)
            if isinstance(loaded, dict):
                info = cast("dict[str, Any]", loaded)
        except ValueError:
            info = {}
    xml_raw = objects.read(layout.media_prefix(feed_id) + f"{stem}{ITEM_XML_SUFFIX}")
    from_xml = _parse_item_xml(xml_raw) if xml_raw is not None else {}

    published = from_xml.get("published_at") or _info_timestamp(info)
    source_key = from_xml.get("source_key") or _info_source_key(info) or f"urn:copycast:{stem}"
    title = from_xml.get("title") or _str(info.get("title")) or stem
    ext = _ext_of(media)
    return {
        "id": stem,
        "source_key": source_key,
        "title": title,
        "description": from_xml.get("description") or _str(info.get("description")),
        "author": _str(info.get("artist") or info.get("uploader") or info.get("channel")),
        "artwork_url": _str(info.get("thumbnail")),
        "published_at": published,
        "duration_seconds": _int(info.get("duration")) or from_xml.get("duration_seconds"),
        "source_url": from_xml.get("source_url") or _str(info.get("webpage_url")),
        "source_number": _int(info.get("episode_number") or info.get("playlist_index")),
        "source_season": _int(info.get("season_number")),
        "source_item_xml": from_xml.get("xml"),
        "media_path": layout.relative(feed_id, media.key),
        "media_mime": mime_for(ext),
        "media_bytes": media.size,
        "public_ext": ext,
        "archived_at": media.last_modified,
    }


def _parse_item_xml(raw: bytes) -> dict[str, Any]:
    """Identity and metadata of a preserved ``<item>``: guid text, else the enclosure URL."""
    element = _parse_xml(raw)
    if element is None:
        return {}
    guid = element.findtext("guid")
    enclosure = element.find("enclosure")
    enclosure_url = enclosure.get("url") if enclosure is not None else None
    key = (guid or "").strip() or (enclosure_url or "").strip()
    duration = element.findtext("itunes:duration", namespaces=_ITEM_XML_NS)
    return {
        "source_key": key or None,
        "title": (element.findtext("title") or "").strip() or None,
        "description": (element.findtext("description") or "").strip() or None,
        "source_url": (element.findtext("link") or "").strip() or None,
        "published_at": _rfc2822(element.findtext("pubDate")),
        "duration_seconds": _itunes_duration(duration),
        "xml": raw.decode("utf-8", errors="replace"),
    }


def _channel_xml(raw: bytes) -> str | None:
    """``source/feed.xml`` minus its items, the shape ``source_channel_xml`` stores."""
    root = _parse_xml(raw)
    if root is None:
        return None
    channel = root.find("channel")
    if channel is None:
        return None
    for item in channel.findall("item"):
        channel.remove(item)
    return etree.tostring(root, encoding="unicode")


async def _reconcile_assets(session: AsyncSession, objects: FeedObjects) -> int:
    """Check every asset row's object; rows for objects nobody knows about."""
    layout, feed_id = objects.layout, objects.feed_id
    rows = list((await session.execute(select(Asset).where(Asset.feed_id == feed_id))).scalars())
    known_paths: set[str] = set()
    for asset in rows:
        if not asset.local_path:
            continue
        known_paths.add(asset.local_path)
        found = objects.resolve(asset.local_path)
        if found is not None:
            await session.execute(
                update(Asset)
                .where(Asset.id == asset.id)
                .values(state=AssetState.archived.value, size_bytes=found.size)
            )
        elif asset.state == AssetState.archived.value:
            await session.execute(
                update(Asset)
                .where(Asset.id == asset.id)
                .values(state=AssetState.wanted.value, size_bytes=None)
            )
    created = 0
    item_ids = set(
        (
            await session.execute(select(CatalogItem.id).where(CatalogItem.feed_id == feed_id))
        ).scalars()
    )
    for obj in objects.assets():
        relative = layout.relative(feed_id, obj.key)
        if relative in known_paths:
            continue
        parsed = Layout.parse_asset_name(obj.name)
        if parsed is None or (parsed.item_id is not None and parsed.item_id not in item_ids):
            continue
        fmt = _asset_format(parsed.kind, parsed.ext)
        aid = make_asset_id(
            feed_id,
            parsed.item_id,
            parsed.kind.value,
            parsed.language,
            fmt,
            parsed.provenance.value,
            parsed.slot,
        )
        stmt = insert(Asset).values(
            id=aid,
            feed_id=feed_id,
            item_id=parsed.item_id,
            kind=parsed.kind.value,
            provenance=parsed.provenance.value,
            language=parsed.language,
            format=fmt,
            slot=parsed.slot,
            local_path=relative,
            mime=mime_for(parsed.ext),
            size_bytes=obj.size,
            state=AssetState.archived.value,
            fetched_at=obj.last_modified,
        )
        await session.execute(stmt.on_conflict_do_nothing())
        created += 1
    return created


# --------------------------------------------------------------------------- helpers


def _parse_xml(raw: bytes) -> Element | None:
    """Parse with the hardened, recovering parser; ``None`` for hopeless input."""
    try:
        return cast("Element | None", etree.fromstring(raw, parser=_XML_PARSER))
    except etree.XMLSyntaxError:
        return None


def mime_for(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    if ext in MEDIA_MIME:
        return MEDIA_MIME[ext]
    guessed, _ = mimetypes.guess_type(f"x.{ext}")
    return guessed or "application/octet-stream"


def _asset_format(kind: AssetKind, ext: str) -> str | None:
    if kind is AssetKind.artwork or kind is AssetKind.attachment:
        return None
    if kind is AssetKind.chapters:
        return "json"
    lowered = ext.lower()
    return {"vtt": "vtt", "srt": "srt", "json": "json", "txt": "text", "html": "html"}.get(lowered)


def _info_source_key(info: dict[str, Any]) -> str | None:
    key = _str(info.get("copycast_source_key"))
    if key:
        return key
    extractor_key = _str(info.get("extractor_key"))
    ident = _str(info.get("id"))
    extractor = _str(info.get("extractor")) or ""
    if extractor_key and ident and not extractor.startswith("copycast:"):
        return f"{extractor_key}:{ident}"
    return _str(info.get("original_url")) or _str(info.get("url")) or _str(info.get("webpage_url"))


def _info_timestamp(info: dict[str, Any]) -> datetime | None:
    for key in ("timestamp", "release_timestamp"):
        value = info.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return datetime.fromtimestamp(float(value), tz=UTC)
    upload_date = _str(info.get("upload_date"))
    if upload_date and len(upload_date) == 8 and upload_date.isdigit():
        return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=UTC)
    return None


def _rfc2822(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    from email.utils import parsedate_to_datetime

    try:
        parsed = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _itunes_duration(value: str | None) -> int | None:
    if not value or not value.strip():
        return None
    parts = value.strip().split(":")
    try:
        numbers = [int(float(p)) for p in parts]
    except ValueError:
        return None
    total = 0
    for number in numbers:
        total = total * 60 + number
    return total


def _str(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _chunks(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def rebuild_blocking(settings: Settings, yes: bool) -> RebuildReport:
    return asyncio.run(rebuild(settings, yes))


__all__ = [
    "MEDIA_MIME",
    "FeedObjects",
    "RebuildRefused",
    "RebuildReport",
    "SkippedDir",
    "mime_for",
    "rebuild",
    "rebuild_blocking",
]
