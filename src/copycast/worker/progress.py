"""Progress snapshots: Engine hooks -> ``jobs.progress`` + ``NOTIFY copycast_events``, throttled.

The Engine reports :class:`Progress` from its thread; the runner's monitor
takes the latest snapshot at most twice a second (``take_due``) and writes it
with the heartbeat. ``take_final`` gives the last snapshot for ``finish``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from copycast.application.models import JobProgress
from copycast.application.ports import Progress
from copycast.domain.enums import EnginePhase, ProgressPhase
from copycast.worker.constants import PROGRESS_MIN_INTERVAL_SECONDS

_PHASES: dict[EnginePhase, ProgressPhase] = {
    EnginePhase.downloading: ProgressPhase.downloading,
    EnginePhase.postprocessing: ProgressPhase.postprocessing,
    EnginePhase.finished: ProgressPhase.postprocessing,
}


def to_job_progress(progress: Progress, item_id: str | None = None) -> JobProgress:
    percent = progress.percent
    if progress.phase is EnginePhase.finished:
        percent = 100.0
    return JobProgress(
        phase=_PHASES[progress.phase],
        item_id=item_id,
        downloaded_bytes=progress.bytes_done,
        total_bytes=progress.bytes_total,
        percent=percent,
        speed_bps=progress.speed_bps,
        eta_seconds=progress.eta_s,
    )


class ProgressTracker:
    """Thread-safe holder of the latest snapshot with a flush throttle."""

    def __init__(
        self,
        *,
        item_id: str | None = None,
        min_interval: float = PROGRESS_MIN_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._item_id = item_id
        self._min_interval = min_interval
        self._clock = clock
        self._lock = threading.Lock()
        self._latest: JobProgress | None = None
        self._dirty = False
        self._last_flush: float | None = None
        self.updates = 0

    def on_progress(self, progress: Progress) -> None:
        """The Engine's progress callback."""
        self.update(to_job_progress(progress, self._item_id))

    def set_phase(self, phase: ProgressPhase) -> None:
        """A phase the job itself reports (``listing``, ``assets``)."""
        with self._lock:
            current = self._latest
        self.update(
            JobProgress(
                phase=phase,
                item_id=self._item_id,
                downloaded_bytes=current.downloaded_bytes if current else None,
                total_bytes=current.total_bytes if current else None,
                percent=current.percent if current else None,
            )
        )

    def update(self, snapshot: JobProgress) -> None:
        with self._lock:
            self._latest = snapshot
            self._dirty = True
            self.updates += 1

    def snapshot(self) -> JobProgress | None:
        with self._lock:
            return self._latest

    def take_due(self) -> JobProgress | None:
        """The latest snapshot when it changed and the throttle interval elapsed; else None."""
        now = self._clock()
        with self._lock:
            if not self._dirty or self._latest is None:
                return None
            if self._last_flush is not None and now - self._last_flush < self._min_interval:
                return None
            self._dirty = False
            self._last_flush = now
            return self._latest

    def take_final(self) -> JobProgress | None:
        with self._lock:
            self._dirty = False
            return self._latest


__all__ = ["ProgressTracker", "to_job_progress"]
