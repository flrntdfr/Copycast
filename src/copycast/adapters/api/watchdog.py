"""The api's event-loop stall watchdog: what blocked the loop, logged while it is blocked.

Every request of the api runs on one event loop, so anything synchronous on it (a
blocking read, CPU-heavy rendering or compression) stalls every request in flight,
media streams included. A task on the loop stamps a heartbeat every ``beat``
seconds; a thread of its own watches the stamp. When it is older than
``threshold`` the thread logs ``api.event_loop_blocked`` with the loop thread's
current stack and the request path it is serving (keys redacted), once per stall;
when the heartbeat comes back the task logs ``api.event_loop_stalled`` with how
long the loop was gone. Both are warnings: a healthy api logs neither.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import threading
import time
import traceback
from pathlib import PurePath
from types import FrameType
from typing import Any, cast

from copycast.adapters.api.middleware import logged_path
from copycast.logging import get_logger

log = get_logger(__name__)

STALL_THRESHOLD_SECONDS = 1.0
BEAT_SECONDS = 0.1
STACK_DEPTH = 25


def _short(filename: str) -> str:
    """``copycast/app.py``, ``starlette/middleware/gzip.py``: the last three path parts."""
    return "/".join(PurePath(filename).parts[-3:])


def stack_of(frame: FrameType | None, depth: int = STACK_DEPTH) -> list[str]:
    """The innermost ``depth`` frames above ``frame``, innermost last, as ``file:line func``."""
    if frame is None:
        return []
    summary = traceback.extract_stack(frame)[-depth:]
    return [f"{_short(entry.filename)}:{entry.lineno} {entry.name}" for entry in summary]


def request_path_of(frame: FrameType | None) -> str | None:
    """The path of the HTTP request whose code is on the stack: the innermost ASGI
    ``scope`` local found walking outwards, as the request log writes it."""
    while frame is not None:
        found: Any = frame.f_locals.get("scope")
        if isinstance(found, dict):
            scope = cast("dict[str, Any]", found)
            if scope.get("type") == "http":
                path = scope.get("path")
                return logged_path(path) if isinstance(path, str) else None
        frame = frame.f_back
    return None


class LoopWatchdog:
    """Start inside the running loop (:meth:`start`), stop at shutdown (:meth:`stop`)."""

    def __init__(
        self, *, threshold: float = STALL_THRESHOLD_SECONDS, beat: float = BEAT_SECONDS
    ) -> None:
        self.threshold = threshold
        self.beat = beat
        self.stalls = 0
        """Stalls seen since start (the heartbeat came back late by ``threshold`` or more)."""
        self._last = time.monotonic()
        self._blocked_logged = False
        self._loop_thread: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._loop_thread = threading.get_ident()
        self._last = time.monotonic()
        self._stop.clear()
        self._task = asyncio.get_running_loop().create_task(
            self._heartbeat(), name="copycast-loop-watchdog"
        )
        self._thread = threading.Thread(
            target=self._watch, name="copycast-loop-watchdog", daemon=True
        )
        self._thread.start()

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, self.beat * 10)

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.beat)
            self.beat_once()

    def beat_once(self) -> None:
        """Stamp the heartbeat; log the stall that just ended, if the stamp was that old."""
        now = time.monotonic()
        late = now - self._last - self.beat
        self._last = now
        if late >= self.threshold:
            self.stalls += 1
            log.warning("api.event_loop_stalled", stalled_ms=round(late * 1000))
        self._blocked_logged = False

    def _watch(self) -> None:
        while not self._stop.wait(self.beat):
            try:
                self.check_once()
            except Exception:  # pragma: no cover - the watchdog must outlive any one report
                log.exception("api.watchdog_failed")

    def check_once(self) -> None:
        """From the watching thread: log the loop's stack once when the stamp is too old."""
        age = time.monotonic() - self._last
        if age < self.threshold + self.beat or self._blocked_logged:
            return
        self._blocked_logged = True
        frame = sys._current_frames().get(self._loop_thread or -1)  # pyright: ignore[reportPrivateUsage]
        log.warning(
            "api.event_loop_blocked",
            blocked_ms=round(age * 1000),
            request_path=request_path_of(frame),
            # Not ``stack``: structlog's console renderer reserves that key for a string.
            loop_stack=stack_of(frame),
        )


__all__ = ["LoopWatchdog", "request_path_of", "stack_of"]
