"""The password-hash limiter: two verifies at once, sixteen logins waiting, then busy."""

from __future__ import annotations

import asyncio
import threading

import pytest

from copycast.application.services import auth
from copycast.domain.exceptions import LoginBusy


async def test_forty_logins_run_two_verifies_at_once_and_the_rest_are_busy() -> None:
    release = threading.Event()
    lock = threading.Lock()
    running = 0
    peak = 0
    ran = 0

    def verify() -> bool:
        nonlocal running, peak, ran
        with lock:
            running += 1
            peak = max(peak, running)
        release.wait(5)
        with lock:
            running -= 1
            ran += 1
        return True

    tasks = [asyncio.create_task(auth.run_hashing(verify)) for _ in range(40)]
    await asyncio.sleep(0.2)  # every login has queued or been refused
    busy = [task for task in tasks if task.done()]
    assert len(busy) == 40 - auth.HASH_SLOTS - auth.HASH_QUEUE_MAX == 22
    for task in busy:
        error = task.exception()
        assert isinstance(error, LoginBusy) and error.retry_after == 5
    release.set()
    results = await asyncio.gather(*(t for t in tasks if t not in busy))
    assert results == [True] * 18
    assert peak == auth.HASH_SLOTS == 2
    assert ran == 18
    assert auth._HashQueue.pending == 0  # pyright: ignore[reportPrivateUsage]
    assert auth.HASH_LIMITER.borrowed_tokens == 0


async def test_a_login_that_waits_too_long_is_busy_without_verifying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "HASH_WAIT_SECONDS", 0.05)
    release = threading.Event()
    calls: list[str] = []

    def slow() -> str:
        release.wait(5)
        return "slow"

    def never() -> str:
        calls.append("never")
        return "never"

    holders = [asyncio.create_task(auth.run_hashing(slow)) for _ in range(auth.HASH_SLOTS)]
    await asyncio.sleep(0.05)
    with pytest.raises(LoginBusy):
        await auth.run_hashing(never)
    assert calls == []
    release.set()
    assert await asyncio.gather(*holders) == ["slow", "slow"]
    # The slots are free again.
    assert await auth.run_hashing(lambda: "free") == "free"
    assert auth.HASH_LIMITER.borrowed_tokens == 0
