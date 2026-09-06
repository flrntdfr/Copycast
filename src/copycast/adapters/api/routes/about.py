"""``GET /api/about``: versions, engine, layout and totals."""

from __future__ import annotations

from fastapi import APIRouter

from copycast.adapters.api.deps import ServicesDep
from copycast.application.models import AboutRead

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


__all__ = ["router"]
