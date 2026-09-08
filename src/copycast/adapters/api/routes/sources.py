"""Sources: probe a URL into candidates, search podcasts and videos by name."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from copycast.adapters.api.deps import ServicesDep, run_cancellable
from copycast.adapters.api.problems import problem_responses
from copycast.application.models import (
    PodcastSearchPage,
    ProbeRequest,
    ProbeResult,
    VideoSearchPage,
    YouTubePlaylistList,
)
from copycast.application.ports import CancelToken

router = APIRouter(tags=["sources"])

MAX_SEARCH_LIMIT = 50


@router.post(
    "/probe",
    operation_id="probe_source",
    openapi_extra={"x-capability": "probe_source"},
    response_model=ProbeResult,
    responses=problem_responses(422),
    summary="Resolve a feed, page or Source URL into candidates",
)
async def probe_source(services: ServicesDep, request: Request, body: ProbeRequest) -> ProbeResult:
    async def _probe(cancel: CancelToken) -> ProbeResult:
        return await services.probe_source(body, cancel=cancel)

    return await run_cancellable(request, _probe)


@router.get(
    "/search/podcasts",
    operation_id="search_podcasts",
    openapi_extra={"x-capability": "search_podcasts"},
    response_model=PodcastSearchPage,
    responses=problem_responses(503),
    summary="Find podcasts by name (iTunes Search)",
)
async def search_podcasts(
    services: ServicesDep,
    query: Annotated[str, Query(max_length=200)],
    limit: Annotated[int, Query(ge=1, le=MAX_SEARCH_LIMIT)] = 10,
) -> PodcastSearchPage:
    return await services.search_podcasts(query, limit)


@router.get(
    "/youtube/playlists",
    operation_id="list_youtube_playlists",
    openapi_extra={"x-capability": "list_youtube_playlists"},
    response_model=YouTubePlaylistList,
    responses=problem_responses(422, 503),
    summary="The signed-in YouTube account's playlists (needs the cookie file), Watch Later first",
)
async def list_youtube_playlists(services: ServicesDep) -> YouTubePlaylistList:
    return await services.list_youtube_playlists()


@router.get(
    "/search/videos",
    operation_id="search_videos",
    openapi_extra={"x-capability": "search_videos"},
    response_model=VideoSearchPage,
    responses=problem_responses(503),
    summary="Find videos by name (YouTube, through the engine)",
)
async def search_videos(
    services: ServicesDep,
    query: Annotated[str, Query(max_length=200)],
    limit: Annotated[int, Query(ge=1, le=MAX_SEARCH_LIMIT)] = 10,
) -> VideoSearchPage:
    return await services.search_videos(query, limit)


__all__ = ["MAX_SEARCH_LIMIT", "router"]
