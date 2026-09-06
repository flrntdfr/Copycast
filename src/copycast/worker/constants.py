"""Timings, limits and exit codes of the worker (all from the plan)."""

from __future__ import annotations

import os
import socket
from datetime import timedelta
from typing import Final

from copycast.domain.enums import JobKind

CLAIM_TICK_SECONDS: Final = 1.0
HEARTBEAT_SECONDS: Final = 30.0
CANCEL_POLL_SECONDS: Final = 2.0
PROGRESS_MIN_INTERVAL_SECONDS: Final = 0.5
"""At most two progress flushes (``jobs.progress`` + NOTIFY) per second per job."""
LOG_FLUSH_SECONDS: Final = 2.0
MONITOR_TICK_SECONDS: Final = 0.25
SCHEDULER_TICK_SECONDS: Final = 60.0

SOFT_TIMEOUTS: Final[dict[JobKind, float]] = {
    JobKind.refresh: 15 * 60.0,
    JobKind.archive_item: 3 * 3600.0,
    JobKind.expand_request: 15 * 60.0,
    JobKind.prune: 3600.0,
    JobKind.rebuild: 3 * 3600.0,
}
UNRESPONSIVE_GRACE_SECONDS: Final = 60.0
"""After a soft timeout set the token, a thread still alive this much later is stuck."""
DRAIN_SECONDS: Final = 110.0

STALE_HEARTBEAT: Final = timedelta(minutes=5)
JOB_RETENTION: Final = timedelta(days=30)
REFRESH_RUN_RETENTION: Final = timedelta(days=90)
AUTOPRUNE_INTERVAL: Final = timedelta(hours=24)
STORAGE_FULL_RETRY: Final = timedelta(minutes=10)
QUARANTINE_RETRY: Final = timedelta(minutes=10)

BACKOFF_BASE_SECONDS: Final = 60.0
BACKOFF_MAX_SECONDS: Final = 6 * 3600.0
BACKOFF_JITTER: Final = 0.2

LOG_LINE_CAP: Final = 5000
"""After this many log lines per job only warnings and errors are kept."""
LEAF_CAP: Final = 1000
"""A Request expands to at most this many items."""

EXIT_OK: Final = 0
EXIT_LOCKED: Final = 1
EXIT_CONFIG: Final = 2
EXIT_STUCK: Final = 3


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


__all__ = [
    "AUTOPRUNE_INTERVAL",
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_JITTER",
    "BACKOFF_MAX_SECONDS",
    "CANCEL_POLL_SECONDS",
    "CLAIM_TICK_SECONDS",
    "DRAIN_SECONDS",
    "EXIT_CONFIG",
    "EXIT_LOCKED",
    "EXIT_OK",
    "EXIT_STUCK",
    "HEARTBEAT_SECONDS",
    "JOB_RETENTION",
    "LEAF_CAP",
    "LOG_FLUSH_SECONDS",
    "LOG_LINE_CAP",
    "MONITOR_TICK_SECONDS",
    "PROGRESS_MIN_INTERVAL_SECONDS",
    "QUARANTINE_RETRY",
    "REFRESH_RUN_RETENTION",
    "SCHEDULER_TICK_SECONDS",
    "SOFT_TIMEOUTS",
    "STALE_HEARTBEAT",
    "STORAGE_FULL_RETRY",
    "UNRESPONSIVE_GRACE_SECONDS",
    "default_worker_id",
]
