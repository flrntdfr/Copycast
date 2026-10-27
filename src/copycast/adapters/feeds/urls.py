"""The public URLs of a feed; every URL Copycast publishes is composed here."""

from __future__ import annotations

from posixpath import basename
from urllib.parse import quote, urlsplit, urlunsplit

FEEDS_PREFIX = "/feeds"
KEY_SEGMENT = "k"
"""``/feeds/{id}/k/{key}/media/...``: a feed's media key in place of its Basic pair."""


def _base(base_url: str) -> str:
    return base_url.rstrip("/")


def with_credentials(url: str, username: str, password: str) -> str:
    """``https://user:pass@host/...``: the form podcast clients take a private feed in."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{quote(username, safe='')}:{quote(password, safe='')}@{host}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def feed_url(
    base_url: str, feed_id: str, *, username: str | None = None, password: str | None = None
) -> str:
    """``{base_url}/feeds/{id}.xml``, with ``user:pass@`` when a pair is given."""
    url = f"{_base(base_url)}{FEEDS_PREFIX}/{quote(feed_id, safe='')}.xml"
    if username is not None and password is not None:
        return with_credentials(url, username, password)
    return url


def _files(base_url: str, feed_id: str, key: str | None) -> str:
    """Where a feed's media and assets live: ``/feeds/{id}``, or ``/feeds/{id}/k/{key}``."""
    root = f"{_base(base_url)}{FEEDS_PREFIX}/{quote(feed_id, safe='')}"
    return root if key is None else f"{root}/{KEY_SEGMENT}/{quote(key, safe='')}"


def media_url(
    base_url: str, feed_id: str, item_id: str, ext: str, *, key: str | None = None
) -> str:
    """``{base_url}/feeds/{id}[/k/{key}]/media/{item_id}.{ext}``"""
    clean_ext = ext.lstrip(".")
    return (
        f"{_files(base_url, feed_id, key)}/media/"
        f"{quote(item_id, safe='')}.{quote(clean_ext, safe='')}"
    )


def asset_url(base_url: str, feed_id: str, local_path: str, *, key: str | None = None) -> str:
    """``{base_url}/feeds/{id}[/k/{key}]/assets/{basename}`` (``local_path`` may carry
    ``assets/``)."""
    name = basename(local_path)
    return f"{_files(base_url, feed_id, key)}/assets/{quote(name, safe='')}"


__all__ = ["FEEDS_PREFIX", "KEY_SEGMENT", "asset_url", "feed_url", "media_url", "with_credentials"]
