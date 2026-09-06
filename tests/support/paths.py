"""URL paths the tests use so that no test composes a URL by hand."""

from __future__ import annotations

API_PREFIX = "/api"
MCP_PATH = "/mcp"
EVENTS_PATH = f"{API_PREFIX}/events"
ABOUT_PATH = f"{API_PREFIX}/about"

HEALTH_LIVE = "/healthz/live"
HEALTH_READY = "/healthz/ready"


def FEED_URL(feed_id: str) -> str:
    return f"/feeds/{feed_id}.xml"


def MEDIA_URL(feed_id: str, item_id: str, ext: str) -> str:
    return f"/feeds/{feed_id}/media/{item_id}.{ext.lstrip('.')}"


def ASSET_URL(feed_id: str, basename: str) -> str:
    return f"/feeds/{feed_id}/assets/{basename}"


def api(path: str) -> str:
    """``/api`` + path (``path`` starts with ``/``)."""
    return f"{API_PREFIX}{path}"
