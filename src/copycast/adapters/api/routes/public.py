"""Public routes under ``/feeds``: the feed XML, media files and assets.

``GET|HEAD /feeds/{id}.xml`` serves the rendered feed from a cache keyed by
``(feed_id, revision)`` with a weak ETag, ``Last-Modified`` and RFC 9110
conditionals; a GET of a Mirror queues a ``feed_fetch`` Refresh in a
background task (the service applies the follow / Paused / cooldown rules).
``/feeds/{id}/media/{item_id}.{ext}`` is a ``FileResponse`` (Range 206/416,
strong ETag) counted as a download only on a GET without Range or with a
Range starting at byte 0. ``/feeds/{id}/assets/{basename}`` is matched
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
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from copycast.adapters.api.auth import FeedAccess
from copycast.adapters.api.cache import CachedFeed, FeedCache
from copycast.adapters.api.container import ApiContainer
from copycast.adapters.api.deps import ContainerDep, FeedCacheDep, ServicesDep
from copycast.adapters.api.problems import problem_response
from copycast.application.models import ItemRead
from copycast.application.services import Services
from copycast.domain.enums import ArchiveState, FeedKind, JobTrigger
from copycast.domain.exceptions import Conflict, Unsupported
from copycast.domain.urls import COPYCAST_ARTWORK_PATH
from copycast.logging import get_logger

router = APIRouter(tags=["public"], dependencies=[FeedAccess])
log = get_logger(__name__)

FEED_CACHE_CONTROL = "no-cache"
MEDIA_CACHE_CONTROL = "public, max-age=86400"
MEDIA_NAME_RE = re.compile(r"^(?P<item_id>[0-9a-f]{16})\.(?P<ext>[A-Za-z0-9]{1,8})$")
PLACEHOLDER_EXT = "mp3"
ON_DEMAND_WAIT_SECONDS = 120.0
ON_DEMAND_POLL_SECONDS = 2.0
ON_DEMAND_RETRY_AFTER = 30
ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


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


async def _is_file(path: Path) -> bool:
    return await asyncio.to_thread(path.is_file)


async def _safe_refresh(services: Services, feed_id: str) -> None:
    try:
        await services.request_refresh(feed_id, JobTrigger.feed_fetch)
    except Exception as exc:  # a fetch must never fail because of the background job
        log.warning("feed.fetch_refresh_failed", feed_id=feed_id, error=str(exc))


async def _safe_count(services: Services, feed_id: str, item_id: str) -> None:
    try:
        await services.record_download(feed_id, item_id)
    except Exception as exc:
        log.warning("media.count_failed", feed_id=feed_id, item_id=item_id, error=str(exc))


async def _cached_feed(
    container: ApiContainer, cache: FeedCache, feed_id: str, revision: int
) -> CachedFeed | None:
    entry = cache.get(feed_id, revision)
    if entry is not None:
        return entry
    rendered = await container.renderer.render(feed_id)
    if rendered is None:
        return None
    return cache.put(
        feed_id,
        revision=rendered.revision,
        body=rendered.body,
        last_modified=rendered.last_modified,
        content_type=rendered.content_type,
    )


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
    entry = await _cached_feed(container, cache, feed_id, head.revision)
    if entry is None:
        return problem_response(request, status=404, slug="not-found", detail="feed not found")
    background: BackgroundTask | None = None
    if request.method == "GET" and head.kind is FeedKind.mirror and head.follow and not head.paused:
        background = BackgroundTask(_safe_refresh, services, feed_id)
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
    if item.media is None or item.media.ext != ext:
        # An Automatic feed lists unarchived items under a placeholder ".mp3" URL:
        # archive on the first request, and serve whatever container the archive got.
        head = await container.renderer.head(feed_id)
        automatic = head is not None and head.automatic and ext == PLACEHOLDER_EXT
        if not automatic or not _on_demand(item):
            return problem_response(request, status=404, slug="not-found", detail="no such media")
        item = await _archive_on_demand(services, feed_id, item_id)
        if item.media is None:
            return problem_response(
                request,
                status=503,
                slug="archiving",
                detail="the episode is being archived; try again shortly",
                headers={"Retry-After": str(ON_DEMAND_RETRY_AFTER)},
            )
    path: Path = container.layout.media_path(feed_id, item_id, item.media.ext)
    if not await _is_file(path):
        return problem_response(request, status=404, slug="not-found", detail="media missing")
    background: BackgroundTask | None = None
    if counts_as_download(request.method, request.headers.get("range")):
        background = BackgroundTask(_safe_count, services, feed_id, item_id)
    return FileResponse(
        path,
        media_type=item.media.mime,
        headers={"Cache-Control": MEDIA_CACHE_CONTROL},
        content_disposition_type="inline",
        background=background,
    )


def _on_demand(item: ItemRead) -> bool:
    """Listed, so an Automatic feed offers it: expired or deleted Episodes download again."""
    return item.listed


async def _archive_on_demand(services: Services, feed_id: str, item_id: str) -> ItemRead:
    """Queue the archive (a no-op when one is running) and wait for it, up to the deadline.

    The job keeps running when the wait ends; the client is told to retry.
    """
    try:
        await services.archive_item(feed_id, item_id, trigger=JobTrigger.feed_fetch)
    except Conflict:
        pass  # archived or being archived meanwhile: the poll below settles it
    except Unsupported:
        return await services.get_item(feed_id, item_id)
    deadline = asyncio.get_running_loop().time() + ON_DEMAND_WAIT_SECONDS
    while True:
        item = await services.get_item(feed_id, item_id)
        if item.media is not None or item.state is ArchiveState.failed:
            return item
        if asyncio.get_running_loop().time() >= deadline:
            return item
        await asyncio.sleep(ON_DEMAND_POLL_SECONDS)


async def asset(request: Request, container: ContainerDep, feed_id: str, filename: str) -> Response:
    if ASSET_NAME_RE.match(filename) is None or ".." in filename:
        return problem_response(request, status=404, slug="not-found", detail="no such asset")
    async with container.uow_factory() as uow:
        row = await uow.assets.by_basename(feed_id, filename)
        local_path: str | None = row.local_path if row is not None else None
        mime: str | None = row.mime if row is not None else None
    if row is None or not local_path:
        return problem_response(request, status=404, slug="not-found", detail="no such asset")
    try:
        path: Path = container.layout.resolve(feed_id, local_path)
    except ValueError:
        return problem_response(request, status=404, slug="not-found", detail="no such asset")
    if not await _is_file(path):
        return problem_response(request, status=404, slug="not-found", detail="asset missing")
    return FileResponse(
        path,
        media_type=mime or "application/octet-stream",
        headers={"Cache-Control": MEDIA_CACHE_CONTROL},
        content_disposition_type="inline",
    )


LOGO_FILE = Path(__file__).resolve().parents[1] / "copycast-artwork.png"
LOGO_CACHE_CONTROL = "public, max-age=604800"
open_router = APIRouter(tags=["public"])
"""Routes under ``/feeds`` that take no credentials: the logo (artwork is public)."""


async def logo() -> Response:
    """The Copycast logo an Inbox feed shows as its artwork (public, like all artwork)."""
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
    summary="The Mirror or Inbox Feed (RSS)",
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
