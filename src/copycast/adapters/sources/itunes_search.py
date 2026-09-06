"""Keyless podcast search through the iTunes Search API."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final, cast

import httpx

from copycast.adapters.sources.cache import TtlCache
from copycast.adapters.sources.http import (
    USER_AGENT,
    SourceRejected,
    SourceUnreachable,
)
from copycast.application.models import PodcastSearchResult

SEARCH_URL: Final = "https://itunes.apple.com/search"
TIMEOUT_SECONDS: Final = 15.0
CACHE_TTL_SECONDS: Final = 600.0
DEFAULT_LIMIT: Final = 10
MAX_LIMIT: Final = 50
MAX_RESPONSE_BYTES: Final = 4 * 1024 * 1024

_ARTWORK_KEYS: Final = ("artworkUrl600", "artworkUrl100", "artworkUrl60", "artworkUrl30")

_cache: TtlCache[tuple[str, int], list[PodcastSearchResult]] = TtlCache(CACHE_TTL_SECONDS)


def cache_key(query: str, limit: int) -> tuple[str, int]:
    return " ".join(query.lower().split()), limit


def search_podcasts(
    query: str,
    limit: int = DEFAULT_LIMIT,
    *,
    client: httpx.Client | None = None,
    cache: TtlCache[tuple[str, int], list[PodcastSearchResult]] | None = None,
) -> list[PodcastSearchResult]:
    """Podcasts matching ``query``; results without a ``feedUrl`` are dropped.

    Answers are cached for ten minutes per (normalized query, limit).
    Network failures raise :class:`SourceUnreachable`; a 4xx raises
    :class:`SourceRejected`.
    """
    limit = max(1, min(int(limit), MAX_LIMIT))
    key = cache_key(query, limit)
    store = cache if cache is not None else _cache
    cached = store.get(key)
    if cached is not None:
        return list(cached)
    if not key[0]:
        return []
    params = {"media": "podcast", "entity": "podcast", "limit": str(limit), "term": key[0]}
    try:
        if client is not None:
            response = client.get(SEARCH_URL, params=params, timeout=TIMEOUT_SECONDS)
        else:
            with httpx.Client(
                headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS, follow_redirects=True
            ) as own:
                response = own.get(SEARCH_URL, params=params)
    except httpx.HTTPError as exc:
        raise SourceUnreachable(f"iTunes Search: {exc}") from exc
    if response.status_code >= 500:
        raise SourceUnreachable(f"iTunes Search answered HTTP {response.status_code}")
    if response.status_code >= 400:
        raise SourceRejected(SEARCH_URL, response.status_code)
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise SourceUnreachable("iTunes Search answer too large")
    try:
        payload = json.loads(response.content)
    except ValueError as exc:
        raise SourceUnreachable("iTunes Search answered with invalid JSON") from exc
    results = parse_results(payload)[:limit]
    store.put(key, results)
    return list(results)


def parse_results(payload: object) -> list[PodcastSearchResult]:
    if not isinstance(payload, Mapping):
        return []
    raw_results = cast(Mapping[str, Any], payload).get("results")
    if not isinstance(raw_results, list):
        return []
    parsed: list[PodcastSearchResult] = []
    for entry in cast(list[object], raw_results):
        if isinstance(entry, Mapping):
            result = parse_result(cast(Mapping[str, Any], entry))
            if result is not None:
                parsed.append(result)
    return parsed


def parse_result(entry: Mapping[str, Any]) -> PodcastSearchResult | None:
    feed_url = _str(entry.get("feedUrl"))
    if not feed_url:
        return None
    title = _str(entry.get("collectionName")) or _str(entry.get("trackName")) or feed_url
    itunes_id = entry.get("collectionId") or entry.get("trackId")
    genre = _str(entry.get("primaryGenreName"))
    if genre is None:
        genres = entry.get("genres")
        if isinstance(genres, list) and genres and isinstance(genres[0], str):
            genre = genres[0]
    return PodcastSearchResult(
        title=title,
        author=_str(entry.get("artistName")),
        feed_url=feed_url,
        artwork_url=next(
            (_str(entry.get(key)) for key in _ARTWORK_KEYS if _str(entry.get(key))), None
        ),
        itunes_id=itunes_id
        if isinstance(itunes_id, int) and not isinstance(itunes_id, bool)
        else None,
        episode_count=(
            entry["trackCount"]
            if isinstance(entry.get("trackCount"), int)
            and not isinstance(entry.get("trackCount"), bool)
            else None
        ),
        genre=genre,
        latest_release_at=_date(entry.get("releaseDate")),
    )


def _str(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _date(value: object) -> datetime | None:
    text = _str(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def clear_cache() -> None:
    _cache.clear()


__all__ = [
    "CACHE_TTL_SECONDS",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "SEARCH_URL",
    "TIMEOUT_SECONDS",
    "cache_key",
    "clear_cache",
    "parse_result",
    "parse_results",
    "search_podcasts",
]
