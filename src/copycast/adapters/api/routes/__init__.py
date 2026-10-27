"""``/api`` routers, one module per area; every route names its capability."""

from __future__ import annotations

from fastapi import APIRouter

from copycast.adapters.api.routes import (
    about,
    admin,
    buckets,
    defaults,
    engine,
    events,
    feeds,
    items,
    jobs,
    keys,
    mirrors,
    sources,
    uploads,
)


def create_api_router() -> APIRouter:
    router = APIRouter()
    for module in (
        feeds,
        items,
        sources,
        mirrors,
        buckets,
        jobs,
        events,
        about,
        admin,
        keys,
        engine,
        defaults,
        uploads,
    ):
        router.include_router(module.router)
    return router


__all__ = ["create_api_router"]
