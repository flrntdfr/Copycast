"""The two-level Feed cache: this process's LRU, then the shared Valkey, then one render."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from copycast.adapters.api import cache as cache_module
from copycast.adapters.api.cache import SHARED_PREFIX, FeedCache, SharedFeedCache, weak_etag

STAMP = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Rendered:
    revision: int
    body: bytes
    last_modified: datetime = STAMP
    content_type: str = "application/rss+xml; charset=utf-8"


class FakeValkey:
    """The three calls ``SharedFeedCache`` makes, in memory; ``down`` makes every call fail."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.ttls: dict[str, int] = {}
        self.down = False
        self.closed = False

    def _check(self) -> None:
        if self.down:
            raise ConnectionError("valkey is down")

    async def get(self, key: str) -> bytes | None:
        self._check()
        return self.data.get(key)

    async def set(self, key: str, value: bytes, *, ex: int, nx: bool = False) -> bool | None:
        self._check()
        if nx and key in self.data:
            return None
        self.data[key] = value
        self.ttls[key] = ex
        return True

    async def aclose(self) -> None:
        self.closed = True


def _renderer(revision: int, body: bytes, calls: list[int]) -> Any:
    async def render() -> Rendered:
        calls.append(revision)
        return Rendered(revision, body)

    return render


async def test_without_a_shared_level_the_lru_renders_once_per_revision() -> None:
    cache = FeedCache(capacity=1)
    calls: list[int] = []
    one = await cache.fetch("f1", 1, _renderer(1, b"<rss>1</rss>", calls))
    again = await cache.fetch("f1", 1, _renderer(1, b"<rss>1</rss>", calls))
    assert one is again and calls == [1] and one is not None
    assert one.etag == weak_etag(b"<rss>1</rss>")
    await cache.fetch("f2", 7, _renderer(7, b"<rss>f2</rss>", calls))
    assert len(cache) == 1 and cache.get("f1", 1) is None  # LRU capacity 1

    async def gone() -> None:
        return None

    assert await cache.fetch("f3", 1, gone) is None
    await cache.aclose()


async def test_replicas_share_one_render_per_revision() -> None:
    valkey = FakeValkey()
    first = FeedCache(shared=SharedFeedCache(valkey, ttl_seconds=600))
    second = FeedCache(shared=SharedFeedCache(valkey, ttl_seconds=600))
    calls: list[int] = []
    rendered = await first.fetch("f1", 3, _renderer(3, b"<rss>3</rss>", calls))
    served = await second.fetch("f1", 3, _renderer(3, b"<rss>3</rss>", calls))
    assert calls == [3], "the second replica took the body from the shared level"
    assert rendered is not None and served is not None
    assert (served.body, served.etag, served.last_modified, served.content_type) == (
        rendered.body,
        rendered.etag,
        rendered.last_modified,
        rendered.content_type,
    )
    assert valkey.ttls[f"{SHARED_PREFIX}f1:3"] == 600
    assert second.get("f1", 3) is served, "kept in the replica's own LRU too"
    await second.fetch("f1", 4, _renderer(4, b"<rss>4</rss>", calls))
    assert calls == [3, 4], "a new revision renders again"
    await first.aclose()
    assert valkey.closed


async def test_a_replica_that_loses_the_render_lock_waits_for_the_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cache_module, "RENDER_WAIT_STEP", 0.01)
    valkey = FakeValkey()
    shared = SharedFeedCache(valkey, ttl_seconds=60)
    assert await shared.lock("f1", 9) is True
    loser = FeedCache(shared=SharedFeedCache(valkey, ttl_seconds=60))
    calls: list[int] = []

    async def winner_finishes() -> None:
        await asyncio.sleep(0.05)
        await shared.put(
            "f1",
            FeedCache().put(
                "f1", revision=9, body=b"<rss>9</rss>", last_modified=STAMP, content_type="x"
            ),
        )

    finishing = asyncio.create_task(winner_finishes())
    entry = await loser.fetch("f1", 9, _renderer(9, b"<rss>mine</rss>", calls))
    await finishing
    assert calls == [] and entry is not None and entry.body == b"<rss>9</rss>"

    # The winner never delivers: the loser renders after the wait instead of failing.
    monkeypatch.setattr(cache_module, "RENDER_WAIT_SECONDS", 0.05)
    assert await shared.lock("f1", 10) is True
    late = await loser.fetch("f1", 10, _renderer(10, b"<rss>10</rss>", calls))
    assert calls == [10] and late is not None


async def test_a_shared_level_that_is_down_is_only_an_accelerator_lost() -> None:
    valkey = FakeValkey()
    valkey.down = True
    shared = SharedFeedCache(valkey, ttl_seconds=60)
    cache = FeedCache(shared=shared)
    calls: list[int] = []
    entry = await cache.fetch("f1", 1, _renderer(1, b"<rss/>", calls))
    assert entry is not None and calls == [1] and shared.errors == 3  # get, lock, set
    assert await shared.get("f1", 1) is None and shared.errors == 4
    valkey.down = False
    valkey.data[f"{SHARED_PREFIX}f1:2"] = b"not a cached feed"
    assert await shared.get("f1", 2) is None, "an undecodable value is a miss"


def test_from_url_accepts_valkey_schemes() -> None:
    shared = SharedFeedCache.from_url("valkey://:pw@localhost:6379/0", ttl_seconds=60)
    kwargs = shared._client.connection_pool.connection_kwargs  # pyright: ignore[reportPrivateUsage]
    assert kwargs["host"] == "localhost" and kwargs["password"] == "pw"
    assert kwargs["socket_timeout"] == cache_module.SHARED_TIMEOUT_SECONDS
