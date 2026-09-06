"""Server-sent events shared by the worker (``NOTIFY copycast_events``) and the api.

The wire form is one JSON object ``{"event": <name>, "data": {...}}``; the
Postgres NOTIFY payload limit keeps it under :data:`MAX_PAYLOAD_BYTES`.
:func:`serialize` and :func:`parse` are the only codec both processes use.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from copycast.application.models import JobProgress, JobRead
from copycast.domain.enums import ArchiveState, RequestStatus

MAX_PAYLOAD_BYTES = 8000
"""Postgres refuses NOTIFY payloads of 8000 bytes or more."""

CHANNEL = "copycast_events"
JOBS_CHANNEL = "copycast_jobs"


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class JobEvent(_Event):
    """A job changed status; ``data`` is the full JobRead."""

    event: Literal["job"] = "job"
    job: JobRead


class ProgressEvent(_Event):
    event: Literal["progress"] = "progress"
    job_id: UUID
    feed_id: str | None = None
    item_id: str | None = None
    progress: JobProgress


class FeedEvent(_Event):
    """Published content of a feed changed (``revision`` bumped)."""

    event: Literal["feed"] = "feed"
    feed_id: str
    revision: int
    reason: str


class ItemEvent(_Event):
    event: Literal["item"] = "item"
    feed_id: str
    item_id: str
    state: ArchiveState


class RequestEvent(_Event):
    event: Literal["request"] = "request"
    feed_id: str
    request_id: UUID
    status: RequestStatus
    item_count: int = 0


class ResyncEvent(_Event):
    """The api lost its LISTEN connection; clients should refetch everything."""

    event: Literal["resync"] = "resync"
    reason: str | None = None


Event = Annotated[
    JobEvent | ProgressEvent | FeedEvent | ItemEvent | RequestEvent | ResyncEvent,
    Field(discriminator="event"),
]

EVENT_NAMES: frozenset[str] = frozenset({"job", "progress", "feed", "item", "request", "resync"})

_adapter: TypeAdapter[Any] = TypeAdapter(Event)


class EventTooLarge(ValueError):
    """The serialized event exceeds the NOTIFY payload limit."""


class InvalidEvent(ValueError):
    """The payload is not a Copycast event."""


def event_name(event: BaseModel) -> str:
    name = getattr(event, "event", None)
    if not isinstance(name, str):
        raise InvalidEvent(f"{type(event).__name__} carries no event name")
    return name


def to_wire(event: BaseModel) -> dict[str, Any]:
    """``{"event": name, "data": {...}}`` with ``data`` excluding the ``event`` key."""
    data = event.model_dump(mode="json", exclude={"event"})
    return {"event": event_name(event), "data": data}


def serialize(event: BaseModel) -> str:
    """Compact JSON for NOTIFY and the SSE ``data:`` line."""
    payload = json.dumps(to_wire(event), separators=(",", ":"), ensure_ascii=False)
    if len(payload.encode("utf-8")) >= MAX_PAYLOAD_BYTES:
        raise EventTooLarge(
            f"{event_name(event)} event is {len(payload.encode('utf-8'))} bytes; "
            f"the limit is {MAX_PAYLOAD_BYTES - 1}"
        )
    return payload


def parse(payload: str | bytes | dict[str, Any]) -> Any:
    """Inverse of :func:`serialize`; returns one of the event models."""
    if isinstance(payload, str | bytes):
        try:
            raw: Any = json.loads(payload)
        except ValueError as exc:
            raise InvalidEvent(f"not JSON: {exc}") from exc
    else:
        raw = payload
    if not isinstance(raw, dict):
        raise InvalidEvent("event payload must be a JSON object")
    wire: dict[str, Any] = dict(raw)  # type: ignore[arg-type]
    name = wire.get("event")
    data = wire.get("data")
    if not isinstance(name, str) or name not in EVENT_NAMES:
        raise InvalidEvent(f"unknown event {name!r}")
    if not isinstance(data, dict):
        raise InvalidEvent("event payload lacks a data object")
    try:
        return _adapter.validate_python({"event": name, **data})  # type: ignore[dict-item]
    except ValidationError as exc:
        raise InvalidEvent(f"invalid {name} event: {exc.error_count()} error(s)") from exc


__all__ = [
    "CHANNEL",
    "EVENT_NAMES",
    "JOBS_CHANNEL",
    "MAX_PAYLOAD_BYTES",
    "Event",
    "EventTooLarge",
    "FeedEvent",
    "InvalidEvent",
    "ItemEvent",
    "JobEvent",
    "ProgressEvent",
    "RequestEvent",
    "ResyncEvent",
    "event_name",
    "parse",
    "serialize",
    "to_wire",
]
