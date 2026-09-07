"""Mirrors: creation (synchronous selection backfill), updates, pause, Refresh, selections."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from copycast.application.capabilities import capability
from copycast.application.events import FeedEvent, ItemEvent, JobEvent
from copycast.application.models import (
    JobRead,
    MirrorCreate,
    MirrorRead,
    MirrorUpdate,
    SelectionRequest,
    SelectionResult,
)
from copycast.application.ports import CancelToken
from copycast.application.services.context import (
    FeedRow,
    JobRow,
    ServiceContext,
    SourceSnapshot,
    UnitOfWorkPort,
)
from copycast.application.services.feeds import require_mirror
from copycast.application.services.items import (
    PRIORITY_FOLLOW,
    PRIORITY_MANUAL,
    enqueue_archive,
    refresh_dedup_key,
)
from copycast.application.services.readmodels import job_read, mirror_read, one_feed_read
from copycast.application.services.sources import probe_snapshots
from copycast.domain.enums import (
    ArchiveState,
    BackfillMode,
    FeedKind,
    JobKind,
    JobTrigger,
    WantedReason,
)
from copycast.domain.exceptions import (
    Ambiguous,
    FeedExists,
    InvalidSelection,
    NotFound,
    SourceKindChange,
)
from copycast.domain.ids import mirror_id
from copycast.domain.selection import parse_selection_strict
from copycast.domain.urls import normalize_source_url
from copycast.logging import get_logger

log = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- refresh


async def enqueue_refresh(uow: UnitOfWorkPort, feed: FeedRow, trigger: JobTrigger) -> JobRow | None:
    """Queue one Refresh; ``None`` when a Refresh of this feed is already queued or running."""
    urgent = trigger in (JobTrigger.manual, JobTrigger.policy)
    priority = PRIORITY_MANUAL if urgent else PRIORITY_FOLLOW
    job = await uow.jobs.enqueue(
        JobKind.refresh,
        trigger,
        feed_id=feed.id,
        priority=priority,
        dedup=refresh_dedup_key(feed.id),
    )
    if job is not None:
        await uow.publish(JobEvent(job=job_read(job)))
        await uow.notify_jobs()
    return job


def refresh_allowed(feed: FeedRow, trigger: JobTrigger, *, cooldown_minutes: int) -> bool:
    """manual: always; scheduled: not Paused; feed_fetch: follow, not Paused, outside cooldown."""
    if trigger == JobTrigger.manual:
        return True
    if feed.paused:
        return False
    if trigger == JobTrigger.feed_fetch:
        if not feed.follow:
            return False
        last = feed.last_refresh_attempt_at
        if last is not None and _now() - last < timedelta(minutes=cooldown_minutes):
            return False
    return True


@capability("request_refresh", response=JobRead)
async def request_refresh(
    ctx: ServiceContext, feed_id: str, trigger: JobTrigger = JobTrigger.manual
) -> JobRead | None:
    """Queue a Refresh of a Mirror; returns the job, or None when the trigger is skipped.

    A manual request while a Refresh is already active returns that job.
    """
    async with ctx.uow_factory() as uow:
        feed = await require_mirror(uow, feed_id)
        cooldown = ctx.settings.refresh.fetch_cooldown_minutes
        if not refresh_allowed(feed, trigger, cooldown_minutes=cooldown):
            return None
        job = await enqueue_refresh(uow, feed, trigger)
        if job is None and trigger == JobTrigger.manual:
            job = await uow.jobs.active_by_dedup_key(refresh_dedup_key(feed_id))
        return job_read(job) if job is not None else None


# --------------------------------------------------------------------------- create


async def _resolve_snapshot(
    ctx: ServiceContext, url: str, token: str | None, *, cancel: CancelToken | None
) -> SourceSnapshot:
    """The cached probe (by token, then by normalized URL) or a fresh one; one candidate."""
    cached = ctx.sources.cached(token=token, url=url)
    if cached is not None:
        return cached
    snapshots = await probe_snapshots(ctx, url, cancel=cancel)
    if len(snapshots) != 1:
        raise Ambiguous([s.candidate for s in snapshots])
    return snapshots[0]


def _feed_values(snapshot: SourceSnapshot, dedup_key: str, body: MirrorCreate) -> dict[str, object]:
    listing = snapshot.listing
    candidate = snapshot.candidate
    return {
        "id": mirror_id(dedup_key),
        "kind": FeedKind.mirror.value,
        "title": candidate.title or listing.title or candidate.source_url,
        "description": candidate.description or listing.description,
        "author": candidate.author or listing.author,
        "artwork_url": candidate.artwork_url or listing.artwork_url,
        "language": listing.language,
        "preferred_language": body.preferred_language,
        "min_duration_seconds": body.min_duration_seconds,
        "source_url": candidate.source_url,
        "source_dedup_key": dedup_key,
        "source_kind": candidate.source_kind.value,
        "service": candidate.service or listing.service,
        "source_etag": snapshot.etag,
        "source_last_modified": snapshot.last_modified,
        "source_channel_xml": snapshot.channel_xml,
        "backfill_mode": body.backfill.mode.value,
        "backfill_latest_n": body.backfill.latest_n,
        "follow": body.effective_follow,
        "paused": False,
        "engine_options": dict(body.engine_options),
    }


@capability("create_mirror", request=MirrorCreate, response=MirrorRead)
async def create_mirror(
    ctx: ServiceContext,
    body: MirrorCreate,
    *,
    trigger: JobTrigger = JobTrigger.ui,
    cancel: CancelToken | None = None,
) -> MirrorRead:
    """Create a Mirror from a probed candidate; the Catalog is populated before returning.

    Reuses the cached probe (``candidate_token`` or the normalized URL, ten
    minutes) and probes otherwise; several candidates -> 422 candidates-ambiguous;
    a Mirror of the same normalized Source -> 409 feed-exists. A ``selection``
    backfill is applied synchronously (archive jobs queued, policy applied);
    ``all`` and ``latest`` queue the first Refresh.
    """
    snapshot = await _resolve_snapshot(ctx, body.source_url, body.candidate_token, cancel=cancel)
    dedup_key = normalize_source_url(snapshot.source_url)
    async with ctx.uow_factory() as uow:
        existing = await uow.feeds.by_dedup_key(dedup_key)
        if existing is None:
            existing = await uow.feeds.get(mirror_id(dedup_key))
        if existing is not None:
            raise FeedExists(existing.id, snapshot.source_url)
        feed = await uow.feeds.add(ctx.rows.feed(**_feed_values(snapshot, dedup_key, body)))
        feed_id = feed.id
        ctx.layout.ensure_feed_dirs(feed_id)
        await uow.catalog.upsert_listing(feed_id, snapshot.listing)
        if body.backfill.mode is BackfillMode.selection:
            expression = body.backfill.selection
            if expression and expression.strip():
                await _apply_selection(
                    uow,
                    feed,
                    SelectionRequest(selection=expression),
                    trigger=trigger,
                    strict=False,
                )
            feed.policy_applied_at = _now()
            await uow.flush()
        else:
            await enqueue_refresh(uow, feed, JobTrigger.policy)
        await uow.update_intent(feed_id)
        await uow.publish(FeedEvent(feed_id=feed_id, revision=feed.revision, reason="created"))
        sources = ctx.sources

        def _save_snapshot() -> None:
            sources.save_snapshot(feed_id, snapshot)

        uow.after_commit(_save_snapshot)
        counts = await uow.catalog.count_by_state(feed_id)
        selected = await _selected_count(uow, feed)
        result = mirror_read(feed, ctx.urls, counts=counts, selected_count=selected)
    log.info("mirror.created", feed_id=feed_id, source_url=snapshot.source_url)
    return result


async def _selected_count(uow: UnitOfWorkPort, feed: FeedRow) -> int:
    if feed.backfill_mode != BackfillMode.selection:
        return 0
    total = 0
    for state in (
        ArchiveState.wanted,
        ArchiveState.archiving,
        ArchiveState.archived,
        ArchiveState.failed,
    ):
        total += len(await uow.catalog.ids_in_state(feed.id, state))
    return total


# --------------------------------------------------------------------------- selections


async def _apply_selection(
    uow: UnitOfWorkPort,
    feed: FeedRow,
    body: SelectionRequest,
    *,
    trigger: JobTrigger,
    strict: bool = True,
) -> SelectionResult:
    numbers = parse_selection_strict(body.selection) if body.selection else []
    resolved = await uow.catalog.resolve_numbers(
        feed.id, numbers=numbers, item_ids=body.item_ids, numbering=body.numbering
    )
    if body.dry_run:
        return SelectionResult(
            resolved=resolved.resolved,
            unresolved=resolved.unresolved,
            numbering_used=resolved.numbering_used,
            already_archived_count=resolved.already_archived_count,
            jobs=[],
            dry_run=True,
        )
    if strict and not resolved.resolved:
        raise InvalidSelection(
            "the selection matches no Catalog item", unresolved=resolved.unresolved
        )
    changed = set(await uow.catalog.set_wanted(resolved.resolved, WantedReason.manual))
    jobs: list[JobRead] = []
    for item in await uow.catalog.get_many(resolved.resolved):
        if item.archive_state in (ArchiveState.wanted, ArchiveState.archiving):
            if item.id in changed:
                await uow.publish(
                    ItemEvent(feed_id=feed.id, item_id=item.id, state=ArchiveState.wanted)
                )
            job = await enqueue_archive(uow, item, trigger=trigger, priority=PRIORITY_MANUAL)
            if job is not None:
                jobs.append(job_read(job))
    if changed:
        await uow.update_intent(feed.id)
    if jobs:
        await uow.notify_jobs()
    return SelectionResult(
        resolved=resolved.resolved,
        unresolved=resolved.unresolved,
        numbering_used=resolved.numbering_used,
        already_archived_count=resolved.already_archived_count,
        jobs=jobs,
        dry_run=False,
    )


@capability("select_items", request=SelectionRequest, response=SelectionResult)
async def select_items(
    ctx: ServiceContext,
    feed_id: str,
    body: SelectionRequest,
    *,
    trigger: JobTrigger = JobTrigger.ui,
) -> SelectionResult:
    """Archive ``"1-42, 180"`` (or explicit ids) exactly once; ``dry_run`` only resolves."""
    async with ctx.uow_factory() as uow:
        feed = await uow.feeds.require(feed_id)
        return await _apply_selection(uow, feed, body, trigger=trigger)


# --------------------------------------------------------------------------- update


@capability("update_mirror", request=MirrorUpdate, response=MirrorRead)
async def update_mirror(
    ctx: ServiceContext,
    feed_id: str,
    body: MirrorUpdate,
    *,
    cancel: CancelToken | None = None,
) -> MirrorRead:
    """PATCH a Mirror's policy, language, minimum length, engine options or Source.

    Retargeting keeps the archive; a kind change is refused. ``language: null`` and
    ``min_duration_seconds: null`` present in the body clear the value.
    """
    fields = body.model_fields_set
    snapshot: SourceSnapshot | None = None
    if "source_url" in fields and body.source_url is not None:
        snapshot = await _resolve_snapshot(ctx, body.source_url, None, cancel=cancel)
    async with ctx.uow_factory() as uow:
        feed = await require_mirror(uow, feed_id)
        refresh_needed = False
        if snapshot is not None:
            await _retarget(uow, feed, snapshot)
            refresh_needed = True
        if "follow" in fields and body.follow is not None:
            feed.follow = body.follow
        if "engine_options" in fields and body.engine_options is not None:
            feed.engine_options = dict(body.engine_options)
        if "preferred_language" in fields:
            feed.preferred_language = body.preferred_language
        if "min_duration_seconds" in fields:
            feed.min_duration_seconds = body.min_duration_seconds
        if "backfill" in fields and body.backfill is not None:
            feed.backfill_mode = body.backfill.mode.value
            feed.backfill_latest_n = body.backfill.latest_n
            if body.backfill.mode is BackfillMode.selection:
                feed.policy_applied_at = feed.policy_applied_at or _now()
                expression = body.backfill.selection
                if expression and expression.strip():
                    await _apply_selection(
                        uow,
                        feed,
                        SelectionRequest(selection=expression),
                        trigger=JobTrigger.ui,
                        strict=False,
                    )
            else:
                feed.policy_applied_at = None
                refresh_needed = True
        await uow.flush()
        await uow.update_intent(feed_id)
        if refresh_needed and not feed.paused:
            await enqueue_refresh(uow, feed, JobTrigger.manual)
        await uow.publish(FeedEvent(feed_id=feed_id, revision=feed.revision, reason="updated"))
        if snapshot is not None:
            sources = ctx.sources
            kept = snapshot

            def _save_snapshot() -> None:
                sources.save_snapshot(feed_id, kept)

            uow.after_commit(_save_snapshot)
        read = await one_feed_read(uow, ctx.urls, feed)
        if not isinstance(read, MirrorRead):  # pragma: no cover - require_mirror guarantees it
            raise NotFound("mirror", feed_id)
        return read


async def _retarget(uow: UnitOfWorkPort, feed: FeedRow, snapshot: SourceSnapshot) -> None:
    if feed.source_kind != snapshot.source_kind.value:
        raise SourceKindChange(feed.source_kind or "unknown", snapshot.source_kind.value)
    dedup_key = normalize_source_url(snapshot.source_url)
    other = await uow.feeds.by_dedup_key(dedup_key)
    if other is not None and other.id != feed.id:
        raise FeedExists(other.id, snapshot.source_url)
    feed.source_url = snapshot.source_url
    feed.source_dedup_key = dedup_key
    feed.service = snapshot.candidate.service or snapshot.listing.service
    feed.source_etag = snapshot.etag
    feed.source_last_modified = snapshot.last_modified
    feed.source_channel_xml = snapshot.channel_xml
    await uow.flush()
    await uow.catalog.upsert_listing(feed.id, snapshot.listing)


# --------------------------------------------------------------------------- pause


@capability("set_paused", response=MirrorRead)
async def set_paused(ctx: ServiceContext, feed_id: str, paused: bool) -> MirrorRead:
    """Pause (cancel the feed's jobs) or resume (queue a manual Refresh) a Mirror."""
    async with ctx.uow_factory() as uow:
        feed = await require_mirror(uow, feed_id)
        if feed.paused != paused:
            feed.paused = paused
            await uow.flush()
            if paused:
                await uow.jobs.cancel_for_feed(feed_id)
            await uow.update_intent(feed_id)
            await uow.publish(
                FeedEvent(
                    feed_id=feed_id,
                    revision=feed.revision,
                    reason="paused" if paused else "resumed",
                )
            )
            if not paused:
                await enqueue_refresh(uow, feed, JobTrigger.manual)
        read = await one_feed_read(uow, ctx.urls, feed)
        if not isinstance(read, MirrorRead):  # pragma: no cover - require_mirror guarantees it
            raise NotFound("mirror", feed_id)
        return read


__all__ = [
    "create_mirror",
    "enqueue_refresh",
    "refresh_allowed",
    "request_refresh",
    "select_items",
    "set_paused",
    "update_mirror",
]
