"""``POST /api/admin/rebuild``: queue the worker's rebuild job (409 while one is active)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body

from copycast.adapters.api.deps import ServicesDep
from copycast.adapters.api.problems import problem_responses
from copycast.application.models import JobRead, PruneResult, PurgeRequest, RebuildRequest

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


DEFAULT_PURGE = PurgeRequest()
"""An empty body means a dry run: nothing is deleted without ``dry_run: false``."""


@router.post(
    "/admin/purge",
    operation_id="purge_episodes",
    openapi_extra={"x-capability": "purge_episodes"},
    response_model=PruneResult,
    summary="Delete every archived Episode everywhere; feeds stay; dry run by default",
)
async def purge_episodes(
    services: ServicesDep, body: Annotated[PurgeRequest, Body()] = DEFAULT_PURGE
) -> PruneResult:
    return await services.purge_episodes(body)


__all__ = ["router"]
