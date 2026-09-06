"""``/api`` routers, one module per area; every route names its capability."""

from __future__ import annotations

from fastapi import APIRouter

from copycast.adapters.api.routes import (
    about,
    admin,
    events,
    feeds,
    inboxes,
    items,
    jobs,
    keys,
    mirrors,
    sources,
)


def create_api_router() -> APIRouter:
    router = APIRouter()
    for module in (feeds, items, sources, mirrors, inboxes, jobs, events, about, admin, keys):
        router.include_router(module.router)
    return router


__all__ = ["create_api_router"]
