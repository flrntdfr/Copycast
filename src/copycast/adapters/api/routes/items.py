"""Catalog items of one feed: list, get, archive on demand, delete (tombstone)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response

from copycast.adapters.api.deps import RebuildGuard, ServicesDep
from copycast.adapters.api.problems import problem_responses
from copycast.application.models import ItemPage, ItemRead, JobRead
from copycast.domain.enums import ArchiveState, JobTrigger

router = APIRouter(tags=["items"])

ItemSort = Literal["published", "ordinal", "title", "added"]
Order = Literal["asc", "desc"]
MAX_LIMIT = 500


@router.get(
    "/feeds/{feed_id}/items",
    operation_id="list_items",
    openapi_extra={"x-capability": "list_items"},
    response_model=ItemPage,
    responses=problem_responses(404),
    summary="Page through a feed's Catalog",
)
async def list_items(
    services: ServicesDep,
    feed_id: str,
    state: Annotated[list[ArchiveState] | None, Query()] = None,
    listed: Annotated[bool | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sort: Annotated[ItemSort, Query()] = "published",
    order: Annotated[Order, Query()] = "desc",
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ItemPage:
    return await services.list_items(
        feed_id,
        state=state or None,
        listed=listed,
        q=q,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/feeds/{feed_id}/items/{item_id}",
    operation_id="get_item",
    openapi_extra={"x-capability": "get_item"},
    response_model=ItemRead,
    responses=problem_responses(404),
    summary="One Catalog item with its assets",
)
async def get_item(services: ServicesDep, feed_id: str, item_id: str) -> ItemRead:
    return await services.get_item(feed_id, item_id)


@router.post(
    "/feeds/{feed_id}/items/{item_id}/archive",
    operation_id="archive_item",
    openapi_extra={"x-capability": "archive_item"},
    response_model=JobRead,
    status_code=202,
    dependencies=[RebuildGuard],
    responses=problem_responses(404, 409, 422),
    summary="Archive one Available item now",
)
async def archive_item(services: ServicesDep, feed_id: str, item_id: str) -> JobRead:
    return await services.archive_item(feed_id, item_id, trigger=JobTrigger.manual)


@router.post(
    "/feeds/{feed_id}/items/{item_id}/metadata",
    operation_id="fetch_item_metadata",
    openapi_extra={"x-capability": "fetch_item_metadata"},
    response_model=ItemRead,
    dependencies=[RebuildGuard],
    responses=problem_responses(404, 422),
    summary="Ask the Source for the item's description, exact date and artwork now",
)
async def fetch_item_metadata(services: ServicesDep, feed_id: str, item_id: str) -> ItemRead:
    return await services.fetch_item_metadata(feed_id, item_id)


@router.delete(
    "/feeds/{feed_id}/items/{item_id}",
    operation_id="delete_item",
    openapi_extra={"x-capability": "delete_item"},
    status_code=204,
    dependencies=[RebuildGuard],
    responses=problem_responses(404, 409),
    summary="Delete an Episode's media and leave a tombstone",
)
async def delete_item(services: ServicesDep, feed_id: str, item_id: str) -> Response:
    await services.delete_item(feed_id, item_id)
    return Response(status_code=204)


__all__ = ["MAX_LIMIT", "router"]
