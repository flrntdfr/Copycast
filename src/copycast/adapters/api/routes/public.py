"""Public routes under ``/feeds``: the feed XML, media files and assets.

``GET|HEAD /feeds/{id}.xml`` serves the rendered feed from a cache keyed by
``(feed_id, revision)`` with a weak ETag, ``Last-Modified`` and RFC 9110
conditionals; a GET of a Mirror first runs a light Refresh inline
(``services.light_refresh``: the Source's newest items within a short deadline
and a cooldown, never a wait on the worker) so new items are in the answer.
``/feeds/{id}/media/{item_id}.{ext}`` proxies the Episode's object from the
Object store (``ObjectResponse``: Range 206/416, the object's strong ETag) counted
as a download only on a GET without Range or with a Range starting at byte 0;
``ext`` is the one the feed advertises (fixed for the item's life), the
archived file's own, or an Automatic feed's legacy ``mp3`` placeholder. An
Ephemeral feed's unarchived item is archived on the first request, which holds
the line (no byte sent, woken by the item's ``archived`` event) for up to
``ON_DEMAND_WAIT_SECONDS`` and then serves the finished object, bit-perfect,
or answers 503 with ``Retry-After`` before a podcast app gives up (iOS drops a
silent request after 60 s). ``/feeds/{id}/assets/{basename}`` is matched
against the feed's archived asset rows and never counted. While authentication
is on, every route here takes the operator pair or the feed's own pair
(``adapters.api.auth.require_feed_access``).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from copycast.adapters.api.auth import FeedAccess
from copycast.adapters.api.cache import CachedFeed, FeedCache
from copycast.adapters.api.container import ApiContainer, FeedHeadLike
from copycast.adapters.api.deps import ContainerDep, FeedCacheDep, ServicesDep
from copycast.adapters.api.objects import ObjectResponse
from copycast.adapters.api.problems import problem_response
from copycast.application.models import ItemRead
from copycast.application.services import Services
from copycast.application.services.light_refresh import LIGHT_REFRESH_DEADLINE_SECONDS
from copycast.domain.enums import ArchiveState, FeedKind, JobTrigger
from copycast.domain.exceptions import Conflict, Unsupported
from copycast.domain.urls import COPYCAST_ARTWORK_PATH
from copycast.logging import get_logger

if TYPE_CHECKING:
    from copycast.adapters.api.events import EventHub

router = APIRouter(tags=["public"], dependencies=[FeedAccess])
log = get_logger(__name__)

FEED_CACHE_CONTROL = "no-cache"
MEDIA_CACHE_CONTROL = "public, max-age=86400"
MEDIA_NAME_RE = re.compile(r"^(?P<item_id>[0-9a-f]{16})\.(?P<ext>[A-Za-z0-9]{1,8})$")
LEGACY_PLACEHOLDER_EXT = "mp3"
"""What an Automatic feed advertised for unarchived items before 1.3; apps still hold it."""
ON_DEMAND_WAIT_SECONDS = 50.0
"""How long a first request holds the line for its archive; under iOS's 60 s idle timeout."""
ON_DEMAND_POLL_SECONDS = 5.0
"""The re-check interval when no event arrives (the event hub reconnecting, a missed NOTIFY)."""
ON_DEMAND_RETRY_AFTER = 30
ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
# LIGHT_REFRESH_DEADLINE_SECONDS (8 s, imported from the service) is a module global here so
# tests can patch how long a fetch waits on the Source; feed_xml passes it on every call.


def http_date(value: datetime) -> str:
    return format_datetime(value.astimezone(UTC), usegmt=True)


def etag_matches(header: str | None, etag: str) -> bool:
    """Weak comparison of ``If-None-Match`` against ``etag`` (``W/`` prefixes ignored)."""
    if header is None:
        return False
    wanted = etag.removeprefix("W/")
    for candidate in header.split(","):
        candidate = candidate.strip()
        if candidate == "*" or candidate.removeprefix("W/") == wanted:
            return True
    return False


def not_modified_since(header: str | None, last_modified: datetime) -> bool:
    if header is None:
        return False
    try:
        since = parsedate_to_datetime(header)
    except (TypeError, ValueError, IndexError):
        return False
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    return last_modified.astimezone(UTC).replace(microsecond=0) <= since


def counts_as_download(method: str, range_header: str | None) -> bool:
    """Full GET or a Range starting at byte 0; HEAD and later ranges never count."""
    if method.upper() != "GET":
        return False
    if range_header is None:
        return True
    value = range_header.strip().lower()
    if not value.startswith("bytes="):
        return False
    first = value.removeprefix("bytes=").split(",", 1)[0].strip()
    return first.startswith("0-")


async def _safe_count(services: Services, feed_id: str, item_id: str) -> None:
    try:
        await services.record_download(feed_id, item_id)
    except Exception as exc:
        log.warning("media.count_failed", feed_id=feed_id, item_id=item_id, error=str(exc))


async def _cached_feed(
    container: ApiContainer, cache: FeedCache, feed_id: str, revision: int
) -> CachedFeed | None:
    return await cache.fetch(feed_id, revision, lambda: container.renderer.render(feed_id))


async def feed_xml(
    request: Request,
    container: ContainerDep,
    services: ServicesDep,
    cache: FeedCacheDep,
    feed_id: str,
) -> Response:
    head = await container.renderer.head(feed_id)
    if head is None:
        return problem_response(request, status=404, slug="not-found", detail="feed not found")
    # A light Refresh: a client's fetch checks the Source's newest items inline (RSS: one
    # conditional GET; a channel: its first page; a playlist: the full Refresh is queued
    # instead) so new items are in the answer; the cooldown keeps crawlers from hammering
    # the Source, the deadline keeps the feed served whatever the Source does.
    if request.method == "GET" and head.kind is FeedKind.mirror and not head.paused:
        await services.light_refresh(feed_id, deadline_seconds=LIGHT_REFRESH_DEADLINE_SECONDS)
        head = await container.renderer.head(feed_id) or head
    entry = await _cached_feed(container, cache, feed_id, head.revision)
    if entry is None:
        return problem_response(request, status=404, slug="not-found", detail="feed not found")
    background: BackgroundTask | None = None
    headers = {
        "ETag": entry.etag,
        "Last-Modified": http_date(entry.last_modified),
        "Cache-Control": FEED_CACHE_CONTROL,
    }
    if_none_match = request.headers.get("if-none-match")
    if etag_matches(if_none_match, entry.etag) or (
        if_none_match is None
        and not_modified_since(request.headers.get("if-modified-since"), entry.last_modified)
    ):
        return Response(status_code=304, headers=headers, background=background)
    body = b"" if request.method == "HEAD" else entry.body
    headers["Content-Length"] = str(len(entry.body))
    return Response(
        content=body,
        media_type=entry.content_type,
        headers=headers,
        background=background,
    )


async def media(
    request: Request,
    container: ContainerDep,
    services: ServicesDep,
    feed_id: str,
    filename: str,
) -> Response:
    match = MEDIA_NAME_RE.match(filename)
    if match is None:
        return problem_response(request, status=404, slug="not-found", detail="no such media")
    item_id, ext = match.group("item_id"), match.group("ext")
    item = await services.get_item(feed_id, item_id)
    head: FeedHeadLike | None = None
    if ext != _public_ext(item) and (item.media is None or ext != item.media.ext):
        # Neither the URL the feed advertises nor the archived file's own name: only the
        # ".mp3" placeholder an Automatic feed advertised before 1.3 is still honoured.
        head = await container.renderer.head(feed_id)
        if head is None or not head.automatic or ext != LEGACY_PLACEHOLDER_EXT:
            return problem_response(request, status=404, slug="not-found", detail="no such media")
    if item.media is None:
        # An Automatic feed lists unarchived items under their stable URL: archive on the
        # first request, and serve whatever container the archive got.
        head = head or await container.renderer.head(feed_id)
        if head is None or not head.automatic or not _on_demand(item):
            return problem_response(request, status=404, slug="not-found", detail="no such media")
        hub: EventHub | None = getattr(request.app.state, "event_hub", None)
        item = await _archive_on_demand(services, hub, feed_id, item_id)
        if item.media is None:
            return problem_response(
                request,
                status=503,
                slug="archiving",
                detail="the episode is being archived; try again shortly",
                headers={"Retry-After": str(ON_DEMAND_RETRY_AFTER)},
            )
    layout = container.layout
    store = layout.store
    obj = await store.head_async(layout.media_key(feed_id, item_id, item.media.ext))
    if obj is None:
        return problem_response(request, status=404, slug="not-found", detail="media missing")
    background: BackgroundTask | None = None
    if counts_as_download(request.method, request.headers.get("range")):
        background = BackgroundTask(_safe_count, services, feed_id, item_id)
    return ObjectResponse(
        store,
        obj,
        media_type=item.media.mime,
        headers={"Cache-Control": MEDIA_CACHE_CONTROL},
        background=background,
    )


def _public_ext(item: ItemRead) -> str:
    """The extension of the URL the feed advertises for ``item`` (``public_media_url``)."""
    return item.public_media_url.rsplit(".", 1)[-1]


def _on_demand(item: ItemRead) -> bool:
    """Listed, so an Automatic feed offers it: expired or deleted Episodes download again."""
    return item.listed


async def _archive_on_demand(
    services: Services, hub: EventHub | None, feed_id: str, item_id: str
) -> ItemRead:
    """Queue the archive (a no-op when one is running) and hold the line, up to the deadline.

    The job runs on the worker's on-demand slot (never behind a Backfill); the wait
    wakes on the feed's events (the worker's NOTIFY) and re-checks every
    ``ON_DEMAND_POLL_SECONDS`` in case one is missed. The job keeps running when the
    wait ends; the client is told to retry.
    """
    subscription = hub.subscribe(feed_id=feed_id) if hub is not None else None
    try:
        try:
            await services.archive_item(feed_id, item_id, trigger=JobTrigger.feed_fetch)
        except Conflict:
            pass  # archived or being archived meanwhile: the wait below settles it
        except Unsupported:
            return await services.get_item(feed_id, item_id)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + ON_DEMAND_WAIT_SECONDS
        while True:
            item = await services.get_item(feed_id, item_id)
            if item.media is not None or item.state is ArchiveState.failed:
                return item
            remaining = deadline - loop.time()
            if remaining <= 0:
                return item
            await _next_change(subscription, item_id, min(remaining, ON_DEMAND_POLL_SECONDS))
    finally:
        if hub is not None and subscription is not None:
            hub.unsubscribe(subscription)


async def _next_change(subscription: Any, item_id: str, within: float) -> None:
    """Return once ``item_id`` changed (or anything resynced), or after ``within`` seconds."""
    if subscription is None:
        await asyncio.sleep(within)
        return
    loop = asyncio.get_running_loop()
    until = loop.time() + within
    while (left := until - loop.time()) > 0:
        envelope = await subscription.next(left)
        if envelope is None:
            if subscription.closed:
                await asyncio.sleep(max(0.0, until - loop.time()))
            return
        if envelope.name == "resync" or getattr(envelope.event, "item_id", None) == item_id:
            return


async def asset(request: Request, container: ContainerDep, feed_id: str, filename: str) -> Response:
    if ASSET_NAME_RE.match(filename) is None or ".." in filename:
        return problem_response(request, status=404, slug="not-found", detail="no such asset")
    async with container.uow_factory() as uow:
        row = await uow.assets.by_basename(feed_id, filename)
        local_path: str | None = row.local_path if row is not None else None
        mime: str | None = row.mime if row is not None else None
    if row is None or not local_path:
        return problem_response(request, status=404, slug="not-found", detail="no such asset")
    layout = container.layout
    try:
        key: str = layout.resolve(feed_id, local_path)
    except ValueError:
        return problem_response(request, status=404, slug="not-found", detail="no such asset")
    obj = await layout.store.head_async(key)
    if obj is None:
        return problem_response(request, status=404, slug="not-found", detail="asset missing")
    return ObjectResponse(
        layout.store,
        obj,
        media_type=mime or "application/octet-stream",
        headers={"Cache-Control": MEDIA_CACHE_CONTROL},
    )


LOGO_FILE = Path(__file__).resolve().parents[1] / "copycast-artwork.png"
LOGO_CACHE_CONTROL = "public, max-age=604800"
open_router = APIRouter(tags=["public"])
"""Routes under ``/feeds`` that take no credentials: the logo (artwork is public)."""


async def logo() -> Response:
    """The Copycast logo a Bucket feed shows as its artwork (public, like all artwork)."""
    return FileResponse(
        LOGO_FILE, media_type="image/png", headers={"Cache-Control": LOGO_CACHE_CONTROL}
    )


open_router.add_api_route(
    COPYCAST_ARTWORK_PATH.removeprefix("/feeds"),
    logo,
    methods=["GET", "HEAD"],
    response_class=Response,
    include_in_schema=False,
)


def _get_and_head(path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
    """One GET operation in the schema plus an explicit HEAD kept out of it."""
    router.add_api_route(path, endpoint, methods=["GET"], response_class=Response, **kwargs)
    router.add_api_route(
        path, endpoint, methods=["HEAD"], response_class=Response, include_in_schema=False
    )


_get_and_head(
    "/{feed_id}.xml",
    feed_xml,
    operation_id="public_feed",
    summary="The Mirror or Bucket Feed (RSS)",
    responses={200: {"content": {"application/rss+xml": {"schema": {"type": "string"}}}}},
)
_get_and_head(
    "/{feed_id}/media/{filename}",
    media,
    operation_id="public_media",
    summary="An archived Episode's audio (Range supported)",
)
_get_and_head(
    "/{feed_id}/assets/{filename}",
    asset,
    operation_id="public_asset",
    summary="A mirrored asset (artwork, chapters, transcript)",
)


__all__ = [
    "FEED_CACHE_CONTROL",
    "MEDIA_CACHE_CONTROL",
    "counts_as_download",
    "etag_matches",
    "http_date",
    "not_modified_since",
    "router",
]
