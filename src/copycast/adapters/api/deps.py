"""Route dependencies: the container, the Principal and its services, the rebuild guard.

``ServicesDep`` acts as the request's Principal (``PrincipalMiddleware``);
``SystemServicesDep`` as SYSTEM, for the routes whose credential is checked by other
means (``/feeds`` by ``FeedAccess``, upload URLs by their token, sign-in by the
password). :func:`refuse_unfit_keys` guards every ``/api`` route: an API key reaches
only the routes of tool capabilities, and only with a scope that covers the method.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, cast

from fastapi import Depends, Request

from copycast.adapters.api.cache import FeedCache
from copycast.adapters.api.container import ApiContainer
from copycast.adapters.api.principal import principal_of
from copycast.application.capabilities import DESTRUCTIVE, tool_capabilities
from copycast.application.ports import CancelToken
from copycast.application.services import Services
from copycast.application.services.jobs import REBUILD_DEDUP_KEY
from copycast.domain.credentials import required_scope, scope_allows
from copycast.domain.enums import KeyScope, PrincipalVia
from copycast.domain.exceptions import Conflict, Forbidden, Unauthorized
from copycast.domain.users import Principal

DISCONNECT_POLL_SECONDS = 0.5


def get_container(request: Request) -> ApiContainer:
    container: ApiContainer = request.app.state.container
    return container


def get_principal(request: Request) -> Principal:
    """The request's Principal; ``Unauthorized`` when none was resolved."""
    principal = principal_of(request.scope)
    if principal is None:
        raise Unauthorized()
    return principal


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def get_services(
    request: Request, container: Annotated[ApiContainer, Depends(get_container)]
) -> Services:
    """The services acting as the request's Principal."""
    return container.services_for(get_principal(request))


def get_system_services(container: Annotated[ApiContainer, Depends(get_container)]) -> Services:
    """The services acting as SYSTEM (``container.services``)."""
    return container.services


def require_admin(principal: PrincipalDep) -> Principal:
    """``Forbidden`` unless the Principal is an Admin."""
    if not principal.admin:
        raise Forbidden("this needs an Admin")
    return principal


def route_capability(request: Request) -> str | None:
    """The ``x-capability`` of the route serving ``request``, if any."""
    route = request.scope.get("route")
    extra = getattr(route, "openapi_extra", None)
    if not isinstance(extra, dict):
        return None
    name = cast(dict[str, object], extra).get("x-capability")
    return name if isinstance(name, str) else None


def refuse_unfit_keys(request: Request) -> None:
    """An API key on ``/api`` reaches tool capabilities only, with a scope fitting the method.

    ``GET`` and ``HEAD`` need ``read``, other methods ``write``, destructive capabilities
    ``full``. Capability-less routes and the capabilities without an MCP tool (sessions,
    API keys, Cookie files, Defaults, ``get_me``) refuse keys. Sessions pass untouched.
    """
    principal = principal_of(request.scope)
    if principal is None or principal.via is not PrincipalVia.api_key:
        return
    name = route_capability(request)
    if name is None or name not in tool_capabilities():
        raise Forbidden("API keys reach the MCP tools' routes only; sign in for the rest")
    needed = required_scope(
        destructive=name in DESTRUCTIVE, read_only=request.method in {"GET", "HEAD"}
    )
    granted = principal.key_scope or KeyScope.read
    if not scope_allows(granted, needed):
        raise Forbidden(
            f"{name} needs an API key with scope '{needed.value}'; "
            f"this key has scope '{granted.value}'"
        )


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
SystemServicesDep = Annotated[Services, Depends(get_system_services)]
RebuildGuard = Depends(refuse_during_rebuild)
KeyGuard = Depends(refuse_unfit_keys)

__all__ = [
    "ContainerDep",
    "FeedCacheDep",
    "KeyGuard",
    "PrincipalDep",
    "RebuildGuard",
    "ServicesDep",
    "SystemServicesDep",
    "get_container",
    "get_feed_cache",
    "get_principal",
    "get_services",
    "get_system_services",
    "refuse_during_rebuild",
    "refuse_unfit_keys",
    "require_admin",
    "route_capability",
    "run_cancellable",
]
