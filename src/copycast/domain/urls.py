"""Source URL normalization for duplicate detection (a comparison key only)."""

from __future__ import annotations

import re
from typing import Final
from urllib.parse import urlsplit

TRACKING_PARAMS: Final = frozenset(
    {"fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src"}
)

_YOUTUBE_HOSTS: Final = frozenset({"youtube.com", "m.youtube.com", "music.youtube.com"})
_YOUTUBE_CHANNEL_PATH: Final = re.compile(r"^/(?:@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)/?$")
_YOUTUBE_TAB: Final = re.compile(
    r"^/(?:@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)/(videos|shorts|streams|live|podcasts|playlists|releases)/?$"
)


def _is_tracking(name: str) -> bool:
    lower = name.lower()
    return lower.startswith("utm_") or lower in TRACKING_PARAMS


def _canonical_query(raw_query: str) -> str:
    if not raw_query.strip():
        return ""
    parts = [p for p in raw_query.split("&") if p.strip()]
    kept = [p for p in parts if not _is_tracking(p.split("=", 1)[0])]
    return "&".join(sorted(kept))


def normalize_source_url(url: str | None) -> str:
    """Reduce a Source URL to its duplicate-detection key.

    Host lowercased minus one leading ``www.``; scheme, port, userinfo and
    fragment dropped; path case preserved with trailing slashes removed;
    query blanks and tracking parameters dropped, the rest sorted. Inputs
    without a host (or unparsable) fall back to the lowercased trimmed input.
    Never use the result for fetching: ``source_url`` stays untouched.
    """
    if url is None:
        return ""
    trimmed = url.strip()
    try:
        parts = urlsplit(trimmed)
        host = parts.hostname
    except ValueError:
        return trimmed.lower()
    if not host:
        return trimmed.lower()
    host = host.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    query = _canonical_query(parts.query)
    return f"{host}{path}{'?' + query if query else ''}"


def has_scheme(url: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url.strip()))


def scheme_candidates(url: str) -> list[str]:
    """URLs to try for user input: as given, or ``https://`` then ``http://`` when scheme-less."""
    text = url.strip()
    if has_scheme(text):
        return [text]
    text = text.lstrip("/")
    return [f"https://{text}", f"http://{text}"]


def youtube_host(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return host in _YOUTUBE_HOSTS


def normalize_youtube_channel(url: str) -> str:
    """A bare YouTube channel URL becomes its Videos tab; anything else is returned unchanged.

    ``https://www.youtube.com/@handle`` -> ``https://www.youtube.com/@handle/videos``.
    Tab URLs (``/shorts``, ``/streams``, ...) are kept verbatim so Shorts/Live
    are mirrored only when that tab URL is pasted.
    """
    text = url.strip()
    if not has_scheme(text) or not youtube_host(text):
        return text
    parts = urlsplit(text)
    if _YOUTUBE_CHANNEL_PATH.match(parts.path):
        base = text.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        return f"{base}/videos"
    return text


def youtube_tab(url: str) -> str | None:
    """The tab named by a YouTube channel tab URL (``videos``, ``shorts``, ...), else None."""
    if not has_scheme(url) or not youtube_host(url):
        return None
    match = _YOUTUBE_TAB.match(urlsplit(url).path)
    return match.group(1) if match else None
