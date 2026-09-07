"""Mirrors: create, update (retarget allowed), pause/resume, refresh, selections."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from copycast.adapters.api.deps import RebuildGuard, ServicesDep, run_cancellable
from copycast.adapters.api.problems import problem_responses
from copycast.application.models import (
    JobRead,
    MirrorChangePreview,
    MirrorCreate,
    MirrorRead,
    MirrorUpdate,
    SelectionRequest,
    SelectionResult,
)
from copycast.application.ports import CancelToken
from copycast.domain.enums import JobTrigger

router = APIRouter(tags=["mirrors"])


@router.post(
    "/mirrors",
    operation_id="create_mirror",
    openapi_extra={"x-capability": "create_mirror"},
    response_model=MirrorRead,
    status_code=201,
    dependencies=[RebuildGuard],
    responses=problem_responses(409, 422),
    summary="Create a Mirror; the Catalog is populated before returning",
)
async def create_mirror(
    services: ServicesDep, request: Request, response: Response, body: MirrorCreate
) -> MirrorRead:
    async def _create(cancel: CancelToken) -> MirrorRead:
        return await services.create_mirror(body, trigger=JobTrigger.ui, cancel=cancel)

    mirror = await run_cancellable(request, _create)
    response.headers["Location"] = str(request.url_for("get_feed", feed_id=mirror.id))
    return mirror


@router.patch(
    "/mirrors/{feed_id}",
    operation_id="update_mirror",
    openapi_extra={"x-capability": "update_mirror"},
    response_model=MirrorRead,
    dependencies=[RebuildGuard],
    responses=problem_responses(404, 409, 422),
    summary="Change policy, engine options or the Source URL (kind change refused)",
)
async def update_mirror(
    services: ServicesDep, request: Request, feed_id: str, body: MirrorUpdate
) -> MirrorRead:
    async def _update(cancel: CancelToken) -> MirrorRead:
        return await services.update_mirror(feed_id, body, cancel=cancel)

    return await run_cancellable(request, _update)


@router.post(
    "/mirrors/{feed_id}/pause",
    operation_id="set_paused",
    openapi_extra={"x-capability": "set_paused"},
    response_model=MirrorRead,
    dependencies=[RebuildGuard],
    responses=problem_responses(404),
    summary="Pause a Mirror (it keeps serving; no automatic Refresh)",
)
async def pause_mirror(services: ServicesDep, feed_id: str) -> MirrorRead:
    return await services.set_paused(feed_id, True)


@router.post(
    "/mirrors/{feed_id}/resume",
    operation_id="resume_mirror",
    response_model=MirrorRead,
    dependencies=[RebuildGuard],
    responses=problem_responses(404),
    summary="Resume a Paused Mirror (set_paused with paused=false)",
)
async def resume_mirror(services: ServicesDep, feed_id: str) -> MirrorRead:
    return await services.set_paused(feed_id, False)


@router.post(
    "/mirrors/{feed_id}/refresh",
    operation_id="request_refresh",
    openapi_extra={"x-capability": "request_refresh"},
    response_model=JobRead,
    status_code=202,
    dependencies=[RebuildGuard],
    responses=problem_responses(404),
    summary="Queue a Refresh now (ignores Paused and the cooldown)",
)
async def request_refresh(services: ServicesDep, feed_id: str) -> JobRead | None:
    return await services.request_refresh(feed_id, JobTrigger.manual)


@router.post(
    "/mirrors/{feed_id}/preview",
    operation_id="preview_mirror_update",
    openapi_extra={"x-capability": "preview_mirror_update"},
    response_model=MirrorChangePreview,
    responses=problem_responses(404, 422),
    summary="What an update would delete or archive, without applying it",
)
async def preview_mirror_update(
    services: ServicesDep, feed_id: str, body: MirrorUpdate
) -> MirrorChangePreview:
    return await services.preview_mirror_update(feed_id, body)


@router.post(
    "/mirrors/{feed_id}/selections",
    operation_id="select_items",
    openapi_extra={"x-capability": "select_items"},
    response_model=SelectionResult,
    status_code=202,
    dependencies=[RebuildGuard],
    responses={200: {"model": SelectionResult}, **problem_responses(404, 422)},
    summary='Archive a selection ("1-42, 180"); 200 with dry_run, 202 when queued',
)
async def select_items(services: ServicesDep, feed_id: str, body: SelectionRequest) -> JSONResponse:
    result = await services.select_items(feed_id, body, trigger=JobTrigger.ui)
    status = 200 if result.dry_run else 202
    return JSONResponse(result.model_dump(mode="json"), status_code=status)


__all__ = ["router"]
