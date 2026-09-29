"""The light Refresh's pure parts: the cooldown gate, the result value, the per-feed lock."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from copycast.application.services.light_refresh import (
    LIGHT_LISTING_LIMIT,
    LIGHT_LISTING_THREADS,
    LIGHT_REFRESH_DEADLINE_SECONDS,
    LightRefreshResult,
    feed_lock,
    light_refresh_allowed,
    listing_executor,
    locked_feed_ids,
)

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
COOLDOWN = 5


def _feed(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "paused": False,
        "follow": True,
        "last_refresh_attempt_at": None,
        "last_light_refresh_at": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, True),  # never refreshed either way
        ({"paused": True}, False),
        ({"paused": True, "follow": False}, False),
        # Follow does not matter: a fetch lists new items even when the policy archives nothing.
        ({"follow": False}, True),
        # The full Refresh's stamp counts.
        ({"last_refresh_attempt_at": NOW - timedelta(minutes=4)}, False),
        ({"last_refresh_attempt_at": NOW - timedelta(minutes=5)}, True),
        ({"last_refresh_attempt_at": NOW - timedelta(hours=1)}, True),
        # So does the light one.
        ({"last_light_refresh_at": NOW - timedelta(minutes=4)}, False),
        ({"last_light_refresh_at": NOW - timedelta(minutes=5)}, True),
        # The newest of the two decides; the other is ignored.
        (
            {
                "last_refresh_attempt_at": NOW - timedelta(hours=1),
                "last_light_refresh_at": NOW - timedelta(minutes=1),
            },
            False,
        ),
        (
            {
                "last_refresh_attempt_at": NOW - timedelta(minutes=1),
                "last_light_refresh_at": NOW - timedelta(hours=1),
            },
            False,
        ),
        (
            {
                "last_refresh_attempt_at": NOW - timedelta(hours=1),
                "last_light_refresh_at": NOW - timedelta(hours=2),
            },
            True,
        ),
    ],
)
def test_light_refresh_allowed(overrides: dict[str, Any], expected: bool) -> None:
    assert light_refresh_allowed(_feed(**overrides), now=NOW, cooldown_minutes=COOLDOWN) is expected


def test_zero_cooldown_allows_every_fetch_unless_paused() -> None:
    fresh = _feed(last_refresh_attempt_at=NOW, last_light_refresh_at=NOW)
    assert light_refresh_allowed(fresh, now=NOW, cooldown_minutes=0) is True
    assert light_refresh_allowed(_feed(paused=True), now=NOW, cooldown_minutes=0) is False


def test_result_defaults_and_constants() -> None:
    failed = LightRefreshResult(status="failed", error="boom")
    assert (failed.new_count, failed.wanted_count, failed.error) == (0, 0, "boom")
    assert LightRefreshResult(status="skipped").error is None
    assert LIGHT_LISTING_LIMIT == 15 and LIGHT_REFRESH_DEADLINE_SECONDS == 8.0


def test_listing_pool_is_bounded_named_and_shared() -> None:
    """Light listings never take the loop's default executor (media, exports) hostage."""
    pool = listing_executor()
    assert pool is listing_executor()
    assert pool._max_workers == LIGHT_LISTING_THREADS == 4
    name = pool.submit(lambda: threading.current_thread().name).result(timeout=5)
    assert name.startswith("light-refresh")


async def test_feed_lock_serializes_one_feed_and_drops_its_entry() -> None:
    order: list[str] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def first() -> None:
        async with feed_lock("f"):
            order.append("first:in")
            started.set()
            await release.wait()
            order.append("first:out")

    async def second() -> None:
        await started.wait()
        async with feed_lock("f"):
            order.append("second:in")

    async def other() -> None:
        await started.wait()
        async with feed_lock("g"):
            order.append("other:in")

    tasks = [asyncio.create_task(t()) for t in (first, second, other)]
    await started.wait()
    await asyncio.sleep(0.05)
    assert "other:in" in order and "second:in" not in order, "another feed is never blocked"
    assert locked_feed_ids() == ["f"]
    release.set()
    await asyncio.gather(*tasks)
    assert order.index("first:out") < order.index("second:in")
    assert locked_feed_ids() == []
