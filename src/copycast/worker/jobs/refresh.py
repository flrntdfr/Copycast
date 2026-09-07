"""The Refresh job: list the Source, apply the Catalog, run the policy, queue archives.

RSS Sources are fetched with a conditional GET (a 304 is ``unchanged`` and
still runs the policy); other Sources go through ``engine.list_source``. A
listing failure ends the run ``failed`` with ``last_error`` and touches
nothing else, so the Mirror keeps serving what it has.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copycast.adapters.assets.mirror import AssetError, feed_artwork_asset, mirror_asset
from copycast.adapters.db.models import Feed
from copycast.adapters.db.uow import UnitOfWork
from copycast.adapters.sources import rss
from copycast.adapters.sources.http import SourceError
from copycast.adapters.storage.atomic import write_atomic, write_json_atomic
from copycast.adapters.storage.layout import Layout
from copycast.application.events import FeedEvent
from copycast.application.ports import (
    Cancelled,
    CancelToken,
    Engine,
    EngineError,
    EngineLog,
    PermanentError,
)
from copycast.application.services.defaults import effective, load_defaults
from copycast.domain.enums import (
    ArchiveState,
    AssetKind,
    AssetState,
    BackfillMode,
    FeedKind,
    JobTrigger,
    ProgressPhase,
    RefreshRunStatus,
    SourceKind,
    WantedReason,
)
from copycast.domain.listing import SourceListing
from copycast.logging import get_logger
from copycast.worker.jobs import JobContext, JobOutcome
from copycast.worker.jobs.common import (
    engine_options_for,
    enqueue_archive_jobs,
    source_error_to_engine,
)

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Listed:
    """What the Source answered; ``listing`` is None for an unchanged (304) RSS Source."""

    listing: SourceListing | None
    body: bytes | None = None
    etag: str | None = None
    last_modified: str | None = None
    channel_xml: str | None = None
    engine_version: str | None = None
    pages: int = 1

    @property
    def unchanged(self) -> bool:
        return self.listing is None


@dataclass(frozen=True, slots=True)
class _FeedSnapshot:
    id: str
    source_url: str
    source_kind: SourceKind
    etag: str | None
    last_modified: str | None
    first_listing: bool
    options: dict[str, Any]


def list_rss(url: str, *, etag: str | None, last_modified: str | None, follow_next: bool) -> Listed:
    """Conditional GET of an RSS Source; pages are followed only on the first listing."""
    try:
        fetched = rss.fetch_feed(
            url, etag=etag, last_modified=last_modified, follow_next=follow_next
        )
    except SourceError as exc:
        raise source_error_to_engine(exc) from exc
    if fetched.not_modified or fetched.parsed is None:
        return Listed(None, None, fetched.etag or etag, fetched.last_modified or last_modified)
    return Listed(
        fetched.parsed.listing,
        fetched.body,
        fetched.etag,
        fetched.last_modified,
        rss.channel_xml(fetched.parsed),
        None,
        fetched.pages,
    )


def list_engine(
    engine: Engine, url: str, options: Mapping[str, Any], cancel: CancelToken, log_: EngineLog
) -> Listed:
    listing = engine.list_source(url, options, cancel, log_)
    return Listed(listing, engine_version=engine.version().version)


def write_source_snapshot(layout: Layout, feed_id: str, listed: Listed) -> Path | None:
    """``source/feed.xml`` (verbatim RSS) or ``source/listing.json`` (raw flat listing)."""
    if listed.body is not None:
        return write_atomic(layout.source_xml_path(feed_id), listed.body)
    if listed.listing is not None and listed.listing.raw is not None:
        return write_json_atomic(layout.source_listing_path(feed_id), listed.listing.raw)
    return None


async def apply_policy(uow: UnitOfWork, feed: Feed, *, now: datetime | None = None) -> list[str]:
    """Backfill once (``policy_applied_at`` null), then follow; returns the ids made wanted.

    all: every listed archivable Available item; latest: the N highest ordinals;
    selection: nothing. Follow wants Available items first seen after the policy
    was applied, so deleted (tombstoned) and failed items are never re-wanted.
    Items shorter than the Mirror's ``min_duration_seconds`` stay Available.
    """
    now = now or datetime.now(UTC)
    shortest = effective(
        load_defaults(uow.layout),
        language=feed.preferred_language,
        min_duration_seconds=feed.min_duration_seconds,
    ).min_duration_seconds
    if feed.policy_applied_at is None:
        mode = BackfillMode(feed.backfill_mode or BackfillMode.all)
        if mode is BackfillMode.all:
            ids = await uow.catalog.available_ids(feed.id, min_duration_seconds=shortest)
        elif mode is BackfillMode.latest:
            ids = await uow.catalog.available_ids(
                feed.id, latest_n=feed.backfill_latest_n, min_duration_seconds=shortest
            )
        else:
            ids = []
        changed = await uow.catalog.set_wanted(ids, WantedReason.backfill)
        feed.policy_applied_at = now
        await uow.flush()
        return changed
    if feed.follow:
        ids = await uow.catalog.available_ids(
            feed.id, first_seen_after=feed.policy_applied_at, min_duration_seconds=shortest
        )
        return await uow.catalog.set_wanted(ids, WantedReason.follow)
    return []


async def run(ctx: JobContext) -> JobOutcome:
    job = ctx.job
    if job.feed_id is None:
        raise PermanentError("refresh job without a feed")
    feed_id = job.feed_id
    async with ctx.uow() as uow:
        feed = await uow.feeds.require(feed_id)
        if feed.kind != FeedKind.mirror or feed.source_url is None or feed.source_kind is None:
            raise PermanentError(f"feed {feed_id!r} is not a Mirror")
        run_row = await uow.telemetry.start_refresh_run(
            feed_id, trigger=JobTrigger(job.trigger), job_id=job.id
        )
        run_id = run_row.id
        await uow.feeds.set_refresh_attempt(feed_id)
        snapshot = _FeedSnapshot(
            id=feed.id,
            source_url=feed.source_url,
            source_kind=SourceKind(feed.source_kind),
            etag=feed.source_etag,
            last_modified=feed.source_last_modified,
            first_listing=feed.last_refresh_success_at is None,
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

    ctx.progress.set_phase(ProgressPhase.listing)
    try:
        ctx.check_cancelled("refresh")
        listed = await _list(ctx, snapshot)
    except EngineError as exc:
        await _record_failure(ctx, feed_id, run_id, exc)
        raise
    except Exception as exc:  # unexpected: treat as transient, keep the Mirror serving
        await _record_failure(ctx, feed_id, run_id, exc)
        raise

    layout: Layout = ctx.container.layout
    layout.ensure_feed_dirs(feed_id)
    if not listed.unchanged:
        await ctx.run_blocking(write_source_snapshot, layout, feed_id, listed)
    artwork = await _mirror_feed_artwork(ctx, feed_id, listed.listing, layout)

    async with ctx.uow() as uow:
        feed = await uow.feeds.get_for_update(feed_id)
        listed_count = new_count = delisted_count = 0
        if listed.listing is not None:
            upsert = await uow.catalog.upsert_listing(feed_id, listed.listing)
            listed_count, new_count, delisted_count = (
                upsert.listed_count,
                upsert.new_count,
                upsert.delisted_count,
            )
            if snapshot.source_kind is SourceKind.rss:
                feed.source_channel_xml = listed.channel_xml
                feed.source_etag = listed.etag
                feed.source_last_modified = listed.last_modified
                await uow.flush()
            listing = listed.listing
            await uow.feeds.apply_metadata(
                feed_id,
                title=listing.title,
                description=listing.description,
                author=listing.author,
                artwork_url=listing.artwork_url,
                language=listing.language,
                service=listing.service,
            )
        elif snapshot.source_kind is SourceKind.rss:
            feed.source_etag = listed.etag
            feed.source_last_modified = listed.last_modified
            await uow.flush()
        if artwork is not None:
            await _record_artwork(uow, feed_id, artwork)
        wanted = await apply_policy(uow, feed)
        wanted_rows = await uow.catalog.get_many(
            await uow.catalog.ids_in_state(feed_id, ArchiveState.wanted)
        )
        enqueued = await enqueue_archive_jobs(uow, wanted_rows, trigger=JobTrigger.policy)
        status = RefreshRunStatus.unchanged if listed.unchanged else RefreshRunStatus.succeeded
        await uow.telemetry.finish_refresh_run(
            run_id,
            status,
            listed_count=listed_count,
            new_count=new_count,
            delisted_count=delisted_count,
            wanted_count=len(wanted),
            engine_version=listed.engine_version,
        )
        await uow.feeds.set_refresh_outcome(feed_id, success_at=datetime.now(UTC), error=None)
        if artwork is not None:
            await uow.feeds.recount_storage(feed_id)
        _, revision = await uow.update_intent(feed_id)
        await uow.publish(FeedEvent(feed_id=feed_id, revision=revision, reason="refresh"))
        if enqueued:
            await uow.notify_jobs()
    log.info(
        "refresh.done",
        feed_id=feed_id,
        status=status.value,
        listed=listed_count,
        new=new_count,
        delisted=delisted_count,
        wanted=len(wanted),
        enqueued=len(enqueued),
    )
    return JobOutcome(
        result={
            "status": status.value,
            "listed": listed_count,
            "new": new_count,
            "delisted": delisted_count,
            "wanted": len(wanted),
            "enqueued": len(enqueued),
            "pages": listed.pages,
        },
        engine_version=listed.engine_version,
    )


async def _list(ctx: JobContext, feed: _FeedSnapshot) -> Listed:
    if feed.source_kind is SourceKind.rss:
        # The first listing walks rel="next" pages and must not be short-cut by a 304.
        return await ctx.run_blocking(
            list_rss,
            feed.source_url,
            etag=None if feed.first_listing else feed.etag,
            last_modified=None if feed.first_listing else feed.last_modified,
            follow_next=feed.first_listing,
        )
    return await ctx.run_blocking(
        list_engine, ctx.container.engine, feed.source_url, feed.options, ctx.cancel, ctx.log
    )


async def _record_failure(ctx: JobContext, feed_id: str, run_id: int, exc: BaseException) -> None:
    cancelled = isinstance(exc, Cancelled)
    async with ctx.uow() as uow:
        await uow.telemetry.finish_refresh_run(
            run_id,
            RefreshRunStatus.cancelled if cancelled else RefreshRunStatus.failed,
            error=str(exc),
        )
        if not cancelled:
            await uow.feeds.set_refresh_outcome(feed_id, success_at=None, error=str(exc))
            revision = await uow.bump_revision(feed_id)
            await uow.publish(
                FeedEvent(feed_id=feed_id, revision=revision, reason="refresh-failed")
            )
    log.warning("refresh.failed", feed_id=feed_id, error=str(exc), cancelled=cancelled)


@dataclass(frozen=True, slots=True)
class _Artwork:
    local_path: str
    mime: str
    size_bytes: int
    remote_url: str
    error: str | None = None


async def _mirror_feed_artwork(
    ctx: JobContext, feed_id: str, listing: SourceListing | None, layout: Layout
) -> _Artwork | None:
    """Mirror the feed Artwork once (when no archived Artwork asset exists)."""
    if listing is None or not listing.artwork_url:
        return None
    async with ctx.uow() as uow:
        existing = await uow.assets.feed_artwork(feed_id)
    if existing is not None and existing.state == AssetState.archived:
        return None
    remote = feed_artwork_asset(listing.artwork_url)
    try:
        mirrored = await ctx.run_blocking(mirror_asset, remote, layout.assets_dir(feed_id))
    except AssetError as exc:
        return _Artwork("", "", 0, listing.artwork_url, error=str(exc))
    return _Artwork(mirrored.local_path, mirrored.mime, mirrored.size_bytes, listing.artwork_url)


async def _record_artwork(uow: UnitOfWork, feed_id: str, artwork: _Artwork) -> None:
    if artwork.error is not None:
        await uow.assets.upsert(
            feed_id,
            None,
            AssetKind.artwork,
            remote_url=artwork.remote_url,
            state=AssetState.failed,
            last_error=artwork.error,
        )
        return
    await uow.assets.upsert(
        feed_id,
        None,
        AssetKind.artwork,
        remote_url=artwork.remote_url,
        local_path=artwork.local_path,
        mime=artwork.mime,
        size_bytes=artwork.size_bytes,
        state=AssetState.archived,
        fetched_at=datetime.now(UTC),
    )


__all__ = [
    "Listed",
    "apply_policy",
    "list_engine",
    "list_rss",
    "run",
    "write_source_snapshot",
]
