"""The light Refresh: a fetch-triggered check of a Source's newest items, inline in the api.

A GET of a Mirror Feed runs it instead of queueing a worker Refresh and
waiting for it. What it does depends on the Source: an RSS Source gets one
conditional GET (a 304 is ``unchanged``, a 200 a *complete* listing that may
delist), a yt-dlp channel a shallow first-page listing of
:data:`LIGHT_LISTING_LIMIT` entries (a *partial* listing: it adds and re-dates
items but never delists or renumbers), and a yt-dlp playlist, which lists
oldest first so a first page is not its newest items, queues the normal full
Refresh job and answers at once. The policy runs after a listing and archive
jobs are queued for what it wants.

It never delays the feed for long (:data:`LIGHT_REFRESH_DEADLINE_SECONDS`)
and never raises to the route: every failure is logged and returned as
``status="failed"``. ``last_refresh_attempt_at``, ``last_refresh_success_at``
and ``last_error`` belong to the full Refresh and are never touched here; the
light Refresh has its own stamp, ``last_light_refresh_at``, and its runs are
``refresh_runs`` rows flagged ``light``.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from copycast.application.events import FeedEvent
from copycast.application.ports import CancelToken
from copycast.application.services.context import (
    FeedRow,
    RssFetch,
    ServiceContext,
    UnitOfWorkPort,
)
from copycast.application.services.defaults import effective, load_defaults
from copycast.application.services.feeds import require_mirror
from copycast.application.services.items import (
    PRIORITY_BACKFILL,
    PRIORITY_FOLLOW,
    PRIORITY_MANUAL,
    archive_dedup_key,
    enqueue_archive,
)
from copycast.application.services.mirrors import request_refresh
from copycast.application.services.policy import apply_policy, delete_rows, shortest_for
from copycast.application.services.retry import retry_concurrent
from copycast.domain.enums import (
    ArchiveState,
    DeleteReason,
    JobTrigger,
    ListingOrder,
    RefreshRunStatus,
    SourceKind,
    WantedReason,
)
from copycast.domain.listing import SourceListing
from copycast.domain.urls import youtube_playlist_id
from copycast.logging import get_logger

log = get_logger(__name__)

LIGHT_REFRESH_DEADLINE_SECONDS = 8.0
"""How long a fetch waits for the Source before the feed is served as it is."""
LIGHT_LISTING_LIMIT = 15
"""Entries of a yt-dlp Source's first page a light Refresh asks for."""

Status = Literal["skipped", "unchanged", "succeeded", "queued", "failed"]
Kind = Literal["unchanged", "complete", "partial", "queued"]
Mode = Literal["rss", "shallow", "queue"]


@dataclass(frozen=True, slots=True)
class LightRefreshResult:
    """What a light Refresh did for one fetch."""

    status: Status
    new_count: int = 0
    wanted_count: int = 0
    error: str | None = None


def light_refresh_allowed(feed: FeedRow, *, now: datetime, cooldown_minutes: int) -> bool:
    """Not Paused, and no Refresh of either kind within the cooldown (absent stamps ignored).

    The newest of ``last_refresh_attempt_at`` (the full Refresh) and
    ``last_light_refresh_at`` counts, so a fetch right after a scheduled
    Refresh lists nothing again.
    """
    if feed.paused:
        return False
    stamps = [
        stamp
        for stamp in (feed.last_refresh_attempt_at, feed.last_light_refresh_at)
        if stamp is not None
    ]
    if not stamps:
        return True
    return now - max(stamps) >= timedelta(minutes=cooldown_minutes)


# --------------------------------------------------------------------------- per-feed lock

_locks: dict[str, asyncio.Lock] = {}
_lock_users: dict[str, int] = {}


@contextlib.asynccontextmanager
async def feed_lock(feed_id: str) -> AsyncGenerator[None]:
    """One light Refresh of a feed at a time in this process; the entry goes with its last user."""
    lock = _locks.setdefault(feed_id, asyncio.Lock())
    _lock_users[feed_id] = _lock_users.get(feed_id, 0) + 1
    try:
        async with lock:
            yield
    finally:
        remaining = _lock_users.get(feed_id, 1) - 1
        if remaining <= 0:
            _lock_users.pop(feed_id, None)
            _locks.pop(feed_id, None)
        else:
            _lock_users[feed_id] = remaining


def locked_feed_ids() -> list[str]:
    """The feeds a light Refresh currently holds or waits on (tests)."""
    return sorted(_locks)


# --------------------------------------------------------------------------- plan and listing


@dataclass(frozen=True, slots=True)
class _Plan:
    """What the feed row alone decides: how the Source is listed."""

    feed_id: str
    source_url: str
    mode: Mode
    etag: str | None
    last_modified: str | None
    options: dict[str, Any]
    language: str | None


@dataclass(frozen=True, slots=True)
class _Listed:
    kind: Kind
    listing: SourceListing | None = None
    rss: RssFetch | None = None


def _plan(ctx: ServiceContext, feed: FeedRow) -> _Plan:
    if feed.source_url is None or feed.source_kind is None:
        raise ValueError(f"Mirror {feed.id!r} has no Source to check")
    kind = SourceKind(feed.source_kind)
    if kind is SourceKind.rss:
        mode: Mode = "rss"
    elif youtube_playlist_id(feed.source_url) is not None:
        mode = "queue"
    else:
        mode = "shallow"
    language = effective(
        load_defaults(ctx.layout),
        language=feed.preferred_language,
        min_duration_seconds=feed.min_duration_seconds,
    ).language
    return _Plan(
        feed_id=feed.id,
        source_url=feed.source_url,
        mode=mode,
        etag=feed.source_etag,
        last_modified=feed.source_last_modified,
        options=dict(feed.engine_options or {}),
        language=language,
    )


def _list(ctx: ServiceContext, plan: _Plan, cancel: CancelToken) -> _Listed:
    """The blocking part: one conditional GET or one shallow engine listing."""
    if plan.mode == "rss":
        fetched = ctx.sources.fetch_rss(
            plan.source_url, etag=plan.etag, last_modified=plan.last_modified
        )
        if fetched.not_modified or fetched.listing is None:
            return _Listed("unchanged", rss=fetched)
        return _Listed("complete", listing=fetched.listing, rss=fetched)
    listing = ctx.sources.list_shallow(
        plan.source_url,
        options=plan.options,
        language=plan.language,
        cancel=cancel,
        limit=LIGHT_LISTING_LIMIT,
    )
    if listing.listing_order is ListingOrder.oldest_first:
        # A playlist-like Source: its first page is its oldest items, not its newest.
        return _Listed("queued")
    return _Listed("partial", listing=listing)


# --------------------------------------------------------------------------- apply


def _priority(reason: str | None) -> int:
    if reason == WantedReason.backfill:
        return PRIORITY_BACKFILL
    if reason == WantedReason.follow:
        return PRIORITY_FOLLOW
    return PRIORITY_MANUAL


async def _enqueue_wanted(uow: UnitOfWorkPort, feed_id: str) -> int:
    """One archive job per wanted row lacking an active one; returns how many were created."""
    rows = await uow.catalog.get_many(await uow.catalog.ids_in_state(feed_id, ArchiveState.wanted))
    created = 0
    for item in rows:
        if await uow.jobs.active_by_dedup_key(archive_dedup_key(item.id)) is not None:
            continue
        job = await enqueue_archive(
            uow, item, trigger=JobTrigger.policy, priority=_priority(item.wanted_reason)
        )
        if job is not None:
            created += 1
    return created


async def _apply(
    ctx: ServiceContext, plan: _Plan, listed: _Listed, run_id: int
) -> tuple[LightRefreshResult, int]:
    """Apply what was listed in one transaction; returns the result and the jobs created."""
    feed_id = plan.feed_id
    defaults = load_defaults(ctx.layout)
    async with ctx.uow_factory() as uow:
        feed = await uow.feeds.get_for_update(feed_id)
        rss = listed.rss
        if listed.kind == "unchanged":
            if rss is not None:
                feed.source_etag = rss.etag
                feed.source_last_modified = rss.last_modified
                await uow.flush()
            await uow.telemetry.finish_refresh_run(run_id, RefreshRunStatus.unchanged)
            return LightRefreshResult(status="unchanged"), 0
        listing = listed.listing
        if listing is None:  # pragma: no cover - _list pairs complete/partial with a listing
            raise ValueError("a listing is required")
        partial = listed.kind == "partial"
        upsert = await uow.catalog.upsert_listing(feed_id, listing, partial=partial)
        if rss is not None:
            feed.source_channel_xml = rss.channel_xml
            feed.source_etag = rss.etag
            feed.source_last_modified = rss.last_modified
            await uow.flush()
        await uow.feeds.apply_metadata(
            feed_id,
            title=listing.title,
            description=listing.description,
            author=listing.author,
            artwork_url=listing.artwork_url,
            language=listing.language,
            service=listing.service,
        )
        if not partial and feed.sync_deletions:
            dropped = await uow.catalog.delisted_archived(feed_id)
            if dropped:
                await delete_rows(uow, feed, list(dropped), DeleteReason.synced)
                await uow.feeds.recount_storage(feed_id)
        wanted = await apply_policy(uow, feed, min_duration_seconds=shortest_for(feed, defaults))
        enqueued = await _enqueue_wanted(uow, feed_id)
        await uow.telemetry.finish_refresh_run(
            run_id,
            RefreshRunStatus.succeeded,
            listed_count=upsert.listed_count,
            new_count=upsert.new_count,
            delisted_count=upsert.delisted_count,
            wanted_count=len(wanted),
        )
        if not partial or upsert.new_count > 0 or wanted:
            _, revision = await uow.update_intent(feed_id)
            await uow.publish(FeedEvent(feed_id=feed_id, revision=revision, reason="light-refresh"))
        if enqueued:
            await uow.notify_jobs()
        if rss is not None and rss.body is not None:
            body = rss.body
            sources = ctx.sources

            def _save_snapshot() -> None:
                sources.save_rss_snapshot(feed_id, body)

            uow.after_commit(_save_snapshot)
        return (
            LightRefreshResult(
                status="succeeded", new_count=upsert.new_count, wanted_count=len(wanted)
            ),
            enqueued,
        )


async def _finish_run(
    ctx: ServiceContext, run_id: int, status: RefreshRunStatus, *, error: str | None
) -> None:
    async with ctx.uow_factory() as uow:
        await uow.telemetry.finish_refresh_run(run_id, status, error=error)


# --------------------------------------------------------------------------- entry point


async def light_refresh(
    ctx: ServiceContext,
    feed_id: str,
    *,
    deadline_seconds: float = LIGHT_REFRESH_DEADLINE_SECONDS,
) -> LightRefreshResult:
    """Check a Mirror's Source for its newest items now, within ``deadline_seconds``.

    Skipped when the Mirror is Paused or a Refresh of either kind ran within
    ``refresh.fetch_cooldown_minutes``; the stamp is written before the Source
    is asked so concurrent fetches skip. Never raises.
    """
    started = time.monotonic()
    kind: Kind | None = None
    enqueued = 0
    try:
        async with feed_lock(feed_id):
            result, kind, enqueued = await _run(ctx, feed_id, deadline_seconds)
    except Exception as exc:
        log.warning("light_refresh.failed", feed_id=feed_id, error=str(exc))
        result = LightRefreshResult(status="failed", error=str(exc))
    if result.status == "skipped":
        log.debug("light_refresh.skipped", feed_id=feed_id)
        return result
    log.info(
        "light_refresh.done",
        feed_id=feed_id,
        status=result.status,
        kind=kind,
        new=result.new_count,
        wanted=result.wanted_count,
        enqueued=enqueued,
        duration_ms=round((time.monotonic() - started) * 1000, 1),
        error=result.error,
    )
    return result


async def _run(
    ctx: ServiceContext, feed_id: str, deadline_seconds: float
) -> tuple[LightRefreshResult, Kind | None, int]:
    now = datetime.now(UTC)
    cooldown = ctx.settings.refresh.fetch_cooldown_minutes
    async with ctx.uow_factory() as uow:
        feed = await require_mirror(uow, feed_id)
        if not light_refresh_allowed(feed, now=now, cooldown_minutes=cooldown):
            return LightRefreshResult(status="skipped"), None, 0
        await uow.feeds.set_light_refresh_at(feed_id, now)
        plan = _plan(ctx, feed)
    if plan.mode == "queue":
        await request_refresh(ctx, feed_id, JobTrigger.feed_fetch)
        return LightRefreshResult(status="queued"), "queued", 0

    async with ctx.uow_factory() as uow:
        run = await uow.telemetry.start_refresh_run(
            feed_id, trigger=JobTrigger.feed_fetch, light=True
        )
        run_id = run.id
    cancel = CancelToken()
    try:
        async with asyncio.timeout(deadline_seconds):
            listed = await asyncio.to_thread(_list, ctx, plan, cancel)
    except TimeoutError:
        cancel.cancel()
        error = f"listing {plan.source_url} took longer than {deadline_seconds:g} seconds"
        log.warning("light_refresh.timeout", feed_id=feed_id, deadline_seconds=deadline_seconds)
        await _finish_run(ctx, run_id, RefreshRunStatus.failed, error=error)
        return LightRefreshResult(status="failed", error=error), None, 0
    except asyncio.CancelledError:
        cancel.cancel()
        with contextlib.suppress(Exception):
            await _finish_run(ctx, run_id, RefreshRunStatus.cancelled, error="fetch cancelled")
        raise
    except Exception as exc:
        error = str(exc) or type(exc).__name__
        await _finish_run(ctx, run_id, RefreshRunStatus.failed, error=error)
        return LightRefreshResult(status="failed", error=error), None, 0

    if listed.kind == "queued":
        await _finish_run(
            ctx,
            run_id,
            RefreshRunStatus.cancelled,
            error="the Source lists oldest first: a full Refresh was queued instead",
        )
        await request_refresh(ctx, feed_id, JobTrigger.feed_fetch)
        return LightRefreshResult(status="queued"), "queued", 0

    result, enqueued = await retry_concurrent(
        lambda: _apply(ctx, plan, listed, run_id), what=f"light_refresh:{feed_id}"
    )
    return result, listed.kind, enqueued


__all__ = [
    "LIGHT_LISTING_LIMIT",
    "LIGHT_REFRESH_DEADLINE_SECONDS",
    "LightRefreshResult",
    "feed_lock",
    "light_refresh",
    "light_refresh_allowed",
    "locked_feed_ids",
]
