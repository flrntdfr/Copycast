from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from copycast.application.events import (
    EVENT_NAMES,
    MAX_PAYLOAD_BYTES,
    EventTooLarge,
    FeedEvent,
    InvalidEvent,
    ItemEvent,
    JobEvent,
    ProgressEvent,
    RequestEvent,
    ResyncEvent,
    parse,
    serialize,
    to_wire,
)
from copycast.application.models import JobProgress, JobRead
from copycast.domain.enums import (
    ArchiveState,
    JobKind,
    JobStatus,
    JobTrigger,
    ProgressPhase,
    RequestStatus,
)


def _job() -> JobRead:
    return JobRead(
        id=uuid4(),
        kind=JobKind.archive_item,
        feed_id="feed1",
        item_id="item1",
        trigger=JobTrigger.manual,
        status=JobStatus.running,
        created_at=datetime(2024, 3, 1, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    "event",
    [
        JobEvent(job=_job()),
        ProgressEvent(
            job_id=uuid4(),
            feed_id="feed1",
            item_id="item1",
            progress=JobProgress(phase=ProgressPhase.downloading, percent=12.5),
        ),
        FeedEvent(feed_id="feed1", revision=7, reason="refresh"),
        ItemEvent(feed_id="feed1", item_id="item1", state=ArchiveState.archived),
        RequestEvent(
            feed_id="inbox1", request_id=uuid4(), status=RequestStatus.expanded, item_count=3
        ),
        ResyncEvent(reason="listen reconnected"),
    ],
)
def test_round_trip(event: object) -> None:
    payload = serialize(event)  # type: ignore[arg-type]
    wire = json.loads(payload)
    assert set(wire) == {"event", "data"}
    assert wire["event"] in EVENT_NAMES
    assert "event" not in wire["data"]
    assert parse(payload) == event
    assert parse(payload.encode()) == event
    assert parse(wire) == event


def test_wire_shape_of_a_feed_event() -> None:
    assert to_wire(FeedEvent(feed_id="f", revision=2, reason="item")) == {
        "event": "feed",
        "data": {"feed_id": "f", "revision": 2, "reason": "item"},
    }


def test_serialize_is_compact_and_under_the_notify_limit() -> None:
    payload = serialize(ResyncEvent())
    assert payload == '{"event":"resync","data":{"reason":null}}'
    assert len(payload.encode()) < MAX_PAYLOAD_BYTES


def test_oversized_events_are_refused() -> None:
    job = _job().model_copy(update={"error": "x" * MAX_PAYLOAD_BYTES})
    with pytest.raises(EventTooLarge):
        serialize(JobEvent(job=job))


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        '{"event":"nope","data":{}}',
        '{"event":"feed"}',
        '{"event":"feed","data":{"feed_id":"f"}}',
        '{"data":{"feed_id":"f","revision":1,"reason":"r"}}',
    ],
)
def test_invalid_payloads(payload: str) -> None:
    with pytest.raises(InvalidEvent):
        parse(payload)
