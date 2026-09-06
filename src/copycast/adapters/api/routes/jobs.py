"""Jobs: list, get, cancel."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from copycast.adapters.api.deps import ServicesDep
from copycast.adapters.api.problems import problem_responses
from copycast.application.models import JobPage, JobRead
from copycast.domain.enums import JobKind, JobStatus

router = APIRouter(tags=["jobs"])

MAX_LIMIT = 500


@router.get(
    "/jobs",
    operation_id="list_jobs",
    openapi_extra={"x-capability": "list_jobs"},
    response_model=JobPage,
    summary="Page through jobs, newest first",
)
async def list_jobs(
    services: ServicesDep,
    feed_id: Annotated[str | None, Query()] = None,
    kind: Annotated[JobKind | None, Query()] = None,
    status: Annotated[list[JobStatus] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobPage:
    return await services.list_jobs(
        feed_id=feed_id, kind=kind, status=status or None, limit=limit, offset=offset
    )


@router.get(
    "/jobs/{job_id}",
    operation_id="get_job",
    openapi_extra={"x-capability": "get_job"},
    response_model=JobRead,
    responses=problem_responses(404),
    summary="One job",
)
async def get_job(services: ServicesDep, job_id: uuid.UUID) -> JobRead:
    return await services.get_job(job_id)


@router.post(
    "/jobs/{job_id}/cancel",
    operation_id="cancel_job",
    openapi_extra={"x-capability": "cancel_job"},
    response_model=JobRead,
    responses=problem_responses(404),
    summary="Cancel a queued job or ask a running one to stop",
)
async def cancel_job(services: ServicesDep, job_id: uuid.UUID) -> JobRead:
    return await services.cancel_job(job_id)


__all__ = ["MAX_LIMIT", "router"]
