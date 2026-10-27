"""``/api/engine/cookies``: the Engine's cookie files, stored for sites that need a login."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response

from copycast.adapters.api.deps import ServicesDep
from copycast.adapters.api.problems import problem_responses
from copycast.application.models import EngineCookiesRead, EngineCookiesWrite

router = APIRouter(tags=["engine"])


@router.get(
    "/engine/cookies",
    operation_id="get_engine_cookies",
    openapi_extra={"x-capability": "get_engine_cookies"},
    response_model=EngineCookiesRead,
    summary="The stored cookie files and the services they unlock (never their values)",
)
async def get_engine_cookies(services: ServicesDep) -> EngineCookiesRead:
    return await services.get_engine_cookies()


@router.put(
    "/engine/cookies",
    operation_id="set_engine_cookies",
    openapi_extra={"x-capability": "set_engine_cookies"},
    response_model=EngineCookiesRead,
    responses=problem_responses(422),
    summary="Store a Netscape cookies.txt under its name for every future fetch",
)
async def set_engine_cookies(services: ServicesDep, body: EngineCookiesWrite) -> EngineCookiesRead:
    return await services.set_engine_cookies(body)


@router.delete(
    "/engine/cookies",
    operation_id="delete_engine_cookies",
    openapi_extra={"x-capability": "delete_engine_cookies"},
    status_code=204,
    responses=problem_responses(404),
    summary="Remove one stored cookie file, or every one without name",
)
async def delete_engine_cookies(
    services: ServicesDep,
    name: Annotated[str | None, Query(max_length=128, description="The file to remove")] = None,
) -> Response:
    await services.delete_engine_cookies(name)
    return Response(status_code=204)


__all__ = ["router"]
