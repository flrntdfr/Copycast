"""Feeds: list (plus the ``/mirrors`` and ``/inboxes`` aliases), get, rotate credentials, delete."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response

from copycast.adapters.api.deps import RebuildGuard, ServicesDep
from copycast.adapters.api.problems import problem_responses
from copycast.application.models import FeedList, FeedRead, SelectionResult
from copycast.domain.enums import FeedKind, JobTrigger

router = APIRouter(tags=["feeds"])

FeedSort = Literal["title", "created_at", "updated_at", "storage_bytes"]
Order = Literal["asc", "desc"]


@router.get(
    "/feeds",
    operation_id="list_feeds",
    openapi_extra={"x-capability": "list_feeds"},
    response_model=FeedList,
    summary="List Feeds (Mirrors and Inboxes)",
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
    "/inboxes",
    operation_id="list_inboxes",
    response_model=FeedList,
    summary="List Inboxes (alias of list_feeds with kind=inbox)",
)
async def list_inboxes(
    services: ServicesDep,
    sort: Annotated[FeedSort, Query()] = "title",
    order: Annotated[Order, Query()] = "asc",
) -> FeedList:
    return await services.list_feeds(FeedKind.inbox, sort=sort, order=order)


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
    summary="Delete a Feed and its files (the default Inbox is refused)",
)
async def delete_feed(services: ServicesDep, feed_id: str) -> Response:
    await services.delete_feed(feed_id)
    return Response(status_code=204)


__all__ = ["router"]
