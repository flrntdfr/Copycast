"""The public URLs of a feed; every URL Copycast publishes is composed here."""

from __future__ import annotations

from posixpath import basename
from urllib.parse import quote

FEEDS_PREFIX = "/feeds"


def _base(base_url: str) -> str:
    return base_url.rstrip("/")


def feed_url(base_url: str, feed_id: str) -> str:
    """``{base_url}/feeds/{id}.xml``"""
    return f"{_base(base_url)}{FEEDS_PREFIX}/{quote(feed_id, safe='')}.xml"


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


__all__ = ["FEEDS_PREFIX", "asset_url", "feed_url", "media_url"]
