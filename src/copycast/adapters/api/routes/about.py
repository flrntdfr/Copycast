"""``GET /api/about`` (versions, engine, layout, totals) and ``GET /api/status``."""

from __future__ import annotations

from fastapi import APIRouter

from copycast.adapters.api.deps import ServicesDep
from copycast.application.models import AboutRead, StatusRead

router = APIRouter(tags=["about"])


@router.get(
    "/about",
    operation_id="about",
    openapi_extra={"x-capability": "about"},
    response_model=AboutRead,
    summary="Application and engine versions, layout version, totals",
)
async def about(services: ServicesDep) -> AboutRead:
    return await services.about()


@router.get(
    "/status",
    operation_id="get_status",
    openapi_extra={"x-capability": "get_status"},
    response_model=StatusRead,
    summary="Status of every component: database, worker, storage, yt-dlp, crawlers",
)
async def get_status(services: ServicesDep) -> StatusRead:
    return await services.get_status()


__all__ = ["router"]
