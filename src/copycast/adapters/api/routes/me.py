"""``GET /api/me``: the signed-in User, their role, auth mode and quota."""

from __future__ import annotations

from fastapi import APIRouter

from copycast.adapters.api.deps import ServicesDep
from copycast.adapters.api.problems import problem_responses
from copycast.application.models import MeRead

router = APIRouter(tags=["users"])


@router.get(
    "/me",
    operation_id="get_me",
    openapi_extra={"x-capability": "get_me"},
    response_model=MeRead,
    responses=problem_responses(401),
    summary="The signed-in User (session only); re-issues the session cookie",
)
async def get_me(services: ServicesDep) -> MeRead:
    return await services.get_me()


__all__ = ["router"]
