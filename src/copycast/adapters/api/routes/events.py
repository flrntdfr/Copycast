"""``GET /api/events``: the server-sent event stream.

``text/event-stream`` with ``retry: 3000``, a keepalive comment every 15 s,
``id:`` per-process sequence numbers of the Principal's view (an Admin's counts every
event, a Member's only their own, ``adapters.api.events``) and a 1000-event replay via
``Last-Event-ID``. Frames: ``job``, ``progress``, ``feed``, ``item``,
``request``, ``resync`` (see ``application.events``). A stream carries only the
events of the Principal's own Feeds (every Feed for an Admin) and ``resync``;
``?feed_id=`` of a Feed the Principal cannot see is 404.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from sse_starlette.event import ServerSentEvent
from sse_starlette.sse import EventSourceResponse

from copycast.adapters.api.deps import PrincipalDep, ServicesDep
from copycast.adapters.api.events import EventHub
from copycast.application.events import serialize
from copycast.domain.users import Principal

router = APIRouter(tags=["events"])

RETRY_MS = 3000
PING_SECONDS = 15


def _last_event_id(header: str | None) -> int | None:
    if header is None:
        return None
    try:
        value = int(header.strip())
    except ValueError:
        return None
    return value if value >= 0 else None


async def _frames(
    hub: EventHub,
    request: Request,
    principal: Principal,
    *,
    feed_id: str | None,
    last_event_id: int | None,
) -> AsyncIterator[ServerSentEvent]:
    with hub.subscribe(principal, feed_id=feed_id, last_event_id=last_event_id) as subscription:
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
        ),
        ping=PING_SECONDS,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__ = ["PING_SECONDS", "RETRY_MS", "router"]
