"""Podcast directory links (Apple Podcasts, Spotify) resolved to the feeds behind them.

Apple's lookup API answers a show id with its ``feedUrl``. Spotify never
exposes a feed: its show page names the show, and Apple's directory is
searched for that name, which is what getrssfeed.com does. Either way the
result is a list of candidate feed URLs the probe then verifies as RSS.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Final, cast
from urllib.parse import urlsplit

import httpx

from copycast.adapters.sources.http import USER_AGENT, SourceError, SourceUnreachable
from copycast.adapters.sources.itunes_search import search_podcasts

LOOKUP_URL: Final = "https://itunes.apple.com/lookup"
TIMEOUT_SECONDS: Final = 15.0
SPOTIFY_MATCH_LIMIT: Final = 5

_APPLE_ID_RE: Final = re.compile(r"/id(?P<id>\d+)(?:[/?#]|$)")
_OG_TITLE_RE: Final = re.compile(
    r'<meta\s+(?:property|name)=["\']og:title["\']\s+content=["\'](?P<title>[^"\']*)["\']', re.I
)
_TITLE_RE: Final = re.compile(r"<title[^>]*>(?P<title>[^<]*)</title>", re.I)


def apple_show_id(url: str) -> str | None:
    """The show id of an Apple Podcasts link (``podcasts.apple.com/.../id123``)."""
    parts = urlsplit(url if "://" in url else f"https://{url}")
    host = parts.netloc.lower()
    if not (host == "podcasts.apple.com" or host.endswith(".podcasts.apple.com")):
        return None
    match = _APPLE_ID_RE.search(parts.path)
    return match.group("id") if match else None


def spotify_show_url(url: str) -> str | None:
    """The canonical ``open.spotify.com/show/<id>`` of a Spotify show link, else None."""
    parts = urlsplit(url if "://" in url else f"https://{url}")
    if parts.netloc.lower() not in ("open.spotify.com", "spotify.com", "www.spotify.com"):
        return None
    segments = [s for s in parts.path.split("/") if s]
    # Locale prefixes such as /intl-fr/show/<id> are tolerated.
    for index, segment in enumerate(segments):
        if segment == "show" and index + 1 < len(segments):
            return f"https://open.spotify.com/show/{segments[index + 1]}"
    return None


def is_directory_link(url: str) -> bool:
    return apple_show_id(url) is not None or spotify_show_url(url) is not None


def _get(url: str, client: httpx.Client | None, **params: str) -> httpx.Response:
    try:
        if client is not None:
            response = client.get(url, params=params or None, timeout=TIMEOUT_SECONDS)
        else:
            with httpx.Client(
                headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS, follow_redirects=True
            ) as own:
                response = own.get(url, params=params or None)
    except httpx.HTTPError as exc:
        raise SourceUnreachable(f"{url}: {exc}") from exc
    if response.status_code >= 400:
        raise SourceError(f"{url} answered HTTP {response.status_code}")
    return response


def apple_feed_url(show_id: str, *, client: httpx.Client | None = None) -> str | None:
    """The ``feedUrl`` Apple's lookup API reports for a show id."""
    response = _get(LOOKUP_URL, client, id=show_id, entity="podcast")
    try:
        payload: object = json.loads(response.text)
    except ValueError as exc:
        raise SourceError("Apple's lookup answered something that is not JSON") from exc
    if not isinstance(payload, dict):
        return None
    results = cast(dict[str, Any], payload).get("results")
    if not isinstance(results, list):
        return None
    for entry in cast(list[object], results):
        if isinstance(entry, dict):
            feed = cast(dict[str, Any], entry).get("feedUrl")
            if isinstance(feed, str) and feed.strip():
                return feed.strip()
    return None


def spotify_show_title(show_url: str, *, client: httpx.Client | None = None) -> str | None:
    """The show's name from its Spotify page (``og:title``, else ``<title>``)."""
    text = _get(show_url, client).text
    match = _OG_TITLE_RE.search(text) or _TITLE_RE.search(text)
    if not match:
        return None
    title = html.unescape(match.group("title")).strip()
    for suffix in (" | Podcast on Spotify", " - Podcast on Spotify", " | Spotify"):
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    return title or None


def resolve_directory_link(url: str, *, client: httpx.Client | None = None) -> list[str] | None:
    """Feed URLs behind an Apple Podcasts or Spotify link; ``None`` when it is neither.

    An empty list means the link was recognised but nothing was found.
    """
    show_id = apple_show_id(url)
    if show_id is not None:
        feed = apple_feed_url(show_id, client=client)
        return [feed] if feed else []
    show_url = spotify_show_url(url)
    if show_url is not None:
        title = spotify_show_title(show_url, client=client)
        if not title:
            return []
        results = search_podcasts(title, SPOTIFY_MATCH_LIMIT, client=client)
        # An exact name match wins outright; otherwise every hit is a candidate.
        exact = [r.feed_url for r in results if r.title.strip().casefold() == title.casefold()]
        return exact[:1] if exact else [r.feed_url for r in results]
    return None


__all__ = [
    "LOOKUP_URL",
    "apple_feed_url",
    "apple_show_id",
    "is_directory_link",
    "resolve_directory_link",
    "spotify_show_title",
    "spotify_show_url",
]
