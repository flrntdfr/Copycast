"""Uploads and Serial Buckets (ADR 0015).

An upload is staged in the Object store, not in Postgres: ``create_upload``
writes ``uploads/<id>/upload.json`` with a digest of a one-time token and
returns the upload URL; ``put_upload`` streams the bytes into a Scratch file,
reads its tags there (they need a seekable file) and uploads it to
``uploads/<id>/file.<ext>``; ``create_serial_bucket`` copies every named upload
(server-side) into a new Bucket as an archived Episode, in the order the agent
gave, and removes the staged objects; ``add_uploads`` does the same into an existing Bucket, in
natural file-name order, dated by arrival (ADR 0018). Staged uploads older
than :data:`UPLOAD_TTL` are swept on the next ``create_upload`` or ``put_upload`` and by
the scheduler (:func:`sweep_expired_uploads`).

An upload belongs to the User who staged it (``owner`` in ``upload.json``; files staged
before 3.0 belong to the owner of existing data): another User's upload is NotFound,
and an Admin may use anyone's, but only into a Feed of that upload's Owner. Quotas
(ADR 0029): staged uploads not yet added count against the room left (a received one
by its size, a pending one by its declared size), and an Owner may hold at most
:data:`MAX_STAGED_UPLOADS` of them, so staging alone cannot fill the Object store.
``create_upload`` checks the declared ``size_bytes``, ``put_upload`` stops at the room
left, and ``add_uploads`` and ``create_serial_bucket`` check the sum of the batch before
copying anything. The PUT's final check and the batch checks run under the Owner's
quota lock (``uow.quota.lock_owner``), so parallel calls cannot overshoot either.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast

from copycast.application.capabilities import capability
from copycast.application.events import FeedEvent
from copycast.application.models import (
    AudioTags,
    BucketRead,
    QuotaStatus,
    SerialBucketCreate,
    SerialEpisode,
    UploadCreate,
    UploadRead,
    UploadsAdd,
)
from copycast.application.services.buckets import bucket_read_for
from copycast.application.services.context import (
    ObjectStorePort,
    Scope,
    ServiceContext,
    UnitOfWorkPort,
)
from copycast.application.services.feeds import require_bucket
from copycast.application.services.owners import acting_owner
from copycast.application.services.quota import over_quota, quota_status, require_room
from copycast.domain.enums import ArchiveState, FeedKind, ListingOrder
from copycast.domain.exceptions import Conflict, NotFound, Unsupported
from copycast.domain.ids import bucket_id
from copycast.domain.listing import SourceListing, SourceListingItem
from copycast.domain.media import MPEG4_CONTAINERS, PODCAST_EXTS, mime_for_ext
from copycast.domain.ordering import natural_key
from copycast.domain.quota import format_size
from copycast.domain.serial import SerialEntry, numbered_title, posting_dates, progress_notes
from copycast.logging import get_logger

log = get_logger(__name__)

UPLOAD_TTL: Final = timedelta(hours=24)
MAX_UPLOAD_BYTES: Final = 4 * 1024**3
MAX_STAGED_UPLOADS: Final = 1000
"""Uploads one Owner may have staged and not yet added (pending or received)."""
UPLOAD_SERVICE: Final = "Upload"
UPLOAD_PATH: Final = "/uploads"
_META: Final = "upload.json"
_AUDIOBOOK_EXTS: Final = frozenset({"m4b"})
"""AAC in MPEG-4 under an audiobook extension: served as m4a, the bytes unchanged."""


def _now() -> datetime:
    return datetime.now(UTC)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def served_ext(filename: str) -> str | None:
    """The extension a podcast app gets for ``filename``: mp3 and m4a as they are, an MPEG-4
    audio container (m4b, mp4) as m4a; ``None`` for anything that would need transcoding."""
    _, dot, ext = filename.rpartition(".")
    ext = ext.lower() if dot else ""
    if ext in PODCAST_EXTS:
        return ext
    if ext in MPEG4_CONTAINERS or ext in _AUDIOBOOK_EXTS:
        return "m4a"
    return None


class _Staged:
    """One ``uploads/<id>/`` prefix of the Object store."""

    def __init__(self, store: ObjectStorePort, root: str, upload_id: str) -> None:
        if not upload_id.isalnum():
            raise NotFound("upload", upload_id)
        self.store = store
        self.id = upload_id
        self.prefix = f"{root}{upload_id}/"
        self.meta_key = self.prefix + _META

    def load(self) -> dict[str, Any]:
        try:
            body = self.store.get_bytes(self.meta_key)
            loaded: Any = json.loads(body) if body is not None else None
        except (OSError, ValueError):
            raise NotFound("upload", self.id) from None
        if not isinstance(loaded, dict):
            raise NotFound("upload", self.id)
        return cast("dict[str, Any]", loaded)

    def save(self, meta: dict[str, Any]) -> None:
        self.store.put_bytes(self.meta_key, json.dumps(meta), content_type="application/json")

    def file(self, meta: dict[str, Any]) -> str:
        return self.prefix + str(meta["file"])


def _staged(ctx: ServiceContext, upload_id: str) -> _Staged:
    return _Staged(ctx.layout.store, ctx.layout.uploads_prefix(), upload_id)


def _owner(ctx: ServiceContext, meta: dict[str, Any]) -> str:
    """The upload's Owner; an upload staged before 3.0 has none and is the existing data's."""
    owner = meta.get("owner")
    return owner if isinstance(owner, str) and owner else ctx.users.owner_of_existing


def _check_visible(ctx: ServiceContext, upload_id: str, meta: dict[str, Any]) -> None:
    """NotFound unless the caller may see the upload: its own, or anyone's for an Admin
    session and SYSTEM (an API key sees its owner's only, like Feeds)."""
    visible = Scope.for_principal(ctx.principal).visible_owner
    if visible is not None and _owner(ctx, meta) != visible:
        raise NotFound("upload", upload_id)


def _read(ctx: ServiceContext, meta: dict[str, Any], token: str | None = None) -> UploadRead:
    tags = meta.get("tags")
    url = None
    if token is not None:
        url = f"{ctx.settings.base_url.rstrip('/')}{UPLOAD_PATH}/{meta['id']}/{token}"
    return UploadRead(
        id=meta["id"],
        filename=meta["filename"],
        state=meta["state"],
        upload_url=url,
        size_bytes=meta.get("size_bytes"),
        tags=AudioTags.model_validate(tags) if tags is not None else None,
        owner=_owner(ctx, meta),
        created_at=datetime.fromisoformat(meta["created_at"]),
    )


@dataclass(slots=True)
class _StagedUsage:
    """What one Owner holds under ``uploads/``: how many uploads, and their bytes."""

    count: int = 0
    bytes: int = 0


def _staged_bytes(meta: dict[str, Any]) -> int:
    """A received upload's size, else the size its ``create_upload`` declared (or 0)."""
    size = meta.get("size_bytes") if meta.get("state") == "received" else None
    if size is None:
        size = meta.get("declared_bytes")
    return size if isinstance(size, int) and not isinstance(size, bool) and size > 0 else 0


def _scan(
    store: ObjectStorePort,
    root: str,
    now: datetime,
    *,
    owner: str | None = None,
    default_owner: str = "",
    exclude: str | None = None,
) -> _StagedUsage:
    """Remove staged uploads older than :data:`UPLOAD_TTL` (abandoned batches) and count
    what ``owner`` still has staged, ``exclude`` aside (uploads with no owner are
    ``default_owner``'s, as :func:`_owner` reads them)."""
    usage = _StagedUsage()
    for name in store.list_dirs(root):
        prefix = f"{root}{name}/"
        objects = store.list(prefix)
        if not objects:
            continue
        meta: Any = {}
        try:
            body = store.get_bytes(prefix + _META)
            meta = json.loads(body) if body is not None else {}
            stale = now - datetime.fromisoformat(str(meta["created_at"])) > UPLOAD_TTL
        except (OSError, ValueError, KeyError, TypeError):
            stale = now - max(o.last_modified for o in objects) > UPLOAD_TTL
        if stale:
            store.delete_prefix(prefix)
            continue
        if owner is None or name == exclude or not isinstance(meta, dict):
            continue
        found = cast("dict[str, Any]", meta)
        staged_by = found.get("owner")
        if (staged_by if isinstance(staged_by, str) and staged_by else default_owner) == owner:
            usage.count += 1
            usage.bytes += _staged_bytes(found)
    return usage


async def _staged_usage(
    ctx: ServiceContext, owner: str, now: datetime, *, exclude: str | None = None
) -> _StagedUsage:
    return await asyncio.to_thread(
        _scan,
        ctx.layout.store,
        ctx.layout.uploads_prefix(),
        now,
        owner=owner,
        default_owner=ctx.users.owner_of_existing,
        exclude=exclude,
    )


def sweep_expired_uploads(store: ObjectStorePort, root: str, now: datetime | None = None) -> None:
    """Remove every staged upload older than :data:`UPLOAD_TTL` (the scheduler's sweep)."""
    _scan(store, root, now or _now())


def _room(status: QuotaStatus, staged: int) -> int | None:
    """What the Owner may still stage: the quota less usage and what is already staged."""
    if status.quota_bytes is None:
        return None
    return max(0, status.quota_bytes - status.used_bytes - staged)


def _does_not_fit(status: QuotaStatus, adding: int, room: int) -> Exception:
    return over_quota(
        status,
        f"over quota: {format_size(adding)} do not fit in the {format_size(room)} left "
        "(uploads staged and not yet added count); free some space, add or let staged "
        "uploads expire, or ask for a larger quota",
    )


@capability("create_upload", request=UploadCreate, response=UploadRead)
async def create_upload(ctx: ServiceContext, body: UploadCreate) -> UploadRead:
    """Stage one audio file: returns the one-time ``upload_url`` to PUT its bytes to."""
    if served_ext(body.filename) is None:
        raise Unsupported(f"{body.filename!r} is not mp3, m4a or m4b; convert it before uploading")
    owner = acting_owner(ctx)
    async with ctx.uow_factory() as uow:
        status = await require_room(uow, ctx.users, owner, adding_bytes=body.size_bytes or 0)
    now = _now()
    staged_now = await _staged_usage(ctx, owner, now)
    if staged_now.count >= MAX_STAGED_UPLOADS:
        raise Conflict(
            f"{staged_now.count} uploads are staged and not yet added, the most one User may "
            "hold; add them to a Bucket or let them expire (24 h)"
        )
    room = _room(status, staged_now.bytes)
    declared = body.size_bytes or 0
    if room is not None and (declared > room or room == 0):
        raise _does_not_fit(status, declared, room)
    upload_id = secrets.token_hex(8)
    token = secrets.token_urlsafe(24)
    staged = _staged(ctx, upload_id)
    _, _, ext = body.filename.rpartition(".")
    meta: dict[str, Any] = {
        "id": upload_id,
        "filename": body.filename,
        "file": f"file.{ext.lower()}",
        "token_sha256": _digest(token),
        "state": "pending",
        "owner": owner,
        "declared_bytes": body.size_bytes,
        "created_at": now.isoformat(),
    }
    await asyncio.to_thread(staged.save, meta)
    log.info("upload.created", upload_id=upload_id, filename=body.filename, owner=owner)
    return _read(ctx, meta, token)


@capability("put_upload", response=UploadRead)
async def put_upload(
    ctx: ServiceContext, upload_id: str, token: str, chunks: AsyncIterator[bytes]
) -> UploadRead:
    """Receive an upload's bytes (once) and read its tags."""
    staged = _staged(ctx, upload_id)
    meta = await asyncio.to_thread(staged.load)
    if not hmac.compare_digest(_digest(token), str(meta.get("token_sha256", ""))):
        raise NotFound("upload", upload_id)
    if meta["state"] != "pending":
        raise Conflict(f"upload {upload_id} was already received")
    owner = _owner(ctx, meta)
    async with ctx.uow_factory() as uow:
        status = await require_room(uow, ctx.users, owner)
    others = await _staged_usage(ctx, owner, _now(), exclude=upload_id)
    room = _room(status, others.bytes)
    partial = ctx.scratch.temp_file(f".{str(meta['file']).rpartition('.')[2]}")
    size = 0
    try:
        with partial.open("wb") as out:
            async for chunk in chunks:
                size += len(chunk)
                if room is not None and size > room and room < MAX_UPLOAD_BYTES:
                    raise _does_not_fit(status, size, room)
                if size > MAX_UPLOAD_BYTES:
                    raise Unsupported(f"an upload is limited to {MAX_UPLOAD_BYTES} bytes")
                out.write(chunk)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    try:
        tags = await asyncio.to_thread(ctx.sources.read_audio_tags, partial)
        if tags is None:
            raise Unsupported(f"{meta['filename']!r} is not an audio file Copycast can read")
        ext = served_ext(str(meta["filename"]))
        mime = mime_for_ext(ext) if ext is not None else None
        key = staged.file(meta)
        await asyncio.to_thread(staged.store.put_file, key, partial, content_type=mime)
    finally:
        partial.unlink(missing_ok=True)
    saved = False
    try:
        # Parallel PUTs each saw the same room: decide under the Owner's quota lock, with
        # what the others received meanwhile, before this one counts as received.
        async with ctx.uow_factory() as uow:
            await uow.quota.lock_owner(owner)
            status = await quota_status(uow, ctx.users, owner)
            others = await _staged_usage(ctx, owner, _now(), exclude=upload_id)
            room = _room(status, others.bytes)
            if room is not None and size > room:
                raise _does_not_fit(status, size, room)
            meta.update(state="received", size_bytes=size, tags=tags.model_dump(mode="json"))
            await asyncio.to_thread(staged.save, meta)
            saved = True
    except BaseException:
        if not saved:  # refused: the upload stays pending, with no file
            with contextlib.suppress(Exception):
                await asyncio.to_thread(staged.store.delete, [key])
        raise
    log.info("upload.received", upload_id=upload_id, bytes=size)
    return _read(ctx, meta)


@capability("get_upload", response=UploadRead)
async def get_upload(ctx: ServiceContext, upload_id: str) -> UploadRead:
    """A staged upload and, once received, the tags read from it; another User's is 404."""
    meta = await asyncio.to_thread(_staged(ctx, upload_id).load)
    _check_visible(ctx, upload_id, meta)
    return _read(ctx, meta)


def _tags(meta: dict[str, Any]) -> AudioTags:
    tags = meta.get("tags")
    return AudioTags.model_validate(tags) if tags is not None else AudioTags()


def _duration(meta: dict[str, Any]) -> int | None:
    return _tags(meta).duration_seconds


def _received(
    ctx: ServiceContext, upload_ids: list[str]
) -> list[tuple[_Staged, dict[str, Any], str]]:
    """Each named upload with its metadata and served extension; 404 for an upload the
    caller may not see, 409 until its PUT."""
    staged: list[tuple[_Staged, dict[str, Any], str]] = []
    for upload_id in upload_ids:
        upload = _staged(ctx, upload_id)
        meta = upload.load()
        _check_visible(ctx, upload_id, meta)
        if meta["state"] != "received":
            raise Conflict(f"upload {upload_id} has no file yet; PUT it first")
        ext = served_ext(str(meta["filename"]))
        assert ext is not None  # create_upload refused anything else
        staged.append((upload, meta, ext))
    return staged


async def _check_batch(
    ctx: ServiceContext,
    uow: UnitOfWorkPort,
    owner: str,
    staged: list[tuple[_Staged, dict[str, Any], str]],
) -> None:
    """Every upload belongs to ``owner`` (the target Feed's: an Admin may add anyone's
    uploads, but only to a Feed of their Owner), and the whole batch fits the quota."""
    for upload, meta, _ in staged:
        if _owner(ctx, meta) != owner:
            raise NotFound("upload", upload.id)
    total = sum(int(meta.get("size_bytes") or 0) for _, meta, _ in staged)
    # Held until this unit of work commits the added bytes: parallel batches queue here
    # and each sees the usage the previous one committed.
    await uow.quota.lock_owner(owner)
    await require_room(uow, ctx.users, owner, adding_bytes=total)


def _description(progress: str, episode: SerialEpisode) -> str:
    return f"{progress}\n\n{episode.description}" if episode.description else progress


@capability("create_serial_bucket", request=SerialBucketCreate, response=BucketRead)
async def create_serial_bucket(ctx: ServiceContext, body: SerialBucketCreate) -> BucketRead:
    """A new Bucket holding the given uploads as Episodes, in exactly the given order."""
    staged = await asyncio.to_thread(
        _received, ctx, [episode.upload_id for episode in body.episodes]
    )

    now = _now()
    durations = [_duration(meta) for _, meta, _ in staged]
    entries = [
        SerialEntry(
            title=episode.title,
            duration_seconds=duration,
            part=episode.part,
            part_title=episode.part_title,
            chapter=episode.chapter,
        )
        for episode, duration in zip(body.episodes, durations, strict=True)
    ]
    notes = progress_notes(entries)
    dates = posting_dates(len(entries), now=now)
    total = len(entries)
    listing = SourceListing(
        service=UPLOAD_SERVICE,
        title=body.name,
        listing_order=ListingOrder.oldest_first,
        items=[
            SourceListingItem(
                source_key=f"upload:{upload.id}",
                title=numbered_title(index + 1, total, episode.title)
                if body.number_titles
                else episode.title,
                description=_description(notes[index], episode),
                published_at=dates[index],
                duration_seconds=durations[index],
                author=episode.author,
                source_number=index + 1,
                source_season=episode.part,
                position=index,
                enclosure_url=f"file.{ext}",
            )
            for index, (episode, (upload, _, ext)) in enumerate(
                zip(body.episodes, staged, strict=True)
            )
        ],
    )

    layout = ctx.layout
    owner = acting_owner(ctx)
    async with ctx.uow_factory() as uow:
        await _check_batch(ctx, uow, owner, staged)
        feed = await uow.feeds.add(
            ctx.rows.feed(
                id=bucket_id(body.name),
                owner=owner,
                kind=FeedKind.bucket.value,
                title=body.name,
                author=body.author,
                description=body.description,
                serial=True,
                follow=False,
                paused=False,
                engine_options={},
            )
        )
        upsert = await uow.catalog.upsert_listing(feed.id, listing, now=now)
        rows = {row.source_key: row for row in await uow.catalog.get_many(upsert.seen_item_ids)}
        for upload, meta, ext in staged:
            row = rows[f"upload:{upload.id}"]
            target = layout.media_key(feed.id, row.id, ext)
            await asyncio.to_thread(layout.store.copy, upload.file(meta), target)
            await uow.catalog.mark_state(
                row.id,
                ArchiveState.archived,
                media_path=layout.relative(feed.id, target),
                media_mime=mime_for_ext(ext),
                media_bytes=int(meta["size_bytes"]),
                duration_seconds=row.duration_seconds,
                archived_at=now,
            )
        await uow.feeds.recount_storage(feed.id)
        _, revision = await uow.update_intent(feed.id)
        await uow.publish(FeedEvent(feed_id=feed.id, revision=revision, reason="created"))
        prefixes = [upload.prefix for upload, _, _ in staged]
        uow.after_commit(lambda: _remove_all(layout.store, prefixes))
        read = await bucket_read_for(uow, ctx, feed)
    log.info("serial_bucket.created", feed_id=read.id, episodes=total)
    return read


@capability("add_uploads", request=UploadsAdd, response=BucketRead)
async def add_uploads(ctx: ServiceContext, bucket: str, body: UploadsAdd) -> BucketRead:
    """Received uploads as archived Episodes of an existing Bucket, in natural file-name order."""
    staged = await asyncio.to_thread(_received, ctx, body.upload_ids)
    staged.sort(key=lambda entry: natural_key(str(entry[1]["filename"])))
    now = _now()
    layout = ctx.layout
    async with ctx.uow_factory() as uow:
        feed = await require_bucket(uow, bucket)
        await _check_batch(ctx, uow, feed.owner, staged)
        total = len(staged)
        for index, (upload, meta, ext) in enumerate(staged):
            # A Bucket dates Episodes by arrival: one second apart, the last one now.
            arrived = now - timedelta(seconds=total - 1 - index)
            tags = _tags(meta)
            filename = str(meta["filename"])
            item = SourceListingItem(
                source_key=f"upload:{upload.id}",
                title=tags.title or filename.rpartition(".")[0] or filename,
                published_at=arrived,
                duration_seconds=tags.duration_seconds,
                author=tags.artist,
                enclosure_url=f"file.{ext}",
            )
            listing = SourceListing(service=UPLOAD_SERVICE, title=feed.title, items=[item])
            # partial: a Bucket has no listing to delist the rows it already holds against.
            upsert = await uow.catalog.upsert_listing(feed.id, listing, now=arrived, partial=True)
            [row] = await uow.catalog.get_many(upsert.seen_item_ids)
            target = layout.media_key(feed.id, row.id, ext)
            await asyncio.to_thread(layout.store.copy, upload.file(meta), target)
            await uow.catalog.mark_state(
                row.id,
                ArchiveState.archived,
                media_path=layout.relative(feed.id, target),
                media_mime=mime_for_ext(ext),
                media_bytes=int(meta["size_bytes"]),
                duration_seconds=row.duration_seconds,
                archived_at=now,
            )
        await uow.feeds.recount_storage(feed.id)
        _, revision = await uow.update_intent(feed.id)
        await uow.publish(FeedEvent(feed_id=feed.id, revision=revision, reason="uploads"))
        prefixes = [upload.prefix for upload, _, _ in staged]
        uow.after_commit(lambda: _remove_all(layout.store, prefixes))
        read = await bucket_read_for(uow, ctx, feed)
    log.info("bucket.uploads_added", feed_id=read.id, episodes=total)
    return read


async def _remove_all(store: ObjectStorePort, prefixes: list[str]) -> None:
    for prefix in prefixes:
        await asyncio.to_thread(store.delete_prefix, prefix)


__all__ = [
    "MAX_STAGED_UPLOADS",
    "MAX_UPLOAD_BYTES",
    "UPLOAD_PATH",
    "UPLOAD_TTL",
    "add_uploads",
    "create_serial_bucket",
    "create_upload",
    "get_upload",
    "put_upload",
    "served_ext",
    "sweep_expired_uploads",
]
