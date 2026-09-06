"""Batches the Engine's log lines into ``job_log_lines``.

The Engine calls :class:`JobLog` from its worker thread; the runner drains it
every few seconds and at the end of the job. After :data:`LOG_LINE_CAP` kept
lines only warnings and errors are recorded, so a chatty extraction cannot
grow the table without bound.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from copycast.domain.enums import LogLevel
from copycast.logging import get_logger
from copycast.worker.constants import LOG_LINE_CAP

LogLine = tuple[int, datetime, LogLevel, str]

log = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class JobLog:
    """An ``EngineLog`` that buffers lines for one job (thread-safe)."""

    def __init__(
        self,
        job_id: uuid.UUID,
        *,
        cap: int = LOG_LINE_CAP,
        start: int = 0,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        """``start`` is the last ``seq`` already stored (a retry continues the numbering)."""
        self.job_id = job_id
        self._cap = cap
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: list[LogLine] = []
        self._seq = start
        self._kept = 0
        self._dropped = 0

    # ------------------------------------------------------------------ EngineLog

    def debug(self, message: str) -> None:
        self._add(LogLevel.debug, message)

    def info(self, message: str) -> None:
        self._add(LogLevel.info, message)

    def warning(self, message: str) -> None:
        self._add(LogLevel.warning, message)

    def error(self, message: str) -> None:
        self._add(LogLevel.error, message)

    # ------------------------------------------------------------------ draining

    def drain(self) -> list[LogLine]:
        """Take every buffered line (the runner appends them to ``job_log_lines``)."""
        with self._lock:
            lines, self._pending = self._pending, []
        return lines

    @property
    def kept(self) -> int:
        return self._kept

    @property
    def dropped(self) -> int:
        return self._dropped

    def _add(self, level: LogLevel, message: str) -> None:
        text = message.rstrip()
        if not text:
            return
        with self._lock:
            if self._kept >= self._cap and level not in (LogLevel.warning, LogLevel.error):
                self._dropped += 1
                return
            self._kept += 1
            self._seq += 1
            self._pending.append((self._seq, self._clock(), level, text))
        log.debug("engine.log", job_id=str(self.job_id), level=level.value, message=text)


__all__ = ["JobLog", "LogLine"]
