"""``copycast rebuild``: reconstruct the database from the data directory.

Postgres is authoritative at runtime, but everything except telemetry can be
recovered from ``feeds/*/feed.json`` plus the files next to it. The rebuild
is idempotent: running it on a healthy database changes nothing.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
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
)
from copycast.domain.enums import (
    ArchiveState,
    AssetKind,
    AssetState,
    WantedReason,
)
from copycast.domain.exceptions import Conflict
from copycast.domain.ids import asset_id as make_asset_id
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
    default_inbox_id: str = ""


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
    """Rebuild the database from ``settings.data_dir``.

    ``yes`` also deletes database rows that have no trace on disk; without it
    they are only counted. ``lock_held`` says the caller (the worker's rebuild
    job) already holds ``WORKER_LOCK``; otherwise it is taken here and the
    rebuild is refused while the worker runs.
    """
    layout = Layout(settings.data_dir)
    version = layout.ensure_version()
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
    on_disk = layout.feed_ids_on_disk()
    counters.data["feeds_on_disk"] = len(on_disk)
    rebuilt: set[str] = set()

    for feed_id in on_disk:
        path = layout.descriptor_path(feed_id)
        if not path.is_file():
            counters.skipped.append(SkippedDir(feed_id=feed_id, reason="no feed.json"))
            continue
        try:
            descriptor = FeedDescriptor.read(path)
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
                await _rebuild_feed(session, layout, descriptor, yes, counters)
            rebuilt.add(feed_id)
        except Exception as exc:
            log.exception("rebuild.feed_failed", feed_id=feed_id)
            counters.skipped.append(SkippedDir(feed_id=feed_id, reason=f"error: {exc}"))

    async with sessionmaker() as session, session.begin():
        feeds = FeedRepository(session)
        for feed_id in await feeds.ids():
            if feed_id in rebuilt or layout.has_feed(feed_id):
                continue
            if yes:
                await feeds.delete(feed_id)
                counters.deleted_feeds.append(feed_id)
            else:
                counters.would_delete_feeds.append(feed_id)
        default = await feeds.ensure_default_inbox()
        counters.data["default_inbox_id"] = default.id

    async with sessionmaker() as session:
        for feed_id in [*rebuilt, counters.data["default_inbox_id"]]:
            if feed_id and not layout.descriptor_path(feed_id).is_file():
                await export_descriptor(session, layout, feed_id, force=True)
    return counters.report()


# --------------------------------------------------------------------------- one feed


async def _rebuild_feed(
    session: AsyncSession,
    layout: Layout,
    descriptor: FeedDescriptor,
    yes: bool,
    counters: _Counters,
) -> None:
    feed_id = descriptor.feed.id
    await _upsert_feed(session, layout, descriptor)
    counters.add("feeds_upserted")

    counters.add("items_upserted", await _upsert_items(session, descriptor))
    counters.add("assets_upserted", await _upsert_assets(session, descriptor))
    counters.add("requests_upserted", await _upsert_requests(session, descriptor))

    catalog = CatalogRepository(session)
    rows = {row.id: row for row in await catalog.for_feed(feed_id)}

    # Media: a file makes the row archived; a missing file makes archiving/archived rows wanted.
    known_stems: set[str] = set()
    for item in rows.values():
        media = layout.find_media(feed_id, item.id)
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
        xml_path = layout.item_xml_path(feed_id, item.id)
        if xml_path.is_file():
            await session.execute(
                update(CatalogItem)
                .where(CatalogItem.id == item.id)
                .values(source_item_xml=xml_path.read_text(encoding="utf-8"))
            )

    orphans = [
        path
        for path in layout.media_files(feed_id)
        if (stem := Layout.media_stem(path)) is not None and stem not in rows
    ]
    counters.add("rows_from_files", await _rows_from_files(session, layout, feed_id, orphans))

    counters.add("assets_from_files", await _reconcile_assets(session, layout, feed_id))

    source_xml = layout.source_xml_path(feed_id)
    if source_xml.is_file():
        await session.execute(
            update(Feed)
            .where(Feed.id == feed_id)
            .values(source_channel_xml=_channel_xml(source_xml.read_bytes()))
        )

    # Rows absent from disk: neither in the descriptor nor backed by a file.
    described_items = {i.id for i in descriptor.items}
    stale_items = [
        iid
        for iid in rows
        if iid not in described_items and layout.find_media(feed_id, iid) is None
    ]
    described_assets = {a.id for a in descriptor.assets}
    stale_assets = [
        a.id
        for a in (await session.execute(select(Asset).where(Asset.feed_id == feed_id))).scalars()
        if a.id not in described_assets
        and (not a.local_path or not layout.resolve(feed_id, a.local_path).is_file())
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


async def _upsert_feed(session: AsyncSession, layout: Layout, descriptor: FeedDescriptor) -> None:
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
        "description": feed.description,
        "author": feed.author,
        "artwork_url": feed.artwork_url,
        "language": feed.language,
        "source_url": feed.source_url,
        "source_dedup_key": feed.source_dedup_key,
        "source_kind": feed.source_kind.value if feed.source_kind else None,
        "service": feed.service,
        "backfill_mode": policy.backfill_mode.value if policy.backfill_mode else None,
        "backfill_latest_n": policy.backfill_latest_n,
        "follow": policy.follow,
        "paused": policy.paused,
        "policy_applied_at": policy.policy_applied_at,
        "engine_options": policy.engine_options,
        "autoprune_days": policy.autoprune_days,
        "intent_version": descriptor.intent_version,
        "created_at": feed.created_at,
    }
    stmt = insert(Feed).values(**values)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Feed.id],
            set_={k: v for k, v in values.items() if k not in ("id", "created_at")},
        )
    )
    layout.ensure_feed_dirs(feed.id)


async def _upsert_items(session: AsyncSession, descriptor: FeedDescriptor) -> int:
    if not descriptor.items:
        return 0
    feed_id = descriptor.feed.id
    rows = [_item_values(feed_id, item) for item in descriptor.items]
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


def _item_values(feed_id: str, item: DescriptorItem) -> dict[str, Any]:
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
        "duration_seconds": item.duration_seconds,
        "source_url": item.source_url,
        "archivable": item.archivable,
        "listed": item.listed,
        "first_seen_at": item.first_seen_at,
        "last_listed_at": item.last_listed_at,
        "archive_state": item.archive_state.value,
        "wanted_reason": item.wanted_reason.value if item.wanted_reason else None,
        "archived_at": item.archived_at,
        "deleted_at": item.deleted_at,
        "media_path": item.media_path,
        "media_mime": item.media_mime,
        "media_bytes": item.media_bytes,
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
    session: AsyncSession, layout: Layout, feed_id: str, item: CatalogItem, media: Path
) -> None:
    stat = await asyncio.to_thread(media.stat)
    relative = layout.relative(feed_id, media)
    ext = media.suffix.lstrip(".").lower()
    mime = item.media_mime if item.media_path == relative and item.media_mime else mime_for(ext)
    await session.execute(
        update(CatalogItem)
        .where(CatalogItem.id == item.id)
        .values(
            archive_state=ArchiveState.archived.value,
            media_path=relative,
            media_mime=mime,
            media_bytes=stat.st_size,
            archived_at=item.archived_at or datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            deleted_at=None,
        )
    )


async def _rows_from_files(
    session: AsyncSession, layout: Layout, feed_id: str, files: Sequence[Path]
) -> int:
    """Catalog rows for media files nobody knows about, ordinals appended by ``published_at``."""
    if not files:
        return 0
    catalog = CatalogRepository(session)
    drafts = [_draft_from_sidecars(layout, feed_id, path) for path in files]
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


def _draft_from_sidecars(layout: Layout, feed_id: str, media: Path) -> dict[str, Any]:
    stem = Layout.media_stem(media) or media.stem
    stat = media.stat()
    info: dict[str, Any] = {}
    info_path = media.with_name(f"{stem}{INFO_JSON_SUFFIX}")
    if info_path.is_file():
        try:
            loaded: object = json.loads(info_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                info = cast("dict[str, Any]", loaded)
        except ValueError:
            info = {}
    xml_path = media.with_name(f"{stem}{ITEM_XML_SUFFIX}")
    from_xml = _parse_item_xml(xml_path.read_bytes()) if xml_path.is_file() else {}

    published = from_xml.get("published_at") or _info_timestamp(info)
    source_key = from_xml.get("source_key") or _info_source_key(info) or f"urn:copycast:{stem}"
    title = from_xml.get("title") or _str(info.get("title")) or stem
    ext = media.suffix.lstrip(".").lower()
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
        "media_path": layout.relative(feed_id, media),
        "media_mime": mime_for(ext),
        "media_bytes": stat.st_size,
        "archived_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC),
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


async def _reconcile_assets(session: AsyncSession, layout: Layout, feed_id: str) -> int:
    """Stat every asset row's file; rows for files nobody knows about."""
    rows = list((await session.execute(select(Asset).where(Asset.feed_id == feed_id))).scalars())
    known_paths: set[str] = set()
    for asset in rows:
        if not asset.local_path:
            continue
        known_paths.add(asset.local_path)
        path = layout.resolve(feed_id, asset.local_path)
        if path.is_file():
            await session.execute(
                update(Asset)
                .where(Asset.id == asset.id)
                .values(state=AssetState.archived.value, size_bytes=path.stat().st_size)
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
    for path in layout.asset_files(feed_id):
        relative = layout.relative(feed_id, path)
        if relative in known_paths:
            continue
        parsed = Layout.parse_asset_name(path.name)
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
        )
        stmt = insert(Asset).values(
            id=aid,
            feed_id=feed_id,
            item_id=parsed.item_id,
            kind=parsed.kind.value,
            provenance=parsed.provenance.value,
            language=parsed.language,
            format=fmt,
            local_path=relative,
            mime=mime_for(parsed.ext),
            size_bytes=path.stat().st_size,
            state=AssetState.archived.value,
            fetched_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
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
    if kind is AssetKind.artwork:
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
    "RebuildRefused",
    "RebuildReport",
    "SkippedDir",
    "mime_for",
    "rebuild",
    "rebuild_blocking",
]
