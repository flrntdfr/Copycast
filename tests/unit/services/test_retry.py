"""``retry_concurrent``: retries only ``ConcurrentUpdate``, backs off, re-raises when exhausted."""

from __future__ import annotations

import pytest

from copycast.application.services.retry import retry_concurrent
from copycast.domain.exceptions import ConcurrentUpdate, NotFound


async def test_returns_the_first_successful_result() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ConcurrentUpdate()
        return "done"

    assert await retry_concurrent(operation, base_delay=0.001) == "done"
    assert calls == 3


async def test_exhausted_attempts_reraise_the_last_conflict() -> None:
    calls = 0

    async def operation() -> None:
        nonlocal calls
        calls += 1
        raise ConcurrentUpdate("still racing")

    with pytest.raises(ConcurrentUpdate, match="still racing"):
        await retry_concurrent(operation, attempts=3, base_delay=0.001)
    assert calls == 3


async def test_other_domain_errors_are_not_retried() -> None:
    calls = 0

    async def operation() -> None:
        nonlocal calls
        calls += 1
        raise NotFound("feed", "x")

    with pytest.raises(NotFound):
        await retry_concurrent(operation, base_delay=0.001)
    assert calls == 1


async def test_rejects_zero_attempts() -> None:
    async def operation() -> None:
        return None

    with pytest.raises(ValueError):
        await retry_concurrent(operation, attempts=0)
