"""Uploads and Serial Inboxes (ADR 0015).

An upload is staged on disk, not in Postgres: ``create_upload`` writes
``uploads/<id>/upload.json`` with a digest of a one-time token and returns the
upload URL; ``put_upload`` streams the bytes to ``uploads/<id>/file.<ext>`` and
reads the file's tags; ``create_serial_inbox`` moves every named upload into a
new Inbox as an archived Episode, in the order the agent gave, and removes the
staging directory. Staged uploads older than :data:`UPLOAD_TTL` are swept on
the next ``create_upload``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from copycast.application.capabilities import capability
from copycast.application.events import FeedEvent
from copycast.application.models import (
    AudioTags,
    InboxRead,
    SerialEpisode,
    SerialInboxCreate,
    UploadCreate,
    UploadRead,
)
from copycast.application.services.context import ServiceContext
from copycast.application.services.inboxes import inbox_read_for
from copycast.domain.enums import ArchiveState, FeedKind, ListingOrder
from copycast.domain.exceptions import Conflict, NotFound, Unsupported
from copycast.domain.ids import inbox_id
from copycast.domain.listing import SourceListing, SourceListingItem
from copycast.domain.media import MPEG4_CONTAINERS, PODCAST_EXTS, mime_for_ext
from copycast.domain.serial import SerialEntry, numbered_title, posting_dates, progress_notes
from copycast.logging import get_logger

log = get_logger(__name__)

UPLOAD_TTL: Final = timedelta(hours=24)
MAX_UPLOAD_BYTES: Final = 4 * 1024**3
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
    """One ``uploads/<id>`` directory."""

    def __init__(self, root: Path, upload_id: str) -> None:
        if not upload_id.isalnum():
            raise NotFound("upload", upload_id)
        self.id = upload_id
        self.dir = root / upload_id
        self.meta_path = self.dir / _META

    def load(self) -> dict[str, Any]:
        try:
            meta: dict[str, Any] = json.loads(self.meta_path.read_text("utf-8"))
        except (OSError, ValueError):
            raise NotFound("upload", self.id) from None
        return meta

    def save(self, meta: dict[str, Any]) -> None:
        tmp = self.meta_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta), "utf-8")
        tmp.replace(self.meta_path)

    def file(self, meta: dict[str, Any]) -> Path:
        return self.dir / str(meta["file"])


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
        created_at=datetime.fromisoformat(meta["created_at"]),
    )


def _sweep(root: Path, now: datetime) -> None:
    """Remove staged uploads older than :data:`UPLOAD_TTL` (abandoned batches)."""
    if not root.is_dir():
        return
    for entry in root.iterdir():
        try:
            meta = json.loads((entry / _META).read_text("utf-8"))
            stale = now - datetime.fromisoformat(meta["created_at"]) > UPLOAD_TTL
        except (OSError, ValueError, KeyError):
            stale = now - datetime.fromtimestamp(entry.stat().st_mtime, UTC) > UPLOAD_TTL
        if stale:
            shutil.rmtree(entry, ignore_errors=True)


@capability("create_upload", request=UploadCreate, response=UploadRead)
async def create_upload(ctx: ServiceContext, body: UploadCreate) -> UploadRead:
    """Stage one audio file: returns the one-time ``upload_url`` to PUT its bytes to."""
    if served_ext(body.filename) is None:
        raise Unsupported(f"{body.filename!r} is not mp3, m4a or m4b; convert it before uploading")
    root = ctx.layout.uploads_dir
    now = _now()
    await asyncio.to_thread(_sweep, root, now)
    upload_id = secrets.token_hex(8)
    token = secrets.token_urlsafe(24)
    staged = _Staged(root, upload_id)
    staged.dir.mkdir(parents=True, exist_ok=False)
    _, _, ext = body.filename.rpartition(".")
    meta: dict[str, Any] = {
        "id": upload_id,
        "filename": body.filename,
        "file": f"file.{ext.lower()}",
        "token_sha256": _digest(token),
        "state": "pending",
        "created_at": now.isoformat(),
    }
    staged.save(meta)
    log.info("upload.created", upload_id=upload_id, filename=body.filename)
    return _read(ctx, meta, token)


@capability("put_upload", response=UploadRead)
async def put_upload(
    ctx: ServiceContext, upload_id: str, token: str, chunks: AsyncIterator[bytes]
) -> UploadRead:
    """Receive an upload's bytes (once) and read its tags."""
    staged = _Staged(ctx.layout.uploads_dir, upload_id)
    meta = staged.load()
    if not hmac.compare_digest(_digest(token), str(meta.get("token_sha256", ""))):
        raise NotFound("upload", upload_id)
    if meta["state"] != "pending":
        raise Conflict(f"upload {upload_id} was already received")
    target = staged.file(meta)
    partial = target.with_suffix(target.suffix + ".part")
    size = 0
    try:
        with partial.open("wb") as out:
            async for chunk in chunks:
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise Unsupported(f"an upload is limited to {MAX_UPLOAD_BYTES} bytes")
                out.write(chunk)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    tags = await asyncio.to_thread(ctx.sources.read_audio_tags, partial)
    if tags is None:
        partial.unlink(missing_ok=True)
        raise Unsupported(f"{meta['filename']!r} is not an audio file Copycast can read")
    os.replace(partial, target)
    meta.update(state="received", size_bytes=size, tags=tags.model_dump(mode="json"))
    staged.save(meta)
    log.info("upload.received", upload_id=upload_id, bytes=size)
    return _read(ctx, meta)


@capability("get_upload", response=UploadRead)
async def get_upload(ctx: ServiceContext, upload_id: str) -> UploadRead:
    """A staged upload and, once received, the tags read from it."""
    return _read(ctx, _Staged(ctx.layout.uploads_dir, upload_id).load())


def _duration(meta: dict[str, Any]) -> int | None:
    tags = meta.get("tags")
    return AudioTags.model_validate(tags).duration_seconds if tags is not None else None


def _description(progress: str, episode: SerialEpisode) -> str:
    return f"{progress}\n\n{episode.description}" if episode.description else progress


@capability("create_serial_inbox", request=SerialInboxCreate, response=InboxRead)
async def create_serial_inbox(ctx: ServiceContext, body: SerialInboxCreate) -> InboxRead:
    """A new Inbox holding the given uploads as Episodes, in exactly the given order."""
    root = ctx.layout.uploads_dir
    staged: list[tuple[_Staged, dict[str, Any], str]] = []
    for episode in body.episodes:
        upload = _Staged(root, episode.upload_id)
        meta = upload.load()
        if meta["state"] != "received":
            raise Conflict(f"upload {episode.upload_id} has no file yet; PUT it first")
        ext = served_ext(str(meta["filename"]))
        assert ext is not None  # create_upload refused anything else
        staged.append((upload, meta, ext))

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
    async with ctx.uow_factory() as uow:
        feed = await uow.feeds.add(
            ctx.rows.feed(
                id=inbox_id(body.name),
                kind=FeedKind.inbox.value,
                title=body.name,
                author=body.author,
                description=body.description,
                serial=True,
                follow=False,
                paused=False,
                engine_options={},
            )
        )
        layout.ensure_feed_dirs(feed.id)
        upsert = await uow.catalog.upsert_listing(feed.id, listing, now=now)
        rows = {row.source_key: row for row in await uow.catalog.get_many(upsert.seen_item_ids)}
        for upload, meta, ext in staged:
            row = rows[f"upload:{upload.id}"]
            target = layout.media_path(feed.id, row.id, ext)
            await asyncio.to_thread(os.replace, upload.file(meta), target)
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
        dirs = [upload.dir for upload, _, _ in staged]
        uow.after_commit(lambda: _remove_all(dirs))
        read = await inbox_read_for(uow, ctx, feed)
    log.info("serial_inbox.created", feed_id=read.id, episodes=total)
    return read


def _remove_all(dirs: list[Path]) -> None:
    for path in dirs:
        shutil.rmtree(path, ignore_errors=True)


__all__ = [
    "MAX_UPLOAD_BYTES",
    "UPLOAD_PATH",
    "UPLOAD_TTL",
    "create_serial_inbox",
    "create_upload",
    "get_upload",
    "put_upload",
    "served_ext",
]
