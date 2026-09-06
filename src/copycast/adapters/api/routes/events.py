"""``GET /api/events``: the server-sent event stream.

``text/event-stream`` with ``retry: 3000``, a keepalive comment every 15 s,
``id:`` per-process sequence numbers and a 1000-event replay via
``Last-Event-ID``. Frames: ``job``, ``progress``, ``feed``, ``item``,
``request``, ``resync`` (see ``application.events``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from sse_starlette.event import ServerSentEvent
from sse_starlette.sse import EventSourceResponse

from copycast.adapters.api.events import EventHub
from copycast.application.events import serialize

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
    hub: EventHub, request: Request, *, feed_id: str | None, last_event_id: int | None
) -> AsyncIterator[ServerSentEvent]:
    with hub.subscribe(feed_id=feed_id, last_event_id=last_event_id) as subscription:
        yield ServerSentEvent(comment="connected", retry=RETRY_MS)
        async for envelope in subscription:
            if await request.is_disconnected():
                return
            yield ServerSentEvent(
                data=serialize(envelope.event), event=envelope.name, id=str(envelope.id)
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
    feed_id: Annotated[str | None, Query()] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> Any:
    hub: EventHub = request.app.state.event_hub
    return EventSourceResponse(
        _frames(hub, request, feed_id=feed_id, last_event_id=_last_event_id(last_event_id)),
        ping=PING_SECONDS,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__ = ["PING_SECONDS", "RETRY_MS", "router"]
