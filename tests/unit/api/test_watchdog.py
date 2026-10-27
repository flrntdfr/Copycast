"""The event-loop stall watchdog and the feed route's Accept-Encoding rule."""

from __future__ import annotations

import asyncio
import time

import pytest
import structlog

from copycast.adapters.api.routes.public import accepts_gzip
from copycast.adapters.api.watchdog import LoopWatchdog, request_path_of


def block_the_loop_like_a_slow_handler(seconds: float) -> None:
    scope = {"type": "http", "path": "/feeds/abc/k/s3cret/media/0123456789abcdef.m4a"}
    assert scope  # a local the watchdog finds on the stack, as in an ASGI middleware frame
    time.sleep(seconds)


async def test_a_blocked_loop_is_logged_with_its_stack_while_blocked_then_its_length() -> None:
    watchdog = LoopWatchdog(threshold=0.2, beat=0.02)
    with structlog.testing.capture_logs() as events:
        watchdog.start()
        await asyncio.sleep(0.1)
        block_the_loop_like_a_slow_handler(0.5)
        await asyncio.sleep(0.1)
        await watchdog.stop()
    blocked = [e for e in events if e["event"] == "api.event_loop_blocked"]
    stalled = [e for e in events if e["event"] == "api.event_loop_stalled"]
    assert len(blocked) == 1, "once per stall"
    assert any("block_the_loop_like_a_slow_handler" in line for line in blocked[0]["loop_stack"])
    assert blocked[0]["request_path"] == "/feeds/abc/k/***/media/0123456789abcdef.m4a"
    assert blocked[0]["blocked_ms"] >= 200
    assert len(stalled) == 1 and stalled[0]["stalled_ms"] >= 400
    assert watchdog.stalls == 1


async def test_a_healthy_loop_logs_nothing() -> None:
    watchdog = LoopWatchdog(threshold=0.2, beat=0.02)
    with structlog.testing.capture_logs() as events:
        watchdog.start()
        await asyncio.sleep(0.3)
        await watchdog.stop()
    assert [e for e in events if e["event"].startswith("api.event_loop")] == []


def test_request_path_of_no_frame_is_none() -> None:
    assert request_path_of(None) is None


@pytest.mark.parametrize(
    ("header", "gzip"),
    [
        (None, False),
        ("", False),
        ("gzip", True),
        ("gzip, deflate, br", True),
        ("br;q=1.0, gzip;q=0.8", True),
        ("GZIP", True),
        ("gzip;q=0", False),
        ("gzip;q=0.0, *;q=1", False),
        ("*", True),
        ("*;q=0", False),
        ("identity", False),
        ("gzip;q=nonsense", False),
    ],
)
def test_accepts_gzip(header: str | None, gzip: bool) -> None:
    assert accepts_gzip(header) is gzip
