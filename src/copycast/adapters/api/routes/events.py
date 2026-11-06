"""``GET /api/events``: the server-sent event stream.

``text/event-stream`` with ``retry: 3000``, a keepalive comment every 15 s,
``id:`` per-process sequence numbers of the Principal's view (an Admin's counts every
event, a Member's only their own, ``adapters.api.events``) and a 1000-event replay via
``Last-Event-ID``. Frames: ``job``, ``progress``, ``feed``, ``item``,
``request``, ``resync`` (see ``application.events``). A stream carries only the
events of the Principal's own Feeds (every Feed for an Admin) and ``resync``;
``?feed_id=`` of a Feed the Principal cannot see is 404.

The request's session is resolved again every :data:`RECHECK_SECONDS` while the stream
is open (which also slides it, like any request): once it is signed out or expired, or its
User is no longer configured, the stream ends, so it never outlives the sign-in it opened
with. (API keys cannot open a stream: the capability has no MCP tool.)
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from sse_starlette.event import ServerSentEvent
from sse_starlette.sse import EventSourceResponse

from copycast.adapters.api.container import ApiContainer
from copycast.adapters.api.deps import ContainerDep, PrincipalDep, ServicesDep
from copycast.adapters.api.events import EventHub, Subscription
from copycast.adapters.api.principal import resolve_principal
from copycast.application.events import serialize
from copycast.domain.enums import AuthMode
from copycast.domain.users import Principal
from copycast.logging import get_logger

log = get_logger(__name__)

router = APIRouter(tags=["events"])

RETRY_MS = 3000
PING_SECONDS = 15
RECHECK_SECONDS = 60.0
"""How often an open stream checks that its credentials still resolve to its Principal."""

StillValid = Callable[[], Awaitable[bool]]


def _last_event_id(header: str | None) -> int | None:
    if header is None:
        return None
    try:
        value = int(header.strip())
    except ValueError:
        return None
    return value if value >= 0 else None


async def _recheck(subscription: Subscription, still_valid: StillValid) -> None:
    """Close ``subscription`` once ``still_valid`` says no (checked every
    :data:`RECHECK_SECONDS`); a failed check (database down) keeps the stream."""
    while not subscription.closed:
        await asyncio.sleep(RECHECK_SECONDS)
        try:
            valid = await still_valid()
        except Exception as exc:  # a check, never the reason a stream dies
            log.warning("events.recheck_failed", error=str(exc))
            continue
        if not valid:
            log.info("events.stream_signed_out")
            subscription.close()
            return


async def _frames(
    hub: EventHub,
    request: Request,
    principal: Principal,
    *,
    feed_id: str | None,
    last_event_id: int | None,
    still_valid: StillValid | None = None,
) -> AsyncIterator[ServerSentEvent]:
    with hub.subscribe(principal, feed_id=feed_id, last_event_id=last_event_id) as subscription:
        recheck = (
            asyncio.create_task(_recheck(subscription, still_valid))
            if still_valid is not None
            else None
        )
        try:
            yield ServerSentEvent(comment="connected", retry=RETRY_MS)
            async for envelope in subscription:
                if await request.is_disconnected():
                    return
                seq = subscription.id_of(envelope)
                yield ServerSentEvent(
                    data=serialize(envelope.event),
                    event=envelope.name,
                    id=str(seq) if seq is not None else None,
                )
        finally:
            if recheck is not None:
                recheck.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await recheck


def _still_valid(container: ApiContainer, request: Request, principal: Principal) -> StillValid:
    """Whether the request's credentials still resolve to ``principal`` (same User)."""
    headers = request.headers
    path = request.url.path

    async def check() -> bool:
        now = await resolve_principal(container, headers, path)
        return (
            now is not None
            and now.username == principal.username
            and now.api_key_id == principal.api_key_id
        )

    return check


@router.get(
    "/events",
    operation_id="subscribe_events",
    openapi_extra={"x-capability": "subscribe_events"},
    summary="Server-sent events: job, progress, feed, item, request, resync",
    response_class=EventSourceResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def subscribe_events(
    request: Request,
    principal: PrincipalDep,
    services: ServicesDep,
    container: ContainerDep,
    feed_id: Annotated[str | None, Query()] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> Any:
    if feed_id is not None:
        await services.get_feed(feed_id)  # NotFound (404) unless the Principal sees it
    hub: EventHub = request.app.state.event_hub
    return EventSourceResponse(
        _frames(
            hub,
            request,
            principal,
            feed_id=feed_id,
            last_event_id=_last_event_id(last_event_id),
            still_valid=None
            if container.users.mode is AuthMode.open
            else _still_valid(container, request, principal),
        ),
        ping=PING_SECONDS,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__ = ["PING_SECONDS", "RECHECK_SECONDS", "RETRY_MS", "router"]
