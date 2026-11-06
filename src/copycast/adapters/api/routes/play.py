"""``/api/feeds/{feed_id}/items/{item_id}/media|assets/{name}``: the web player's own routes.

A session, never a feed secret, opens them (plan U10): the browser plays an Episode
through ``ItemMedia.play_url`` and loads an item asset through ``AssetRead.play_url``.
Both look the item up with the Principal's services, so a Feed the caller cannot see
answers 404, and API keys are refused (capability-less routes, ``refuse_unfit_keys``).

- ``/media`` streams the archived object like ``/feeds/{id}/media/…`` does (Range,
  HEAD, ETag; ``Cache-Control: private, max-age=0``) and counts a play as a download by
  the same rule (ADR 0010, through SYSTEM's ``record_download``). It never archives on
  demand: an item without media is 404. Every answer names the file
  (``Content-Disposition: inline``, ``attachment`` with ``?download=1``) after the
  item's title and the archived media's own extension.
- ``/assets/{name}`` serves one of that item's archived assets by its file name, found
  only among ``get_item(…).assets``; anything else is 404. It is sandboxed
  (``Content-Security-Policy: sandbox``) unless it is audio, video or a PDF.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from fastapi import APIRouter, Request, Response
from starlette.background import BackgroundTask

from copycast.adapters.api.deps import ContainerDep, ServicesDep, SystemServicesDep
from copycast.adapters.api.media_response import (
    asset_object,
    content_disposition,
    counts_as_download,
    download_name,
    media_object,
)
from copycast.adapters.api.problems import problem_response, problem_responses
from copycast.adapters.api.security import PODCAST_POLICY, UNSANDBOXED_TYPES
from copycast.application.services import Services
from copycast.domain.enums import AssetState
from copycast.logging import get_logger

router = APIRouter(tags=["items"])
log = get_logger(__name__)

PLAY_CACHE_CONTROL: Final = "private, max-age=0"
"""The browser revalidates (the ETag answers 304 cheaply); no shared cache keeps it."""
MEDIA_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {"content": {"audio/*": {"schema": {"type": "string", "format": "binary"}}}},
    206: {"description": "The requested range"},
    **problem_responses(401, 404),
}


async def _record(system: Services, feed_id: str, item_id: str) -> None:
    try:
        await system.record_download(feed_id, item_id)
    except Exception as exc:
        log.warning("play.count_failed", feed_id=feed_id, item_id=item_id, error=str(exc))


async def play_item_media(
    request: Request,
    container: ContainerDep,
    services: ServicesDep,
    system: SystemServicesDep,
    feed_id: str,
    item_id: str,
    download: bool = False,
) -> Response:
    """The archived media of one item, for the web player (``?download=1``: a download)."""
    item = await services.get_item(feed_id, item_id)
    if item.media is None:
        return problem_response(
            request, status=404, slug="not-found", detail="no media archived for this item"
        )
    background: BackgroundTask | None = None
    if counts_as_download(request.method, request.headers.get("range")):
        background = BackgroundTask(_record, system, feed_id, item_id)
    disposition = content_disposition(download_name(item), attachment=download)
    response = await media_object(
        container.layout,
        feed_id,
        item,
        headers={"Cache-Control": PLAY_CACHE_CONTROL, "Content-Disposition": disposition},
        background=background,
    )
    if response is None:
        return problem_response(request, status=404, slug="not-found", detail="media missing")
    return response


async def play_item_asset(
    request: Request,
    container: ContainerDep,
    services: ServicesDep,
    feed_id: str,
    item_id: str,
    name: str,
) -> Response:
    """One archived asset of one item (chapters, transcript, Artwork, attachment)."""
    item = await services.get_item(feed_id, item_id)
    asset = next(
        (a for a in item.assets if a.name == name and a.state is AssetState.archived), None
    )
    if asset is None:
        return problem_response(request, status=404, slug="not-found", detail="no such asset")
    headers = {"Cache-Control": PLAY_CACHE_CONTROL, "X-Content-Type-Options": "nosniff"}
    if not (asset.mime or "").lower().startswith(UNSANDBOXED_TYPES):
        headers["Content-Security-Policy"] = PODCAST_POLICY
    response = await asset_object(container.layout, feed_id, asset, headers=headers)
    if response is None:
        return problem_response(request, status=404, slug="not-found", detail="asset missing")
    return response


def _get_and_head(path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
    """One GET operation in the schema plus an explicit HEAD kept out of it."""
    router.add_api_route(path, endpoint, methods=["GET"], response_class=Response, **kwargs)
    router.add_api_route(
        path, endpoint, methods=["HEAD"], response_class=Response, include_in_schema=False
    )


_get_and_head(
    "/feeds/{feed_id}/items/{item_id}/media",
    play_item_media,
    operation_id="play_item_media",
    responses=MEDIA_RESPONSES,
    summary="An item's archived media for the web player (session only; Range supported)",
)
_get_and_head(
    "/feeds/{feed_id}/items/{item_id}/assets/{name}",
    play_item_asset,
    operation_id="play_item_asset",
    responses={
        200: {"content": {"application/octet-stream": {"schema": {"type": "string"}}}},
        **problem_responses(401, 404),
    },
    summary="One archived asset of an item for the web player (session only)",
)


__all__ = ["PLAY_CACHE_CONTROL", "play_item_asset", "play_item_media", "router"]
