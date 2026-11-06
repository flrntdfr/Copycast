"""``/api/feeds/{feed_id}/items/{item_id}/media|assets/{name}``: the web player's own routes.

A session, never a feed secret, opens them: the browser plays an Episode through
``ItemMedia.play_url`` and loads an item asset through ``AssetRead.play_url``. The
routes arrive with the quota work; the router is registered now so the OpenAPI order
is fixed.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["items"])

__all__ = ["router"]
