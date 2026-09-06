"""Route dependencies: the container, the services, the rebuild guard."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request

from copycast.adapters.api.cache import FeedCache
from copycast.adapters.api.container import ApiContainer
from copycast.application.ports import CancelToken
from copycast.application.services import Services
from copycast.application.services.jobs import REBUILD_DEDUP_KEY
from copycast.domain.exceptions import Conflict

DISCONNECT_POLL_SECONDS = 0.5


def get_container(request: Request) -> ApiContainer:
    container: ApiContainer = request.app.state.container
    return container


def get_services(container: Annotated[ApiContainer, Depends(get_container)]) -> Services:
    return container.services


def get_feed_cache(request: Request) -> FeedCache:
    cache: FeedCache = request.app.state.feed_cache
    return cache


async def refuse_during_rebuild(
    container: Annotated[ApiContainer, Depends(get_container)],
) -> None:
    """Mutations answer 409 while a rebuild job is queued or running."""
    async with container.uow_factory() as uow:
        active = await uow.jobs.active_by_dedup_key(REBUILD_DEDUP_KEY)
    if active is not None:
        raise Conflict("a rebuild is in progress; retry once it has finished")


async def run_cancellable[T](
    request: Request, operation: Callable[[CancelToken], Awaitable[T]]
) -> T:
    """Run ``operation`` and cancel its token (and task) when the client disconnects."""
    token = CancelToken()
    task = asyncio.ensure_future(operation(token))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=DISCONNECT_POLL_SECONDS)
            if done:
                return task.result()
            if await request.is_disconnected():
                token.cancel()
                task.cancel()
                raise asyncio.CancelledError("client disconnected")
    except asyncio.CancelledError:
        token.cancel()
        if not task.done():
            task.cancel()
        raise


ContainerDep = Annotated[ApiContainer, Depends(get_container)]
FeedCacheDep = Annotated[FeedCache, Depends(get_feed_cache)]
ServicesDep = Annotated[Services, Depends(get_services)]
RebuildGuard = Depends(refuse_during_rebuild)

__all__ = [
    "ContainerDep",
    "FeedCacheDep",
    "RebuildGuard",
    "ServicesDep",
    "get_container",
    "get_feed_cache",
    "get_services",
    "refuse_during_rebuild",
    "run_cancellable",
]
