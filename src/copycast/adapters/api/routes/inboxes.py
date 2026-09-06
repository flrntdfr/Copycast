"""Inboxes: create, update, Requests, synchronous prune."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from copycast.adapters.api.deps import RebuildGuard, ServicesDep
from copycast.adapters.api.problems import problem_responses
from copycast.application.models import (
    InboxCreate,
    InboxRead,
    InboxUpdate,
    PruneRequest,
    PruneResult,
    RequestCreate,
    RequestPage,
    RequestRead,
)
from copycast.domain.enums import RequestedVia

router = APIRouter(tags=["inboxes"])

MAX_LIMIT = 500


@router.post(
    "/inboxes",
    operation_id="create_inbox",
    openapi_extra={"x-capability": "create_inbox"},
    response_model=InboxRead,
    status_code=201,
    dependencies=[RebuildGuard],
    responses=problem_responses(409, 422),
    summary="Create an Inbox",
)
async def create_inbox(
    services: ServicesDep, request: Request, response: Response, body: InboxCreate
) -> InboxRead:
    inbox = await services.create_inbox(body)
    response.headers["Location"] = str(request.url_for("get_feed", feed_id=inbox.id))
    return inbox


@router.patch(
    "/inboxes/{inbox_id}",
    operation_id="update_inbox",
    openapi_extra={"x-capability": "update_inbox"},
    response_model=InboxRead,
    dependencies=[RebuildGuard],
    responses=problem_responses(404, 409, 422),
    summary="Rename an Inbox or change its autoprune",
)
async def update_inbox(services: ServicesDep, inbox_id: str, body: InboxUpdate) -> InboxRead:
    return await services.update_inbox(inbox_id, body)


@router.post(
    "/inboxes/{inbox_id}/requests",
    operation_id="add_request",
    openapi_extra={"x-capability": "add_request"},
    response_model=RequestRead,
    status_code=202,
    dependencies=[RebuildGuard],
    responses=problem_responses(404, 422),
    summary="Push a URL into an Inbox (expanded by the worker)",
)
async def add_request(services: ServicesDep, inbox_id: str, body: RequestCreate) -> RequestRead:
    return await services.add_request(inbox_id, body, via=RequestedVia.ui)


@router.get(
    "/inboxes/{inbox_id}/requests",
    operation_id="list_requests",
    openapi_extra={"x-capability": "list_requests"},
    response_model=RequestPage,
    responses=problem_responses(404),
    summary="Requests of an Inbox, newest first",
)
async def list_requests(
    services: ServicesDep,
    inbox_id: str,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RequestPage:
    return await services.list_requests(inbox_id, limit=limit, offset=offset)


@router.get(
    "/inboxes/{inbox_id}/requests/{request_id}",
    operation_id="get_request",
    openapi_extra={"x-capability": "get_request"},
    response_model=RequestRead,
    responses=problem_responses(404),
    summary="One Request with the items it produced",
)
async def get_request(services: ServicesDep, inbox_id: str, request_id: uuid.UUID) -> RequestRead:
    return await services.get_request(inbox_id, request_id)


@router.post(
    "/inboxes/{inbox_id}/prune",
    operation_id="prune_inbox",
    openapi_extra={"x-capability": "prune_inbox"},
    response_model=PruneResult,
    dependencies=[RebuildGuard],
    responses=problem_responses(404, 422),
    summary="Delete Inbox Episodes matching every given criterion (synchronous)",
)
async def prune_inbox(services: ServicesDep, inbox_id: str, body: PruneRequest) -> PruneResult:
    return await services.prune_inbox(inbox_id, body)


__all__ = ["MAX_LIMIT", "router"]
