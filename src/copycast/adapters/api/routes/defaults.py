"""``/api/settings/defaults``: operator defaults every Mirror may override."""

from __future__ import annotations

from fastapi import APIRouter

from copycast.adapters.api.deps import ServicesDep
from copycast.adapters.api.problems import problem_responses
from copycast.application.models import MirrorDefaults

router = APIRouter(tags=["settings"])


@router.get(
    "/settings/defaults",
    operation_id="get_mirror_defaults",
    openapi_extra={"x-capability": "get_mirror_defaults"},
    response_model=MirrorDefaults,
    summary="Defaults applied wherever a Mirror leaves a value null",
)
async def get_mirror_defaults(services: ServicesDep) -> MirrorDefaults:
    return await services.get_mirror_defaults()


@router.put(
    "/settings/defaults",
    operation_id="set_mirror_defaults",
    openapi_extra={"x-capability": "set_mirror_defaults"},
    response_model=MirrorDefaults,
    responses=problem_responses(422),
    summary="Replace the operator defaults",
)
async def set_mirror_defaults(services: ServicesDep, body: MirrorDefaults) -> MirrorDefaults:
    return await services.set_mirror_defaults(body)


__all__ = ["router"]
