"""The public URLs of a feed; every URL Copycast publishes is composed here."""

from __future__ import annotations

from posixpath import basename
from urllib.parse import quote, urlsplit, urlunsplit

FEEDS_PREFIX = "/feeds"


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


def media_url(base_url: str, feed_id: str, item_id: str, ext: str) -> str:
    """``{base_url}/feeds/{id}/media/{item_id}.{ext}``"""
    clean_ext = ext.lstrip(".")
    return (
        f"{_base(base_url)}{FEEDS_PREFIX}/{quote(feed_id, safe='')}/media/"
        f"{quote(item_id, safe='')}.{quote(clean_ext, safe='')}"
    )


def asset_url(base_url: str, feed_id: str, local_path: str) -> str:
    """``{base_url}/feeds/{id}/assets/{basename}`` (``local_path`` may carry ``assets/``)."""
    name = basename(local_path)
    return (
        f"{_base(base_url)}{FEEDS_PREFIX}/{quote(feed_id, safe='')}/assets/{quote(name, safe='')}"
    )


__all__ = ["FEEDS_PREFIX", "asset_url", "feed_url", "media_url", "with_credentials"]
