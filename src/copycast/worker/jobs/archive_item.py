"""The archive job: fetch one item through the Engine and register what it produced.

RSS items are fetched through a synthesized info dict built from the item's
``<item>`` in ``source/feed.xml`` (falling back to the stored
``source_item_xml``); everything else is a yt-dlp extraction of the item URL.
Engine outputs (Artwork, subtitles, chapters) become assets under ``assets/``;
for RSS items the Source's own chapters, transcripts and image are mirrored
when the Engine produced none. Asset failures mark the asset ``failed``; the
job still succeeds.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copycast.adapters.assets.mirror import (
    CHAPTERS_MIME,
    AssetError,
    RemoteAsset,
    mirror_asset,
    remote_assets_from_item,
)
from copycast.adapters.db.models import Feed
from copycast.adapters.db.uow import UnitOfWork
from copycast.adapters.engine.synth import from_listing_item
from copycast.adapters.sources import rss
from copycast.adapters.storage.atomic import write_atomic, write_json_atomic
from copycast.adapters.storage.layout import Layout
from copycast.application.events import FeedEvent, ItemEvent
from copycast.application.ports import (
    Cancelled,
    EngineError,
    FetchResult,
    FetchSpec,
    PermanentError,
    StorageFull,
    TransientError,
)
from copycast.domain.enums import (
    ArchiveState,
    AssetFormat,
    AssetKind,
    AssetProvenance,
    AssetState,
    FeedKind,
    FetchKind,
    ProgressPhase,
    SourceKind,
)
from copycast.domain.listing import SourceListingItem
from copycast.logging import get_logger
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
    path = layout.source_xml_path(target.feed_id)
    if path.is_file():
        try:
            parsed = rss.parse_feed(path.read_bytes(), url=target.feed_source_url)
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


def prepare(layout: Layout, target: _Target) -> Prepared:
    home = layout.media_dir(target.feed_id)
    temp = layout.tmp_dir(target.feed_id)
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


def _move(src: Path, dst: Path) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    return dst.stat().st_size


def place_engine_outputs(
    layout: Layout, feed_id: str, item_id: str, result: FetchResult
) -> Produced:
    """Artwork -> ``assets/{item}.artwork.{ext}``, subtitles -> transcripts, chapters -> JSON."""
    produced = Produced()
    if result.artwork_path is not None and result.artwork_path.is_file():
        ext = result.artwork_path.suffix.lstrip(".").lower() or "jpg"
        target = layout.item_artwork_path(feed_id, item_id, ext)
        try:
            size = _move(result.artwork_path, target)
            produced.assets.append(
                PlacedAsset(
                    AssetKind.artwork,
                    layout.relative(feed_id, target),
                    IMAGE_MIME.get(ext, "application/octet-stream"),
                    size,
                )
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
        try:
            target = layout.transcript_path(feed_id, item_id, lang, AssetProvenance.mirrored, ext)
            size = _move(path, target)
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
                layout.relative(feed_id, target),
                "text/vtt" if fmt is AssetFormat.vtt else "application/x-subrip",
                size,
                language=lang,
                format=fmt,
            )
        )
    if result.chapters:
        target = layout.chapters_path(feed_id, item_id)
        try:
            write_json_atomic(target, result.chapters)
            produced.assets.append(
                PlacedAsset(
                    AssetKind.chapters,
                    layout.relative(feed_id, target),
                    CHAPTERS_MIME,
                    target.stat().st_size,
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


def _mirror_one(layout: Layout, feed_id: str, remote: RemoteAsset) -> PlacedAsset:
    try:
        mirrored = mirror_asset(remote, layout.assets_dir(feed_id))
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
        target = _target(ctx, feed, item.source_key, item.source_url, item.source_item_xml, item_id)

    layout: Layout = ctx.container.layout
    layout.ensure_feed_dirs(target.feed_id)
    try:
        prepared = await ctx.run_blocking(prepare, layout, target)
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
        await _record_failure(ctx, target.feed_id, item_id, exc)
        raise
    except Exception as exc:
        wrapped = TransientError(f"{type(exc).__name__}: {exc}")
        await _record_failure(ctx, target.feed_id, item_id, wrapped)
        raise wrapped from exc

    ctx.progress.set_phase(ProgressPhase.assets)
    produced = await ctx.run_blocking(place_engine_outputs, layout, target.feed_id, item_id, result)
    assets = list(produced.assets)
    if prepared.item_xml is not None:
        await ctx.run_blocking(
            write_atomic, layout.item_xml_path(target.feed_id, item_id), prepared.item_xml
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

    async with ctx.uow() as uow:
        archived = await uow.catalog.mark_state(
            item_id,
            ArchiveState.archived,
            only_from=ArchiveState.archiving,
            media_path=layout.relative(target.feed_id, result.audio_path),
            media_mime=result.mime,
            media_bytes=result.size_bytes,
            duration_seconds=result.duration_seconds,
            archived_at=datetime.now(UTC),
        )
        if not archived:
            # Deleted (tombstoned) while downloading: the files must not outlive the row's intent.
            uow.after_commit(lambda: _discard_outputs(layout, target.feed_id, item_id, result))
            log.warning("archive.row_changed", item_id=item_id)
            return JobOutcome(result={"skipped": "item state changed during the fetch"})
        if prepared.item_xml is not None:
            await uow.catalog.set_source_item_xml(item_id, prepared.item_xml)
        await _record_assets(uow, target.feed_id, item_id, assets)
        await uow.feeds.recount_storage(target.feed_id)
        _, revision = await uow.update_intent(target.feed_id, debounce=True)
        await uow.publish(
            ItemEvent(feed_id=target.feed_id, item_id=item_id, state=ArchiveState.archived)
        )
        await uow.publish(FeedEvent(feed_id=target.feed_id, revision=revision, reason="archived"))
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


def _target(
    ctx: JobContext,
    feed: Feed,
    source_key: str,
    item_source_url: str | None,
    stored_item_xml: str | None,
    item_id: str,
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
        options=engine_options_for(
            ctx.container.settings, feed.engine_options, language=feed.language
        ),
    )


def _discard_outputs(layout: Layout, feed_id: str, item_id: str, result: FetchResult) -> None:
    for path in (result.audio_path, result.info_json_path, result.artwork_path):
        if path is not None:
            path.unlink(missing_ok=True)
    for path in result.subtitle_paths.values():
        path.unlink(missing_ok=True)
    layout.remove_tmp_leftovers(feed_id, item_id)


async def _record_failure(ctx: JobContext, feed_id: str, item_id: str, exc: EngineError) -> None:
    """Transient -> wanted (attempt counted; failed once exhausted); permanent -> failed;
    cancelled and storage full -> wanted without counting the attempt."""
    if isinstance(exc, Cancelled | StorageFull):
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
        if changed:
            await uow.publish(ItemEvent(feed_id=feed_id, item_id=item_id, state=state))
            await uow.update_intent(feed_id, debounce=True)
    log.warning("archive.failed", item_id=item_id, state=state.value, error=str(exc))


__all__ = [
    "IMAGE_MIME",
    "PlacedAsset",
    "Prepared",
    "Produced",
    "mirror_item_assets",
    "place_engine_outputs",
    "prepare",
    "run",
]
