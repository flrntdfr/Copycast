"""Feed-link discovery in HTML pages.

A regex scan of the first 256 KiB for ``<link rel="alternate"
type="application/rss+xml" href=...>`` in document order, deduplicated and
resolved against the page's final URL. Atom links are ignored: Copycast
mirrors RSS only.
"""

from __future__ import annotations

import html
import re
from typing import Final
from urllib.parse import urljoin

from copycast.adapters.sources.http import MAX_HTML_BYTES

RSS_TYPES: Final = frozenset({"application/rss+xml", "application/rss"})

_LINK_TAG: Final = re.compile(rb"<link\b[^>]*>", re.IGNORECASE | re.DOTALL)
_ATTR: Final = re.compile(
    rb"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))""",
    re.DOTALL,
)
_HTML_HINT: Final = re.compile(rb"<!doctype\s+html|<html\b|<head\b|<body\b|<link\b", re.IGNORECASE)


def _attributes(tag_bytes: bytes) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in _ATTR.finditer(tag_bytes):
        name = match.group(1).decode("ascii", "ignore").lower()
        raw = match.group(2) or match.group(3) or match.group(4) or b""
        attrs.setdefault(name, html.unescape(raw.decode("utf-8", "replace")).strip())
    return attrs


def discover_feed_links(document: bytes | str, base_url: str) -> list[str]:
    """Every advertised RSS feed URL in document order, without duplicates."""
    data = document.encode("utf-8", "replace") if isinstance(document, str) else document
    data = data[:MAX_HTML_BYTES]
    found: list[str] = []
    seen: set[str] = set()
    for match in _LINK_TAG.finditer(data):
        attrs = _attributes(match.group(0))
        rel = {token for token in attrs.get("rel", "").lower().split() if token}
        media_type = attrs.get("type", "").split(";", 1)[0].strip().lower()
        href = attrs.get("href", "")
        if "alternate" not in rel or media_type not in RSS_TYPES or not href:
            continue
        resolved = urljoin(base_url, href)
        if resolved in seen:
            continue
        seen.add(resolved)
        found.append(resolved)
    return found


def looks_like_html(document: bytes, content_type: str | None) -> bool:
    if content_type and content_type in {"text/html", "application/xhtml+xml"}:
        return True
    return _HTML_HINT.search(document[:4096]) is not None


__all__ = ["RSS_TYPES", "discover_feed_links", "looks_like_html"]
