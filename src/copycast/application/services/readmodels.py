"""Rows -> the response models shared by the API, MCP and UI.

Every URL a model carries comes from :class:`PublicUrls`; nothing here
composes one by hand. Health, counts and selection summaries are derived
here so the three surfaces agree on the wording.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence

from copycast.application.models import (
    ApiKeyRead,
    AssetRead,
    BackfillPolicy,
    CatalogCounts,
    FeedHealth,
    FeedRead,
    InboxRead,
    ItemMedia,
    ItemRead,
    JobRead,
    MirrorRead,
    RequestRead,
    SelectionSummary,
)
from copycast.application.services.context import (
    ApiKeyRow,
    AssetRow,
    FeedRow,
    ItemRow,
    JobRow,
    PublicUrls,
    RequestRow,
    UnitOfWorkPort,
)
from copycast.domain.enums import (
    ArchiveState,
    AssetFormat,
    AssetKind,
    AssetProvenance,
    AssetState,
    BackfillMode,
    FeedKind,
    HealthStatus,
    KeyScope,
    RequestedVia,
    RequestStatus,
    SourceKind,
)

SELECTED_STATES: tuple[ArchiveState, ...] = (
    ArchiveState.wanted,
    ArchiveState.archiving,
    ArchiveState.archived,
    ArchiveState.failed,
)


def feed_health(feed: FeedRow, counts: CatalogCounts) -> FeedHealth:
    """paused > last Refresh failed > Episodes failing > never refreshed > ok."""
    if feed.paused:
        return FeedHealth(status=HealthStatus.paused, reason="Paused")
    if feed.last_error:
        return FeedHealth(status=HealthStatus.error, reason=feed.last_error)
    if counts.failed:
        noun = "Episode" if counts.failed == 1 else "Episodes"
        return FeedHealth(status=HealthStatus.warn, reason=f"{counts.failed} {noun} failed")
    if feed.last_refresh_attempt_at is None:
        return FeedHealth(status=HealthStatus.never, reason="never refreshed")
    return FeedHealth(status=HealthStatus.ok)


def selection_summary(feed: FeedRow, selected_count: int) -> SelectionSummary | None:
    if feed.backfill_mode != BackfillMode.selection:
        return None
    return SelectionSummary(count=selected_count, applied_at=feed.policy_applied_at)


def mirror_read(
    feed: FeedRow,
    urls: PublicUrls,
    *,
    counts: CatalogCounts,
    selected_count: int = 0,
) -> MirrorRead:
    if feed.source_url is None or feed.source_kind is None or feed.backfill_mode is None:
        raise ValueError(f"feed {feed.id} is not a Mirror")
    return MirrorRead(
        id=feed.id,
        title=feed.title,
        description=feed.description,
        artwork_url=feed.artwork_url,
        feed_url=urls.feed_url(feed.id, username=feed.auth_username, password=feed.auth_password),
        feed_credentials=urls.feed_credentials(feed.auth_username, feed.auth_password),
        episode_count=counts.archived,
        storage_bytes=feed.storage_bytes,
        revision=feed.revision,
        created_at=feed.created_at,
        source_url=feed.source_url,
        service=feed.service,
        source_kind=SourceKind(feed.source_kind),
        paused=feed.paused,
        follow=feed.follow,
        backfill=BackfillPolicy(
            mode=BackfillMode(feed.backfill_mode), latest_n=feed.backfill_latest_n
        ),
        engine_options=dict(feed.engine_options or {}),
        last_refresh_attempt_at=feed.last_refresh_attempt_at,
        last_refresh_success_at=feed.last_refresh_success_at,
        last_error=feed.last_error,
        health=feed_health(feed, counts),
        counts=counts,
        selection=selection_summary(feed, selected_count),
    )


def inbox_read(
    feed: FeedRow, urls: PublicUrls, *, counts: CatalogCounts, request_count: int = 0
) -> InboxRead:
    return InboxRead(
        id=feed.id,
        title=feed.title,
        name=feed.title,
        description=feed.description,
        artwork_url=feed.artwork_url,
        feed_url=urls.feed_url(feed.id, username=feed.auth_username, password=feed.auth_password),
        feed_credentials=urls.feed_credentials(feed.auth_username, feed.auth_password),
        episode_count=counts.archived,
        storage_bytes=feed.storage_bytes,
        revision=feed.revision,
        created_at=feed.created_at,
        autoprune_days=feed.autoprune_days,
        request_count=request_count,
    )


def feed_read(
    feed: FeedRow,
    urls: PublicUrls,
    *,
    counts: CatalogCounts,
    request_count: int = 0,
    selected_count: int = 0,
) -> FeedRead:
    if feed.kind == FeedKind.inbox:
        return inbox_read(feed, urls, counts=counts, request_count=request_count)
    return mirror_read(feed, urls, counts=counts, selected_count=selected_count)


async def selected_counts(uow: UnitOfWorkPort, feeds: Iterable[FeedRow]) -> dict[str, int]:
    """Items ever selected (wanted, archiving, archived or failed) per selection-mode Mirror."""
    counts: dict[str, int] = {}
    for feed in feeds:
        if feed.backfill_mode != BackfillMode.selection:
            continue
        total = 0
        for state in SELECTED_STATES:
            total += len(await uow.catalog.ids_in_state(feed.id, state))
        counts[feed.id] = total
    return counts


async def feed_reads(
    uow: UnitOfWorkPort, urls: PublicUrls, feeds: Sequence[FeedRow]
) -> list[FeedRead]:
    ids = [feed.id for feed in feeds]
    counts = await uow.catalog.count_by_state_many(ids)
    inbox_ids = [feed.id for feed in feeds if feed.kind == FeedKind.inbox]
    request_counts: Mapping[str, int] = (
        await uow.requests.count_for_feeds(inbox_ids) if inbox_ids else {}
    )
    selected = await selected_counts(uow, feeds)
    return [
        feed_read(
            feed,
            urls,
            counts=counts.get(feed.id, CatalogCounts()),
            request_count=request_counts.get(feed.id, 0),
            selected_count=selected.get(feed.id, 0),
        )
        for feed in feeds
    ]


async def one_feed_read(uow: UnitOfWorkPort, urls: PublicUrls, feed: FeedRow) -> FeedRead:
    return (await feed_reads(uow, urls, [feed]))[0]


def asset_read(asset: AssetRow, urls: PublicUrls) -> AssetRead:
    served = asset.state == AssetState.archived and bool(asset.local_path)
    url = urls.asset_url(asset.feed_id, asset.local_path) if served and asset.local_path else None
    return AssetRead(
        id=asset.id,
        feed_id=asset.feed_id,
        item_id=asset.item_id,
        kind=AssetKind(asset.kind),
        provenance=AssetProvenance(asset.provenance),
        language=asset.language,
        format=AssetFormat(asset.format) if asset.format else None,
        url=url,
        remote_url=asset.remote_url,
        mime=asset.mime,
        size_bytes=asset.size_bytes,
        state=AssetState(asset.state),
        last_error=asset.last_error,
        fetched_at=asset.fetched_at,
    )


def item_read(
    item: ItemRow,
    urls: PublicUrls,
    *,
    assets: Sequence[AssetRow] = (),
    request_ids: Sequence[uuid.UUID] = (),
) -> ItemRead:
    media: ItemMedia | None = None
    ext = item.media_ext
    if (
        item.archive_state == ArchiveState.archived
        and ext
        and item.media_mime
        and item.media_bytes is not None
    ):
        media = ItemMedia(
            url=urls.media_url(item.feed_id, item.id, ext),
            bytes=item.media_bytes,
            mime=item.media_mime,
            ext=ext,
        )
    return ItemRead(
        id=item.id,
        feed_id=item.feed_id,
        ordinal=item.ordinal,
        source_number=item.source_number,
        source_season=item.source_season,
        title=item.title,
        description=item.description,
        published_at=item.published_at,
        added_at=item.first_seen_at,
        duration_seconds=item.duration_seconds,
        state=ArchiveState(item.archive_state),
        listed=item.listed,
        attempt_count=item.attempt_count,
        last_error=item.last_error,
        media=media,
        artwork_url=item.artwork_url,
        assets=[asset_read(asset, urls) for asset in assets],
        download_count=item.download_count,
        first_downloaded_at=item.first_downloaded_at,
        last_downloaded_at=item.last_downloaded_at,
        request_ids=list(request_ids),
        item_url=item.source_url,
    )


async def item_reads(
    uow: UnitOfWorkPort, urls: PublicUrls, items: Sequence[ItemRow]
) -> list[ItemRead]:
    """ItemReads with assets and request ids inlined (two queries for the whole page)."""
    ids = [item.id for item in items]
    assets: Mapping[str, Sequence[AssetRow]] = await uow.assets.for_items(ids) if ids else {}
    requests: Mapping[str, Sequence[uuid.UUID]] = (
        await uow.requests.request_ids_for_items(ids) if ids else {}
    )
    return [
        item_read(item, urls, assets=assets.get(item.id, ()), request_ids=requests.get(item.id, ()))
        for item in items
    ]


def job_read(job: JobRow) -> JobRead:
    return JobRead.model_validate(job)


def api_key_read(key: ApiKeyRow) -> ApiKeyRead:
    return ApiKeyRead(
        id=key.id,
        name=key.name,
        scope=KeyScope(key.scope),
        prefix=key.prefix,
        created_at=key.created_at,
        last_used_at=key.last_used_at,
    )


async def request_read(
    uow: UnitOfWorkPort,
    urls: PublicUrls,
    request: RequestRow,
    *,
    with_items: bool = True,
) -> RequestRead:
    items: list[ItemRead] = []
    if with_items:
        ids = await uow.requests.item_ids(request.id)
        rows: Sequence[ItemRow] = await uow.catalog.get_many(ids) if ids else []
        items = await item_reads(uow, urls, rows)
    job = await uow.jobs.latest_for_request(request.id)
    return RequestRead(
        id=request.id,
        inbox_id=request.feed_id,
        url=request.url,
        requested_via=RequestedVia(request.requested_via),
        status=RequestStatus(request.status),
        item_count=request.item_count,
        error=request.error,
        items=items,
        job=job_read(job) if job is not None else None,
        created_at=request.created_at,
    )


__all__ = [
    "SELECTED_STATES",
    "api_key_read",
    "asset_read",
    "feed_health",
    "feed_read",
    "feed_reads",
    "inbox_read",
    "item_read",
    "item_reads",
    "job_read",
    "mirror_read",
    "one_feed_read",
    "request_read",
    "selected_counts",
    "selection_summary",
]
