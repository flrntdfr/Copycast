"""The archive job: fetch one item through the Engine and register what it produced.

RSS items are fetched through a synthesized info dict built from the item's
``<item>`` in ``source/feed.xml`` (falling back to the stored
``source_item_xml``); everything else is a yt-dlp extraction of the item URL.
The Engine downloads, transcodes and tags in a Scratch directory of the job's
own; only the finished files leave it, uploaded to the Object store (the audio as a
multipart upload) before the row is marked ``archived``, so nothing raw or
partial is ever served and no row points at a missing object. The Scratch
directory goes when the job ends, whatever happened. Engine outputs (Artwork,
subtitles, chapters as Podcasting 2.0 JSON) become assets under ``assets/``;
for RSS items the Source's own chapters, transcripts and image are mirrored
when the Engine produced none. Asset failures mark the asset ``failed``; the
job still succeeds. A stream with no recording yet (``NotReady``) puts the
item back to ``wanted`` without counting the attempt and records its live
status; once the job has waited ``LIVE_WAIT_MAX`` the item fails for good.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from copycast.adapters.assets.mirror import (
    CHAPTERS_MIME,
    AssetError,
    RemoteAsset,
    mirror_asset,
    remote_assets_from_item,
    remote_attachments_from_description,
)
from copycast.adapters.db.models import Feed
from copycast.adapters.db.uow import UnitOfWork
from copycast.adapters.engine.synth import from_listing_item
from copycast.adapters.sources import rss
from copycast.adapters.storage.layout import Layout
from copycast.adapters.storage.scratch import JobDir, Scratch
from copycast.application.events import FeedEvent, ItemEvent
from copycast.application.ports import (
    Cancelled,
    EngineError,
    FetchResult,
    FetchSpec,
    NotReady,
    PermanentError,
    StorageFull,
    TransientError,
)
from copycast.application.services.defaults import effective, load_defaults
from copycast.domain.chapters import podcast_chapters
from copycast.domain.enums import (
    ArchiveState,
    AssetFormat,
    AssetKind,
    AssetProvenance,
    AssetState,
    FeedKind,
    FetchKind,
    LiveStatus,
    ProgressPhase,
    SourceKind,
)
from copycast.domain.listing import SourceListingItem
from copycast.logging import get_logger
from copycast.settings import StorageSettings
from copycast.worker.constants import live_wait_exceeded, live_wait_exhausted_message
from copycast.worker.jobs import JobContext, JobOutcome
from copycast.worker.jobs.common import engine_options_for

log = get_logger(__name__)

IMAGE_MIME: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}
SUBTITLE_FORMATS: dict[str, AssetFormat] = {"vtt": AssetFormat.vtt, "srt": AssetFormat.srt}


@dataclass(frozen=True, slots=True)
class _Target:
    """The row values the job needs after its first transaction."""

    feed_id: str
    feed_kind: FeedKind
    source_kind: SourceKind | None
    feed_source_url: str | None
    feed_title: str | None
    feed_author: str | None
    feed_artwork_url: str | None
    item_id: str
    source_key: str
    item_source_url: str | None
    stored_item_xml: str | None
    options: dict[str, Any]
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Prepared:
    spec: FetchSpec
    item_xml: str | None = None


@dataclass(frozen=True, slots=True)
class PlacedAsset:
    """An asset file the job put under ``assets/`` (or failed to)."""

    kind: AssetKind
    local_path: str | None
    mime: str | None
    size_bytes: int | None
    language: str | None = None
    format: AssetFormat | None = None
    provenance: AssetProvenance = AssetProvenance.mirrored
    remote_url: str | None = None
    error: str | None = None
    slot: str | None = None


@dataclass(slots=True)
class Produced:
    assets: list[PlacedAsset] = field(default_factory=list[PlacedAsset])

    def has(
        self, kind: AssetKind, *, language: str | None = None, fmt: AssetFormat | None = None
    ) -> bool:
        return any(
            a.error is None
            and a.kind is kind
            and (language is None or a.language == language)
            and (fmt is None or a.format is fmt)
            for a in self.assets
        )


# --------------------------------------------------------------------------- preparation


def _rss_entry(layout: Layout, target: _Target) -> tuple[SourceListingItem, str] | None:
    """The listing item and ``<item>`` XML for ``source_key`` from ``source/feed.xml``,
    else from the stored ``source_item_xml``."""
    snapshot = layout.store.get_bytes(layout.source_xml_key(target.feed_id))
    if snapshot is not None:
        try:
            parsed = rss.parse_feed(snapshot, url=target.feed_source_url)
        except Exception as exc:  # a corrupt snapshot must not block the item
            log.warning("archive.source_xml_unreadable", feed_id=target.feed_id, error=str(exc))
        else:
            element = parsed.items.get(target.source_key)
            entry = next(
                (i for i in parsed.listing.items if i.source_key == target.source_key), None
            )
            if element is not None and entry is not None:
                return entry, rss.item_xml(element)
    if target.stored_item_xml:
        wrapped = (
            '<rss version="2.0"><channel><title>copycast</title>'
            f"{target.stored_item_xml}</channel></rss>"
        )
        try:
            parsed = rss.parse_feed(wrapped, url=target.feed_source_url)
        except Exception:
            return None
        if parsed.listing.items:
            entry = parsed.listing.items[0]
            element = parsed.items.get(entry.source_key)
            if element is not None:
                return entry, rss.item_xml(element)
    return None


def prepare(layout: Layout, target: _Target, work: JobDir) -> Prepared:
    home, temp = work.home, work.temp
    if target.source_kind is SourceKind.rss:
        found = _rss_entry(layout, target)
        if found is None:
            raise PermanentError(
                f"the Source no longer advertises item {target.item_id!r}; nothing to fetch"
            )
        entry, item_xml = found
        try:
            synth = from_listing_item(
                entry,
                item_id=target.item_id,
                feed_title=target.feed_title,
                feed_author=target.feed_author,
                feed_artwork_url=target.feed_artwork_url,
            )
        except ValueError as exc:
            raise PermanentError(str(exc)) from exc
        return Prepared(
            FetchSpec(FetchKind.direct, synth.url, target.item_id, home, temp, synth), item_xml
        )
    if not target.item_source_url:
        raise PermanentError(f"item {target.item_id!r} has no URL to fetch")
    return Prepared(FetchSpec(FetchKind.ytdlp, target.item_source_url, target.item_id, home, temp))


# --------------------------------------------------------------------------- outputs


def _upload(layout: Layout, key: str, path: Path, mime: str | None, uploaded: list[str]) -> int:
    """One finished Scratch file into the Object store; ``uploaded`` records the key for cleanup."""
    size = layout.store.put_file(key, path, content_type=mime)
    uploaded.append(key)
    return size


def upload_media(
    layout: Layout, feed_id: str, item_id: str, result: FetchResult, uploaded: list[str]
) -> str:
    """The audio (multipart when large) and its ``.info.json``; returns the audio's key."""
    key = layout.media_key(feed_id, item_id, result.ext)
    _upload(layout, key, result.audio_path, result.mime, uploaded)
    if result.info_json_path is not None and result.info_json_path.is_file():
        info_key = layout.info_json_key(feed_id, item_id)
        _upload(layout, info_key, result.info_json_path, "application/json", uploaded)
    return key


def upload_item_xml(
    layout: Layout, feed_id: str, item_id: str, item_xml: str, uploaded: list[str]
) -> None:
    key = layout.item_xml_key(feed_id, item_id)
    layout.store.put_bytes(key, item_xml, content_type="application/xml")
    uploaded.append(key)


def place_engine_outputs(
    layout: Layout, feed_id: str, item_id: str, result: FetchResult, uploaded: list[str]
) -> Produced:
    """Artwork -> ``assets/{item}.artwork.{ext}``, subtitles -> transcripts, chapters ->
    Podcasting 2.0 JSON (none when no chapter has a usable start), each uploaded."""
    produced = Produced()
    if result.artwork_path is not None and result.artwork_path.is_file():
        ext = result.artwork_path.suffix.lstrip(".").lower() or "jpg"
        key = layout.item_artwork_key(feed_id, item_id, ext)
        mime = IMAGE_MIME.get(ext, "application/octet-stream")
        try:
            size = _upload(layout, key, result.artwork_path, mime, uploaded)
            produced.assets.append(
                PlacedAsset(AssetKind.artwork, layout.relative(feed_id, key), mime, size)
            )
        except OSError as exc:
            produced.assets.append(PlacedAsset(AssetKind.artwork, None, None, None, error=str(exc)))
    for language, path in sorted(result.subtitle_paths.items()):
        if not path.is_file():
            continue
        ext = path.suffix.lstrip(".").lower()
        fmt = SUBTITLE_FORMATS.get(ext)
        if fmt is None:
            continue
        lang = language.strip().lower()
        mime = "text/vtt" if fmt is AssetFormat.vtt else "application/x-subrip"
        try:
            key = layout.transcript_key(feed_id, item_id, lang, AssetProvenance.mirrored, ext)
            size = _upload(layout, key, path, mime, uploaded)
        except (OSError, ValueError) as exc:
            produced.assets.append(
                PlacedAsset(
                    AssetKind.transcript,
                    None,
                    None,
                    None,
                    language=lang,
                    format=fmt,
                    error=str(exc),
                )
            )
            continue
        produced.assets.append(
            PlacedAsset(
                AssetKind.transcript,
                layout.relative(feed_id, key),
                mime,
                size,
                language=lang,
                format=fmt,
            )
        )
    chapters = podcast_chapters(result.chapters)
    if chapters is not None:
        key = layout.chapters_key(feed_id, item_id)
        try:
            size = layout.write_json(key, chapters)
            uploaded.append(key)
            produced.assets.append(
                PlacedAsset(
                    AssetKind.chapters,
                    layout.relative(feed_id, key),
                    CHAPTERS_MIME,
                    size,
                    format=AssetFormat.json,
                )
            )
        except OSError as exc:
            produced.assets.append(
                PlacedAsset(
                    AssetKind.chapters, None, None, None, format=AssetFormat.json, error=str(exc)
                )
            )
    return produced


def mirror_item_assets(
    layout: Layout,
    feed_id: str,
    item_id: str,
    item_xml: str,
    base_url: str | None,
    produced: Produced,
) -> list[PlacedAsset]:
    """The Source's chapters, transcripts and ``itunes:image`` the Engine did not produce."""
    placed: list[PlacedAsset] = []
    remotes = remote_assets_from_item(
        item_xml,
        item_id=item_id,
        base_url=base_url,
        include_artwork=not produced.has(AssetKind.artwork),
    )
    for remote in remotes:
        if remote.kind is AssetKind.chapters and produced.has(AssetKind.chapters):
            continue
        if remote.kind is AssetKind.transcript and produced.has(
            AssetKind.transcript, language=remote.language, fmt=remote.format
        ):
            continue
        placed.append(_mirror_one(layout, feed_id, remote))
    return placed


def mirror_description_attachments(
    layout: Layout, feed_id: str, item_id: str, description: str, base_url: str | None
) -> list[PlacedAsset]:
    """The images the show notes embed and the files they link to, kept next to the media."""
    remotes = remote_attachments_from_description(description, item_id=item_id, base_url=base_url)
    return [_mirror_one(layout, feed_id, remote) for remote in remotes]


def _mirror_one(layout: Layout, feed_id: str, remote: RemoteAsset) -> PlacedAsset:
    try:
        mirrored = mirror_asset(remote, layout, feed_id)
    except AssetError as exc:
        return PlacedAsset(
            remote.kind,
            None,
            None,
            None,
            language=remote.language,
            format=remote.format,
            provenance=remote.provenance,
            remote_url=remote.url,
            error=str(exc),
            slot=remote.slot,
        )
    return PlacedAsset(
        mirrored.kind,
        mirrored.local_path,
        mirrored.mime,
        mirrored.size_bytes,
        language=mirrored.language,
        format=mirrored.format,
        provenance=mirrored.provenance,
        remote_url=remote.url,
        slot=mirrored.slot,
    )


async def _record_assets(
    uow: UnitOfWork, feed_id: str, item_id: str, assets: list[PlacedAsset]
) -> None:
    now = datetime.now(UTC)
    for asset in assets:
        await uow.assets.upsert(
            feed_id,
            item_id,
            asset.kind,
            provenance=asset.provenance,
            language=asset.language,
            format=asset.format,
            remote_url=asset.remote_url,
            local_path=asset.local_path,
            mime=asset.mime,
            size_bytes=asset.size_bytes,
            state=AssetState.failed if asset.error else AssetState.archived,
            last_error=asset.error,
            fetched_at=None if asset.error else now,
            slot=asset.slot,
        )


# --------------------------------------------------------------------------- the job


async def run(ctx: JobContext) -> JobOutcome:
    job = ctx.job
    if job.item_id is None:
        raise PermanentError("archive job without an item")
    item_id = job.item_id
    async with ctx.uow() as uow:
        item = await uow.catalog.require(item_id)
        feed = await uow.feeds.require(item.feed_id)
        if item.archive_state != ArchiveState.wanted:
            log.info("archive.skipped", item_id=item_id, state=item.archive_state)
            return JobOutcome(result={"skipped": f"item is {item.archive_state}"})
        if feed.paused:
            raise Cancelled("the Mirror is Paused")
        await uow.catalog.mark_state(item_id, ArchiveState.archiving, only_from=ArchiveState.wanted)
        await uow.publish(ItemEvent(feed_id=feed.id, item_id=item_id, state=ArchiveState.archiving))
        target = _target(
            ctx,
            feed,
            item.source_key,
            item.source_url,
            item.source_item_xml,
            item_id,
            description=item.description,
        )

    layout: Layout = ctx.container.layout
    scratch: Scratch = ctx.container.scratch
    with scratch.job(item_id) as work:
        outcome = await _fetch_and_store(ctx, layout, work, target, item_id)
    if isinstance(outcome, JobOutcome):
        return outcome
    result, assets = outcome
    log.info(
        "archive.done",
        item_id=item_id,
        feed_id=target.feed_id,
        bytes=result.size_bytes,
        assets=len([a for a in assets if a.error is None]),
        asset_failures=len([a for a in assets if a.error is not None]),
    )
    return JobOutcome(
        result={
            "media_bytes": result.size_bytes,
            "ext": result.ext,
            "assets": len([a for a in assets if a.error is None]),
            "asset_failures": [a.error for a in assets if a.error is not None],
        },
        engine_version=result.engine_version or None,
    )


async def _fetch_and_store(
    ctx: JobContext, layout: Layout, work: JobDir, target: _Target, item_id: str
) -> tuple[FetchResult, list[PlacedAsset]] | JobOutcome:
    """Fetch into Scratch, upload the finished files, then mark the row archived.

    Returns the fetch result and the assets placed, or the job's outcome when the row
    changed while downloading (its objects are removed again).
    """
    try:
        _check_scratch_room(ctx.container.scratch, ctx.container.settings.storage)
        prepared = await ctx.run_blocking(prepare, layout, target, work)
        ctx.check_cancelled(f"fetch of {item_id}")
        result = await ctx.run_blocking(
            ctx.container.engine.fetch_item,
            prepared.spec,
            target.options,
            ctx.cancel,
            ctx.progress.on_progress,
            ctx.log,
        )
    except EngineError as exc:
        failure = await _record_failure(ctx, target.feed_id, item_id, exc)
        if failure is exc:
            raise
        raise failure from exc
    except Exception as exc:
        wrapped = TransientError(f"{type(exc).__name__}: {exc}")
        await _record_failure(ctx, target.feed_id, item_id, wrapped)
        raise wrapped from exc

    ctx.progress.set_phase(ProgressPhase.assets)
    uploaded: list[str] = []
    try:
        media_key = await ctx.run_blocking(
            upload_media, layout, target.feed_id, item_id, result, uploaded
        )
        produced = await ctx.run_blocking(
            place_engine_outputs, layout, target.feed_id, item_id, result, uploaded
        )
        assets = list(produced.assets)
        if prepared.item_xml is not None:
            await ctx.run_blocking(
                upload_item_xml, layout, target.feed_id, item_id, prepared.item_xml, uploaded
            )
            assets.extend(
                await ctx.run_blocking(
                    mirror_item_assets,
                    layout,
                    target.feed_id,
                    item_id,
                    prepared.item_xml,
                    target.feed_source_url,
                    produced,
                )
            )
    except Exception as exc:
        # The Object store refused or went away mid-upload: nothing is archived, the job retries.
        await ctx.run_blocking(_discard_objects, layout, uploaded)
        wrapped = TransientError(f"upload to storage failed: {type(exc).__name__}: {exc}")
        await _record_failure(ctx, target.feed_id, item_id, wrapped)
        raise wrapped from exc

    # A flat listing (a YouTube channel tab) carries neither a description nor a date; the
    # full info the fetch produced does, and fills whatever the Catalog row still lacks.
    enriched = await ctx.run_blocking(metadata_from_info_json, result.info_json_path)
    notes = target.description or enriched.get("description")
    if notes:
        assets.extend(
            await ctx.run_blocking(
                mirror_description_attachments,
                layout,
                target.feed_id,
                item_id,
                notes,
                target.feed_source_url,
            )
        )

    async with ctx.uow() as uow:
        archived = await uow.catalog.mark_state(
            item_id,
            ArchiveState.archived,
            only_from=ArchiveState.archiving,
            media_path=layout.relative(target.feed_id, media_key),
            media_mime=result.mime,
            media_bytes=result.size_bytes,
            duration_seconds=result.duration_seconds,
            archived_at=datetime.now(UTC),
        )
        if not archived:
            # Deleted (tombstoned) while downloading: the objects must not outlive the row's
            # intent.
            mirrored = [a.local_path for a in assets if a.local_path]
            keys = [*uploaded, *(layout.resolve(target.feed_id, p) for p in mirrored)]

            async def _discard() -> None:
                await ctx.run_blocking(_discard_objects, layout, keys)

            uow.after_commit(_discard)
            log.warning("archive.row_changed", item_id=item_id)
            return JobOutcome(result={"skipped": "item state changed during the fetch"})
        if prepared.item_xml is not None:
            await uow.catalog.set_source_item_xml(item_id, prepared.item_xml)
        if enriched:
            await uow.catalog.fill_metadata(item_id, **enriched)
        await _record_assets(uow, target.feed_id, item_id, assets)
        await uow.feeds.recount_storage(target.feed_id)
        _, revision = await uow.update_intent(target.feed_id, debounce=True)
        await uow.publish(
            ItemEvent(feed_id=target.feed_id, item_id=item_id, state=ArchiveState.archived)
        )
        await uow.publish(FeedEvent(feed_id=target.feed_id, revision=revision, reason="archived"))
    return result, assets


def _target(
    ctx: JobContext,
    feed: Feed,
    source_key: str,
    item_source_url: str | None,
    stored_item_xml: str | None,
    item_id: str,
    *,
    description: str | None = None,
) -> _Target:
    return _Target(
        feed_id=feed.id,
        feed_kind=FeedKind(feed.kind),
        source_kind=SourceKind(feed.source_kind) if feed.source_kind else None,
        feed_source_url=feed.source_url,
        feed_title=feed.title,
        feed_author=feed.author,
        feed_artwork_url=feed.artwork_url,
        item_id=item_id,
        source_key=source_key,
        item_source_url=item_source_url,
        stored_item_xml=stored_item_xml,
        description=description,
        options=engine_options_for(
            ctx.container.settings,
            feed.engine_options,
            language=effective(
                load_defaults(ctx.container.layout),
                language=feed.preferred_language,
                min_duration_seconds=feed.min_duration_seconds,
            ).language,
        ),
    )


def metadata_from_info_json(path: Path | None) -> dict[str, Any]:
    """``description``, ``published_at``, ``author``, ``artwork_url`` from a yt-dlp info dict."""
    if path is None or not path.is_file():
        return {}
    try:
        info: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(info, dict):
        return {}
    data = cast(dict[str, Any], info)
    found: dict[str, Any] = {}
    description = data.get("description")
    if isinstance(description, str) and description.strip():
        found["description"] = description.strip()
    for key in ("timestamp", "release_timestamp"):
        value = data.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
            found["published_at"] = datetime.fromtimestamp(float(value), tz=UTC)
            break
    else:
        upload_date = data.get("upload_date") or data.get("release_date")
        if isinstance(upload_date, str) and len(upload_date) == 8 and upload_date.isdigit():
            with contextlib.suppress(ValueError):
                found["published_at"] = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=UTC)
    for key in ("artist", "uploader", "channel", "creator"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            found["author"] = value.strip()
            break
    thumbnail = data.get("thumbnail")
    if isinstance(thumbnail, str) and thumbnail.startswith("http"):
        found["artwork_url"] = thumbnail
    return found


def _check_scratch_room(scratch: Scratch, storage: StorageSettings) -> None:
    """``StorageFull`` (re-queued, archive claims paused) below ``min_free_scratch_mb``."""
    free = scratch.free_bytes()
    floor = storage.min_free_scratch_mb * 1024 * 1024
    if free is not None and free < floor:
        raise StorageFull(
            f"Scratch has {free // (1024 * 1024)} MiB free, below {floor // (1024 * 1024)} MiB"
        )


def _discard_objects(layout: Layout, keys: list[str]) -> None:
    with contextlib.suppress(Exception):
        layout.store.delete(keys)


async def _record_failure(
    ctx: JobContext, feed_id: str, item_id: str, exc: EngineError
) -> EngineError:
    """Transient -> wanted (attempt counted; failed once exhausted); permanent -> failed;
    cancelled and storage full -> wanted without counting the attempt; not ready (no
    recording yet) -> wanted without counting the attempt, the live status stored, until
    the job has waited ``LIVE_WAIT_MAX``: then failed, as the permanent error returned.

    Returns the error the job should raise: ``exc`` itself, or the permanent error a
    ``NotReady`` turned into. This is the only place the ceiling is measured: the runner
    trusts the error it gets (``NotReady`` re-queues, ``PermanentError`` fails), so the
    Catalog and the job cannot disagree on the outcome.
    """
    live_status: LiveStatus | None = None
    if isinstance(exc, NotReady):
        live_status = exc.live_status
        if live_wait_exceeded(ctx.job.created_at):
            exc = PermanentError(live_wait_exhausted_message(str(exc)))
            state, count, error = ArchiveState.failed, True, str(exc)
        else:
            state, count, error = ArchiveState.wanted, False, str(exc)
    elif isinstance(exc, Cancelled | StorageFull):
        state, count, error = (
            ArchiveState.wanted,
            False,
            None if isinstance(exc, Cancelled) else str(exc),
        )
    elif isinstance(exc, PermanentError):
        state, count, error = ArchiveState.failed, True, str(exc)
    else:
        state = ArchiveState.failed if ctx.exhausted else ArchiveState.wanted
        count, error = True, str(exc)
    async with ctx.uow() as uow:
        changed = await uow.catalog.mark_state(
            item_id, state, only_from=ArchiveState.archiving, error=error, count_attempt=count
        )
        if live_status is not None:
            await uow.catalog.set_live_status(item_id, live_status)
        if changed:
            await uow.publish(ItemEvent(feed_id=feed_id, item_id=item_id, state=state))
            await uow.update_intent(feed_id, debounce=True)
    if live_status is not None and state is ArchiveState.wanted:
        log.info(
            "archive.not_ready", item_id=item_id, live_status=live_status.value, error=str(exc)
        )
    else:
        log.warning("archive.failed", item_id=item_id, state=state.value, error=str(exc))
    return exc


__all__ = [
    "IMAGE_MIME",
    "PlacedAsset",
    "Prepared",
    "Produced",
    "mirror_description_attachments",
    "mirror_item_assets",
    "place_engine_outputs",
    "prepare",
    "run",
    "upload_item_xml",
    "upload_media",
]
