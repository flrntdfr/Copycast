"""The EventHub: one ``LISTEN copycast_events`` connection fanned out to SSE subscribers.

The worker (and the api itself, through ``UnitOfWork.publish``) runs
``NOTIFY copycast_events`` after commit; the hub parses each payload with
``application.events.parse``, stamps a per-process sequence id, keeps the last
``buffer_size`` events for ``Last-Event-ID`` replay and pushes the event into
every subscriber's bounded queue. A subscriber that cannot keep up is closed:
the client reconnects and replays from its last id. When the LISTEN
connection drops the hub reconnects with backoff and emits ``resync``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql
from pydantic import BaseModel

from copycast.application.events import CHANNEL, InvalidEvent, ResyncEvent, parse
from copycast.logging import get_logger

log = get_logger(__name__)

DEFAULT_BUFFER_SIZE = 1000
DEFAULT_QUEUE_SIZE = 256
RECONNECT_MAX_SECONDS = 30.0
NOTIFY_POLL_SECONDS = 1.0


def psycopg_conninfo(database_url: str) -> str:
    """``postgresql+psycopg://`` (SQLAlchemy) -> ``postgresql://`` (psycopg)."""
    parts = urlsplit(database_url)
    scheme = "postgresql" if parts.scheme.startswith("postgres") else parts.scheme
    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, ""))


def event_feed_id(event: BaseModel) -> str | None:
    """The feed an event concerns (``None`` for ``resync`` and feed-less jobs)."""
    job = getattr(event, "job", None)
    if job is not None:
        feed_id = getattr(job, "feed_id", None)
        return feed_id if isinstance(feed_id, str) else None
    feed_id = getattr(event, "feed_id", None)
    return feed_id if isinstance(feed_id, str) else None


@dataclass(frozen=True, slots=True)
class Envelope:
    """One event with its per-process sequence id."""

    id: int
    event: BaseModel

    @property
    def name(self) -> str:
        name = getattr(self.event, "event", "")
        return name if isinstance(name, str) else ""


class Subscription:
    """A bounded queue of envelopes; ``None`` marks the end of the stream."""

    def __init__(self, hub: EventHub, *, feed_id: str | None, queue_size: int) -> None:
        self._hub = hub
        self.feed_id = feed_id
        self._queue: asyncio.Queue[Envelope | None] = asyncio.Queue(maxsize=queue_size)
        self.closed = False
        self.dropped = 0

    def wants(self, envelope: Envelope) -> bool:
        if self.feed_id is None or envelope.name == "resync":
            return True
        return event_feed_id(envelope.event) == self.feed_id

    def offer(self, envelope: Envelope) -> None:
        if self.closed or not self.wants(envelope):
            return
        try:
            self._queue.put_nowait(envelope)
        except asyncio.QueueFull:
            self.dropped += 1
            self.close()

    def close(self) -> None:
        """End the stream; the queue's tail sentinel wakes the consumer."""
        if self.closed:
            return
        self.closed = True
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)

    async def __aiter__(self) -> AsyncIterator[Envelope]:
        while True:
            envelope = await self._queue.get()
            if envelope is None:
                return
            yield envelope
            if self.closed and self._queue.empty():
                return

    def __enter__(self) -> Subscription:
        return self

    def __exit__(self, *_: object) -> None:
        self._hub.unsubscribe(self)


class EventHub:
    """LISTEN once per process, fan out to subscribers, replay the last events."""

    def __init__(
        self,
        conninfo: str,
        *,
        channel: str = CHANNEL,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        reconnect_max_seconds: float = RECONNECT_MAX_SECONDS,
    ) -> None:
        self._conninfo = conninfo
        self._channel = channel
        self._buffer: deque[Envelope] = deque(maxlen=buffer_size)
        self._queue_size = queue_size
        self._reconnect_max = reconnect_max_seconds
        self._subscribers: set[Subscription] = set()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._next_id = 1
        self.connected = False
        self.connections = 0
        self._ready = asyncio.Event()

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._listen(), name="copycast-event-hub")

    async def wait_connected(self, deadline_seconds: float = 5.0) -> bool:
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(deadline_seconds):
                await self._ready.wait()
        return self.connected

    async def stop(self) -> None:
        self._stopping = True
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for subscriber in list(self._subscribers):
            subscriber.close()
        self._subscribers.clear()
        self.connected = False

    # ------------------------------------------------------------------ publishing

    @property
    def last_id(self) -> int:
        return self._next_id - 1

    def publish(self, event: BaseModel) -> Envelope:
        """Stamp, buffer and fan out one event (also used for locally raised ``resync``)."""
        envelope = Envelope(id=self._next_id, event=event)
        self._next_id += 1
        self._buffer.append(envelope)
        for subscriber in list(self._subscribers):
            subscriber.offer(envelope)
        return envelope

    def _handle_payload(self, payload: str) -> None:
        try:
            event = parse(payload)
        except InvalidEvent as exc:
            log.warning("events.invalid_payload", error=str(exc))
            return
        self.publish(event)

    # ------------------------------------------------------------------ subscribing

    def subscribe(
        self, *, feed_id: str | None = None, last_event_id: int | None = None
    ) -> Subscription:
        """A subscription replaying every buffered event after ``last_event_id``.

        A ``last_event_id`` older than the buffer (or unknown after a restart)
        first yields a ``resync`` so the client refetches everything.
        """
        subscription = Subscription(self, feed_id=feed_id, queue_size=self._queue_size)
        if last_event_id is not None:
            oldest = self._buffer[0].id if self._buffer else self._next_id
            if last_event_id + 1 < oldest or last_event_id >= self._next_id:
                subscription.offer(
                    Envelope(id=self.last_id, event=ResyncEvent(reason="replay-gap"))
                )
            for envelope in self._buffer:
                if envelope.id > last_event_id:
                    subscription.offer(envelope)
        self._subscribers.add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        self._subscribers.discard(subscription)
        subscription.close()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # ------------------------------------------------------------------ listening

    async def _listen(self) -> None:
        delay = 1.0
        while not self._stopping:
            try:
                async with await psycopg.AsyncConnection.connect(
                    self._conninfo, autocommit=True, application_name="copycast-events"
                ) as conn:
                    await conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(self._channel)))
                    self.connections += 1
                    self.connected = True
                    self._ready.set()
                    if self.connections > 1:
                        self.publish(ResyncEvent(reason="listen-reconnected"))
                    delay = 1.0
                    while not self._stopping:
                        gen: Any = conn.notifies(timeout=NOTIFY_POLL_SECONDS)
                        async for notification in gen:
                            self._handle_payload(str(notification.payload))
            except asyncio.CancelledError:
                raise
            except (psycopg.Error, OSError) as exc:
                self.connected = False
                if self._stopping:
                    return
                log.warning("events.listen_failed", error=str(exc), retry_seconds=delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._reconnect_max)
        self.connected = False


__all__ = [
    "DEFAULT_BUFFER_SIZE",
    "DEFAULT_QUEUE_SIZE",
    "Envelope",
    "EventHub",
    "Subscription",
    "event_feed_id",
    "psycopg_conninfo",
]
