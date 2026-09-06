"""The rendered-feed cache keyed by ``(feed_id, revision)``.

``revision`` is the published-feed change token: a hit needs the same
revision, so a stale body is never served; the last ``capacity`` feeds are
kept (LRU) and one entry per feed.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class CachedFeed:
    revision: int
    body: bytes
    etag: str
    last_modified: datetime
    content_type: str


def weak_etag(body: bytes) -> str:
    """``W/"<sha256[:20]>"`` of the rendered body."""
    return f'W/"{hashlib.sha256(body).hexdigest()[:20]}"'


class FeedCache:
    def __init__(self, capacity: int = 128) -> None:
        self._capacity = max(1, capacity)
        self._entries: OrderedDict[str, CachedFeed] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, feed_id: str, revision: int) -> CachedFeed | None:
        entry = self._entries.get(feed_id)
        if entry is None or entry.revision != revision:
            self.misses += 1
            return None
        self._entries.move_to_end(feed_id)
        self.hits += 1
        return entry

    def put(
        self,
        feed_id: str,
        *,
        revision: int,
        body: bytes,
        last_modified: datetime,
        content_type: str,
    ) -> CachedFeed:
        entry = CachedFeed(
            revision=revision,
            body=body,
            etag=weak_etag(body),
            last_modified=last_modified,
            content_type=content_type,
        )
        self._entries[feed_id] = entry
        self._entries.move_to_end(feed_id)
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)
        return entry

    def invalidate(self, feed_id: str) -> None:
        self._entries.pop(feed_id, None)

    def __len__(self) -> int:
        return len(self._entries)


__all__ = ["CachedFeed", "FeedCache", "weak_etag"]
