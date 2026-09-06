"""A small thread-safe TTL cache shared by the probe and the iTunes search."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable
from typing import Final, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")

DEFAULT_TTL_SECONDS: Final = 600.0
DEFAULT_MAX_ENTRIES: Final = 512


class TtlCache[K: Hashable, V]:
    """Entries expire ``ttl`` seconds after insertion; the oldest go first when full."""

    def __init__(
        self,
        ttl: float = DEFAULT_TTL_SECONDS,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl = ttl
        self.max_entries = max_entries
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[K, tuple[float, V]] = {}

    def get(self, key: K) -> V | None:
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= now:
                del self._entries[key]
                return None
            return value

    def put(self, key: K, value: V) -> None:
        now = self._clock()
        with self._lock:
            self._purge_locked(now)
            self._entries[key] = (now + self.ttl, value)
            while len(self._entries) > self.max_entries:
                oldest = next(iter(self._entries))
                del self._entries[oldest]

    def pop(self, key: K) -> V | None:
        with self._lock:
            entry = self._entries.pop(key, None)
        return entry[1] if entry is not None and entry[0] > self._clock() else None

    def purge(self) -> int:
        with self._lock:
            return self._purge_locked(self._clock())

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __contains__(self, key: object) -> bool:
        return self.get(key) is not None  # type: ignore[arg-type]

    def _purge_locked(self, now: float) -> int:
        expired = [key for key, (expires_at, _) in self._entries.items() if expires_at <= now]
        for key in expired:
            del self._entries[key]
        return len(expired)


__all__ = ["DEFAULT_MAX_ENTRIES", "DEFAULT_TTL_SECONDS", "TtlCache"]
