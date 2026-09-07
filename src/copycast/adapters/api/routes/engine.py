"""``/api/engine/cookies``: the Engine's cookie file, stored for sites that need a login."""

from __future__ import annotations

from fastapi import APIRouter, Response

from copycast.adapters.api.deps import ServicesDep
from copycast.adapters.api.problems import problem_responses
from copycast.application.models import EngineCookiesRead, EngineCookiesWrite

router = APIRouter(tags=["engine"])


@router.get(
    "/engine/cookies",
    operation_id="get_engine_cookies",
    openapi_extra={"x-capability": "get_engine_cookies"},
    response_model=EngineCookiesRead,
    summary="Whether a cookie file is stored and which domains it covers (never its values)",
)
async def get_engine_cookies(services: ServicesDep) -> EngineCookiesRead:
    return await services.get_engine_cookies()


@router.put(
    "/engine/cookies",
    operation_id="set_engine_cookies",
    openapi_extra={"x-capability": "set_engine_cookies"},
    response_model=EngineCookiesRead,
    responses=problem_responses(422),
    summary="Store a Netscape cookies.txt for every future fetch",
)
async def set_engine_cookies(services: ServicesDep, body: EngineCookiesWrite) -> EngineCookiesRead:
    return await services.set_engine_cookies(body)


@router.delete(
    "/engine/cookies",
    operation_id="delete_engine_cookies",
    openapi_extra={"x-capability": "delete_engine_cookies"},
    status_code=204,
    summary="Remove the stored cookie file",
)
async def delete_engine_cookies(services: ServicesDep) -> Response:
    await services.delete_engine_cookies()
    return Response(status_code=204)


__all__ = ["router"]
