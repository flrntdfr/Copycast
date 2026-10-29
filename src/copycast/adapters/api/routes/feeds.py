"""Feeds: list (plus the ``/mirrors`` and ``/buckets`` aliases), get, rotate credentials, delete,
and the storage view: the objects a Feed keeps, each downloadable, and every Feed's file count."""

from __future__ import annotations

import mimetypes
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Query, Request, Response

from copycast.adapters.api.deps import ContainerDep, RebuildGuard, ServicesDep
from copycast.adapters.api.objects import ObjectResponse
from copycast.adapters.api.problems import problem_response, problem_responses
from copycast.application.models import (
    ClearFeedRequest,
    ClearFeedResult,
    FeedList,
    FeedObjectList,
    FeedRead,
    SelectionResult,
    StorageSummary,
)
from copycast.domain.enums import FeedKind, JobTrigger

router = APIRouter(tags=["feeds"])

FeedSort = Literal["title", "created_at", "updated_at", "storage_bytes"]
Order = Literal["asc", "desc"]


@router.get(
    "/feeds",
    operation_id="list_feeds",
    openapi_extra={"x-capability": "list_feeds"},
    response_model=FeedList,
    summary="List Feeds (Mirrors and Buckets)",
)
async def list_feeds(
    services: ServicesDep,
    kind: Annotated[FeedKind | None, Query()] = None,
    sort: Annotated[FeedSort, Query()] = "title",
    order: Annotated[Order, Query()] = "asc",
) -> FeedList:
    return await services.list_feeds(kind, sort=sort, order=order)


@router.get(
    "/mirrors",
    operation_id="list_mirrors",
    response_model=FeedList,
    summary="List Mirrors (alias of list_feeds with kind=mirror)",
)
async def list_mirrors(
    services: ServicesDep,
    sort: Annotated[FeedSort, Query()] = "title",
    order: Annotated[Order, Query()] = "asc",
) -> FeedList:
    return await services.list_feeds(FeedKind.mirror, sort=sort, order=order)


@router.get(
    "/buckets",
    operation_id="list_buckets",
    response_model=FeedList,
    summary="List Buckets (alias of list_feeds with kind=bucket)",
)
async def list_buckets(
    services: ServicesDep,
    sort: Annotated[FeedSort, Query()] = "title",
    order: Annotated[Order, Query()] = "asc",
) -> FeedList:
    return await services.list_feeds(FeedKind.bucket, sort=sort, order=order)


@router.get(
    "/storage",
    operation_id="storage_summary",
    openapi_extra={"x-capability": "storage_summary"},
    response_model=StorageSummary,
    summary="How many files every Feed keeps in its bucket, and their total size",
)
async def storage_summary(services: ServicesDep) -> StorageSummary:
    return await services.storage_summary()


OPML_MEDIA_TYPE = "text/x-opml+xml; charset=utf-8"


@router.get(
    "/feeds.opml",
    operation_id="export_opml",
    openapi_extra={"x-capability": "export_opml"},
    response_class=Response,
    responses={200: {"content": {"text/x-opml+xml": {"schema": {"type": "string"}}}}},
    summary="Every feed as OPML, to subscribe to all of them at once",
)
async def export_opml(services: ServicesDep) -> Response:
    document = await services.export_opml()
    return Response(
        document,
        media_type=OPML_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="copycast.opml"'},
    )


@router.get(
    "/feeds/{feed_id}",
    operation_id="get_feed",
    openapi_extra={"x-capability": "get_feed"},
    response_model=FeedRead,
    responses=problem_responses(404),
    summary="One Feed",
)
async def get_feed(services: ServicesDep, feed_id: str) -> FeedRead:
    return await services.get_feed(feed_id)


@router.get(
    "/feeds/{feed_id}/objects",
    operation_id="list_feed_objects",
    openapi_extra={"x-capability": "list_feed_objects"},
    response_model=FeedObjectList,
    responses=problem_responses(404),
    summary="Every object a Feed keeps in the Object store, with a download URL each",
)
async def list_feed_objects(services: ServicesDep, feed_id: str) -> FeedObjectList:
    return await services.list_feed_objects(feed_id)


OBJECT_CONTENT_TYPES = {".m4a": "audio/mp4", ".vtt": "text/vtt", ".srt": "application/x-subrip"}
"""Types ``mimetypes`` does not know on every platform."""


def content_disposition(name: str) -> str:
    """``attachment`` with an ASCII ``filename`` and, for other names, RFC 5987's ``filename*``
    (what Starlette's ``FileResponse`` does); quotes, backslashes and control characters never
    reach the header."""
    ascii_name = "".join(
        ch for ch in name.encode("ascii", "ignore").decode() if ch >= " " and ch not in '"\\'
    )
    header = f'attachment; filename="{ascii_name or "download"}"'
    if quote(name) != name:
        header += f"; filename*=utf-8''{quote(name)}"
    return header


def object_media_type(name: str) -> str:
    """The content type to download ``name`` under; ``application/octet-stream`` when unknown."""
    lower = name.lower()
    for suffix, media_type in OBJECT_CONTENT_TYPES.items():
        if lower.endswith(suffix):
            return media_type
    guessed, _ = mimetypes.guess_type(name, strict=False)
    return guessed or "application/octet-stream"


@router.get(
    "/feeds/{feed_id}/objects/{path:path}",
    operation_id="download_feed_object",
    response_class=Response,
    responses={
        200: {"content": {"application/octet-stream": {"schema": {"type": "string"}}}},
        **problem_responses(404),
    },
    summary="Download one stored object of a Feed as an attachment",
)
async def download_feed_object(
    request: Request, container: ContainerDep, services: ServicesDep, feed_id: str, path: str
) -> Response:
    await services.get_feed(feed_id)
    layout = container.layout
    try:
        key: str = layout.resolve(feed_id, path)
    except ValueError:
        return problem_response(request, status=404, slug="not-found", detail="no such object")
    obj = await layout.store.head_async(key)
    if obj is None:
        return problem_response(request, status=404, slug="not-found", detail="no such object")
    return ObjectResponse(
        layout.store,
        obj,
        media_type=object_media_type(obj.name),
        headers={"Content-Disposition": content_disposition(obj.name)},
    )


@router.post(
    "/feeds/{feed_id}/credentials/rotate",
    operation_id="rotate_feed_credentials",
    openapi_extra={"x-capability": "rotate_feed_credentials"},
    response_model=FeedRead,
    dependencies=[RebuildGuard],
    responses=problem_responses(404, 409),
    summary="Mint a new Basic auth pair for a Feed (every subscribed client must be updated)",
)
async def rotate_feed_credentials(services: ServicesDep, feed_id: str) -> FeedRead:
    return await services.rotate_feed_credentials(feed_id)


@router.post(
    "/feeds/{feed_id}/archive-available",
    operation_id="archive_available",
    openapi_extra={"x-capability": "archive_available"},
    response_model=SelectionResult,
    status_code=202,
    dependencies=[RebuildGuard],
    responses=problem_responses(404),
    summary="Queue every Available item of a Feed (the policy is untouched)",
)
async def archive_available(services: ServicesDep, feed_id: str) -> SelectionResult:
    return await services.archive_available(feed_id, trigger=JobTrigger.ui)


@router.post(
    "/feeds/{feed_id}/retry-failed",
    operation_id="retry_failed",
    openapi_extra={"x-capability": "retry_failed"},
    response_model=SelectionResult,
    status_code=202,
    dependencies=[RebuildGuard],
    responses=problem_responses(404),
    summary="Queue every failed item of a Feed again",
)
async def retry_failed(services: ServicesDep, feed_id: str) -> SelectionResult:
    return await services.retry_failed(feed_id, trigger=JobTrigger.ui)


@router.delete(
    "/feeds/{feed_id}",
    operation_id="delete_feed",
    openapi_extra={"x-capability": "delete_feed"},
    status_code=204,
    dependencies=[RebuildGuard],
    responses=problem_responses(404, 409),
    summary="Delete a Feed and its files (the default Bucket is refused)",
)
async def delete_feed(services: ServicesDep, feed_id: str) -> Response:
    await services.delete_feed(feed_id)
    return Response(status_code=204)


@router.post(
    "/feeds/{feed_id}/clear",
    operation_id="clear_feed",
    openapi_extra={"x-capability": "clear_feed"},
    response_model=ClearFeedResult,
    dependencies=[RebuildGuard],
    responses=problem_responses(404),
    summary="Purge a Feed's bucket: delete every Episode not locked (dry run by default)",
)
async def clear_feed(
    services: ServicesDep, feed_id: str, body: ClearFeedRequest
) -> ClearFeedResult:
    return await services.clear_feed(feed_id, body)


__all__ = ["router"]
