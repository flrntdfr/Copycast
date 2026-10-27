"""The rendered-feed cache keyed by ``(feed_id, revision)``: the Feed cache.

``revision`` is the published-feed change token: a hit needs the same
revision, so a stale body is never served. Two levels: this process's LRU
(the last ``capacity`` feeds, one entry per feed, no network at all) and,
when ``[cache] url`` names one, a Valkey (or Redis) every api replica shares,
so a revision is rendered once across the fleet. A render takes a short
``SET NX`` lock there; a replica that loses the race waits a moment for the
winner's body instead of rendering the same revision again. The shared level
is an accelerator only: any error there is logged and the request renders
from Postgres as if it were absent.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from copycast.logging import get_logger

log = get_logger(__name__)

SHARED_PREFIX = "copycast:feed:"
LOCK_PREFIX = "copycast:render:"
LOCK_SECONDS = 15
RENDER_WAIT_SECONDS = 2.0
RENDER_WAIT_STEP = 0.05
SHARED_TIMEOUT_SECONDS = 0.5
ERROR_LOG_INTERVAL = 60.0


@dataclass(frozen=True, slots=True)
class CachedFeed:
    revision: int
    body: bytes
    etag: str
    last_modified: datetime
    content_type: str


def weak_etag(body: bytes) -> str:
    """``W/"<sha256[:20]>"`` of the rendered body."""
    return f'W/"{hashlib.sha256(body).hexdigest()[:20]}"'


class RenderedLike(Protocol):
    @property
    def revision(self) -> int: ...
    @property
    def body(self) -> bytes: ...
    @property
    def last_modified(self) -> datetime: ...
    @property
    def content_type(self) -> str: ...


def _encode(entry: CachedFeed) -> bytes:
    header = {
        "revision": entry.revision,
        "last_modified": entry.last_modified.isoformat(),
        "content_type": entry.content_type,
    }
    return json.dumps(header).encode("utf-8") + b"\n" + entry.body


def _decode(raw: bytes) -> CachedFeed | None:
    header_raw, sep, body = raw.partition(b"\n")
    if not sep:
        return None
    try:
        header: dict[str, Any] = json.loads(header_raw)
        return CachedFeed(
            revision=int(header["revision"]),
            body=body,
            etag=weak_etag(body),
            last_modified=datetime.fromisoformat(str(header["last_modified"])),
            content_type=str(header["content_type"]),
        )
    except (ValueError, KeyError, TypeError):
        return None


class SharedFeedCache:
    """The level every api replica shares: one key per ``(feed_id, revision)``, with a TTL."""

    def __init__(self, client: Any, *, ttl_seconds: int) -> None:
        self._client = client
        self._ttl = ttl_seconds
        self._last_error_log = 0.0
        self.hits = 0
        self.misses = 0
        self.errors = 0

    @classmethod
    def from_url(cls, url: str, *, ttl_seconds: int) -> SharedFeedCache:
        import redis.asyncio as redis

        url = url.replace("valkeys://", "rediss://", 1).replace("valkey://", "redis://", 1)
        client = redis.Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
            url,
            socket_timeout=SHARED_TIMEOUT_SECONDS,
            socket_connect_timeout=SHARED_TIMEOUT_SECONDS,
            health_check_interval=30,
        )
        return cls(client, ttl_seconds=ttl_seconds)

    def _failed(self, what: str, exc: BaseException) -> None:
        self.errors += 1
        now = time.monotonic()
        if now - self._last_error_log >= ERROR_LOG_INTERVAL:
            self._last_error_log = now
            log.warning("feed_cache.shared_error", op=what, error=str(exc)[:200])

    async def get(self, feed_id: str, revision: int) -> CachedFeed | None:
        try:
            raw: bytes | None = await self._client.get(f"{SHARED_PREFIX}{feed_id}:{revision}")
        except Exception as exc:
            self._failed("get", exc)
            return None
        entry = _decode(raw) if raw is not None else None
        if entry is None:
            self.misses += 1
        else:
            self.hits += 1
        return entry

    async def put(self, feed_id: str, entry: CachedFeed) -> None:
        key = f"{SHARED_PREFIX}{feed_id}:{entry.revision}"
        try:
            await self._client.set(key, _encode(entry), ex=self._ttl)
        except Exception as exc:
            self._failed("set", exc)

    async def lock(self, feed_id: str, revision: int) -> bool:
        """``True`` when this replica should render (it won the lock, or the cache is down)."""
        try:
            won = await self._client.set(
                f"{LOCK_PREFIX}{feed_id}:{revision}", b"1", nx=True, ex=LOCK_SECONDS
            )
        except Exception as exc:
            self._failed("lock", exc)
            return True
        return bool(won)

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except Exception as exc:  # pragma: no cover - closing a dead connection
            self._failed("close", exc)


class FeedCache:
    def __init__(self, capacity: int = 128, *, shared: SharedFeedCache | None = None) -> None:
        self._capacity = max(1, capacity)
        self._entries: OrderedDict[str, CachedFeed] = OrderedDict()
        self.shared = shared
        self.hits = 0
        self.misses = 0

    def get(self, feed_id: str, revision: int) -> CachedFeed | None:
        entry = self._entries.get(feed_id)
        if entry is None or entry.revision != revision:
            self.misses += 1
            return None
        self._entries.move_to_end(feed_id)
        self.hits += 1
        return entry

    def put(
        self,
        feed_id: str,
        *,
        revision: int,
        body: bytes,
        last_modified: datetime,
        content_type: str,
    ) -> CachedFeed:
        entry = CachedFeed(
            revision=revision,
            body=body,
            etag=weak_etag(body),
            last_modified=last_modified,
            content_type=content_type,
        )
        self._entries[feed_id] = entry
        self._entries.move_to_end(feed_id)
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)
        return entry

    def invalidate(self, feed_id: str) -> None:
        self._entries.pop(feed_id, None)

    def _keep(self, feed_id: str, entry: CachedFeed) -> CachedFeed:
        self._entries[feed_id] = entry
        self._entries.move_to_end(feed_id)
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)
        return entry

    async def fetch(
        self,
        feed_id: str,
        revision: int,
        render: Callable[[], Awaitable[RenderedLike | None]],
    ) -> CachedFeed | None:
        """This process, then the shared level, then ``render`` (once across replicas)."""
        entry = self.get(feed_id, revision)
        if entry is not None:
            return entry
        shared = self.shared
        if shared is not None:
            found = await shared.get(feed_id, revision)
            if found is not None:
                return self._keep(feed_id, found)
            if not await shared.lock(feed_id, revision):
                loop = asyncio.get_running_loop()
                until = loop.time() + RENDER_WAIT_SECONDS
                while loop.time() < until:
                    await asyncio.sleep(RENDER_WAIT_STEP)
                    found = await shared.get(feed_id, revision)
                    if found is not None:
                        return self._keep(feed_id, found)
        rendered = await render()
        if rendered is None:
            return None
        entry = self.put(
            feed_id,
            revision=rendered.revision,
            body=rendered.body,
            last_modified=rendered.last_modified,
            content_type=rendered.content_type,
        )
        if shared is not None:
            await shared.put(feed_id, entry)
        return entry

    async def aclose(self) -> None:
        if self.shared is not None:
            await self.shared.aclose()

    def __len__(self) -> int:
        return len(self._entries)


__all__ = ["CachedFeed", "FeedCache", "SharedFeedCache", "weak_etag"]
