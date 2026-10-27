"""Buckets: create, update, Requests, synchronous prune."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from copycast.adapters.api.deps import RebuildGuard, ServicesDep
from copycast.adapters.api.problems import problem_responses
from copycast.application.models import (
    BucketCreate,
    BucketRead,
    BucketUpdate,
    PruneRequest,
    PruneResult,
    RequestCreate,
    RequestPage,
    RequestRead,
)
from copycast.domain.enums import RequestedVia

router = APIRouter(tags=["buckets"])

MAX_LIMIT = 500


@router.post(
    "/buckets",
    operation_id="create_bucket",
    openapi_extra={"x-capability": "create_bucket"},
    response_model=BucketRead,
    status_code=201,
    dependencies=[RebuildGuard],
    responses=problem_responses(409, 422),
    summary="Create a Bucket",
)
async def create_bucket(
    services: ServicesDep, request: Request, response: Response, body: BucketCreate
) -> BucketRead:
    bucket = await services.create_bucket(body)
    response.headers["Location"] = str(request.url_for("get_feed", feed_id=bucket.id))
    return bucket


@router.patch(
    "/buckets/{bucket_id}",
    operation_id="update_bucket",
    openapi_extra={"x-capability": "update_bucket"},
    response_model=BucketRead,
    dependencies=[RebuildGuard],
    responses=problem_responses(404, 409, 422),
    summary="Rename a Bucket or change its autoprune",
)
async def update_bucket(services: ServicesDep, bucket_id: str, body: BucketUpdate) -> BucketRead:
    return await services.update_bucket(bucket_id, body)


@router.post(
    "/buckets/{bucket_id}/requests",
    operation_id="add_request",
    openapi_extra={"x-capability": "add_request"},
    response_model=RequestRead,
    status_code=202,
    dependencies=[RebuildGuard],
    responses=problem_responses(404, 422),
    summary="Push a URL into a Bucket (expanded by the worker)",
)
async def add_request(services: ServicesDep, bucket_id: str, body: RequestCreate) -> RequestRead:
    return await services.add_request(bucket_id, body, via=RequestedVia.ui)


@router.get(
    "/buckets/{bucket_id}/requests",
    operation_id="list_requests",
    openapi_extra={"x-capability": "list_requests"},
    response_model=RequestPage,
    responses=problem_responses(404),
    summary="Requests of a Bucket, newest first",
)
async def list_requests(
    services: ServicesDep,
    bucket_id: str,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RequestPage:
    return await services.list_requests(bucket_id, limit=limit, offset=offset)


@router.get(
    "/buckets/{bucket_id}/requests/{request_id}",
    operation_id="get_request",
    openapi_extra={"x-capability": "get_request"},
    response_model=RequestRead,
    responses=problem_responses(404),
    summary="One Request with the items it produced",
)
async def get_request(services: ServicesDep, bucket_id: str, request_id: uuid.UUID) -> RequestRead:
    return await services.get_request(bucket_id, request_id)


@router.post(
    "/buckets/{bucket_id}/prune",
    operation_id="prune_bucket",
    openapi_extra={"x-capability": "prune_bucket"},
    response_model=PruneResult,
    dependencies=[RebuildGuard],
    responses=problem_responses(404, 422),
    summary="Delete Bucket Episodes matching every given criterion (synchronous)",
)
async def prune_bucket(services: ServicesDep, bucket_id: str, body: PruneRequest) -> PruneResult:
    return await services.prune_bucket(bucket_id, body)


__all__ = ["MAX_LIMIT", "router"]
