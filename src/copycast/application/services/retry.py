"""Retry a unit of work that lost a race with a running job.

Postgres aborts one of two transactions that deadlock (``40P01``) or fail to
serialize (``40001``); the unit of work surfaces both as
:class:`~copycast.domain.exceptions.ConcurrentUpdate`. Deleting a Feed or an
Episode races the worker's archive transaction for the same rows (item ->
feed -> job in one order, feed -> cascade in the other), so those services run
their transaction through :func:`retry_concurrent` instead of answering 409
on the first collision.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from copycast.domain.exceptions import ConcurrentUpdate
from copycast.logging import get_logger

log = get_logger(__name__)

DEFAULT_ATTEMPTS = 5
BASE_DELAY_SECONDS = 0.05


async def retry_concurrent[T](
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = BASE_DELAY_SECONDS,
    what: str = "transaction",
) -> T:
    """Run ``operation`` until it succeeds or ``attempts`` :class:`ConcurrentUpdate` failures.

    Each retry waits ``base_delay * 2**n`` seconds (50 ms, 100 ms, 200 ms, ...); the
    last failure propagates unchanged so the API still answers 409.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except ConcurrentUpdate:
            if attempt >= attempts:
                log.warning("db.concurrent_update.exhausted", what=what, attempts=attempts)
                raise
            delay = base_delay * (2 ** (attempt - 1))
            log.info("db.concurrent_update.retry", what=what, attempt=attempt, delay=delay)
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


__all__ = ["BASE_DELAY_SECONDS", "DEFAULT_ATTEMPTS", "retry_concurrent"]
