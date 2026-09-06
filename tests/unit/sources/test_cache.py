from __future__ import annotations

from copycast.adapters.sources.cache import TtlCache


def test_entries_expire_after_ttl() -> None:
    now = [0.0]
    cache: TtlCache[str, int] = TtlCache(10, clock=lambda: now[0])
    cache.put("a", 1)
    assert cache.get("a") == 1 and "a" in cache and len(cache) == 1
    now[0] = 9.9
    assert cache.get("a") == 1
    now[0] = 10.0
    assert cache.get("a") is None and "a" not in cache
    assert len(cache) == 0


def test_pop_purge_clear_and_capacity() -> None:
    now = [0.0]
    cache: TtlCache[int, str] = TtlCache(5, max_entries=2, clock=lambda: now[0])
    cache.put(1, "one")
    cache.put(2, "two")
    cache.put(3, "three")
    assert cache.get(1) is None and cache.get(3) == "three"
    assert cache.pop(2) == "two" and cache.pop(2) is None
    now[0] = 6
    cache.put(4, "four")
    assert cache.purge() == 0
    assert len(cache) == 1
    cache.clear()
    assert len(cache) == 0
