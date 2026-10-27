"""Uploads and Serial Inboxes (ADR 0015).

``POST /api/uploads`` stages a file and returns its one-time ``upload_url``;
``PUT /uploads/{id}/{token}`` (outside ``/api``: the token is the credential,
so an agent holding only an API key can ``curl -T`` the file) receives the
bytes; ``POST /api/serial-inboxes`` turns received uploads into an Inbox in
the given order.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from copycast.adapters.api.deps import RebuildGuard, ServicesDep
from copycast.adapters.api.problems import problem_responses
from copycast.application.models import InboxRead, SerialInboxCreate, UploadCreate, UploadRead

router = APIRouter(tags=["uploads"])
open_router = APIRouter(tags=["uploads"])


@router.post(
    "/uploads",
    operation_id="create_upload",
    openapi_extra={"x-capability": "create_upload"},
    response_model=UploadRead,
    status_code=201,
    dependencies=[RebuildGuard],
    responses=problem_responses(422),
    summary="Stage an audio file; PUT its bytes to the returned upload_url",
)
async def create_upload(services: ServicesDep, body: UploadCreate) -> UploadRead:
    return await services.create_upload(body)


@router.get(
    "/uploads/{upload_id}",
    operation_id="get_upload",
    openapi_extra={"x-capability": "get_upload"},
    response_model=UploadRead,
    responses=problem_responses(404),
    summary="A staged upload and the tags read from it",
)
async def get_upload(services: ServicesDep, upload_id: str) -> UploadRead:
    return await services.get_upload(upload_id)


@router.post(
    "/serial-inboxes",
    operation_id="create_serial_inbox",
    openapi_extra={"x-capability": "create_serial_inbox"},
    response_model=InboxRead,
    status_code=201,
    dependencies=[RebuildGuard],
    responses=problem_responses(404, 409, 422),
    summary="Create an Inbox from uploads, Episodes in the given order",
)
async def create_serial_inbox(
    services: ServicesDep, request: Request, response: Response, body: SerialInboxCreate
) -> InboxRead:
    inbox = await services.create_serial_inbox(body)
    response.headers["Location"] = str(request.url_for("get_feed", feed_id=inbox.id))
    return inbox


@open_router.put(
    "/{upload_id}/{token}",
    operation_id="put_upload",
    openapi_extra={"x-capability": "put_upload"},
    response_model=UploadRead,
    dependencies=[RebuildGuard],
    responses=problem_responses(404, 409, 422),
    summary="The bytes of a staged upload (the URL is the credential)",
)
async def put_upload(
    services: ServicesDep, request: Request, upload_id: str, token: str
) -> UploadRead:
    return await services.put_upload(upload_id, token, request.stream())


__all__ = ["open_router", "router"]
