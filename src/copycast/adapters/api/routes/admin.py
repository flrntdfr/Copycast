"""``POST /api/admin/rebuild``: queue the worker's rebuild job (409 while one is active)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body

from copycast.adapters.api.deps import ServicesDep
from copycast.adapters.api.problems import problem_responses
from copycast.application.models import JobRead, RebuildRequest

router = APIRouter(tags=["admin"])

DEFAULT_REBUILD = RebuildRequest()
"""An empty body means the default request (``dry_run=true``)."""


@router.post(
    "/admin/rebuild",
    operation_id="rebuild",
    openapi_extra={"x-capability": "rebuild"},
    response_model=JobRead,
    status_code=202,
    responses=problem_responses(409),
    summary="Reconstruct the database from the data directory (worker job under WORKER_LOCK)",
)
async def rebuild(
    services: ServicesDep, body: Annotated[RebuildRequest, Body()] = DEFAULT_REBUILD
) -> JobRead:
    return await services.rebuild(body)


__all__ = ["router"]
