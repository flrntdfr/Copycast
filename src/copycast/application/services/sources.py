"""Sources: probing a URL into candidates and keyless podcast search."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from copycast.application.capabilities import capability
from copycast.application.models import (
    PodcastSearchPage,
    ProbeRequest,
    ProbeResult,
    VideoSearchPage,
    YouTubePlaylistList,
)
from copycast.application.ports import CancelToken
from copycast.application.services.context import ServiceContext, SourceSnapshot
from copycast.domain.enums import FeedKind
from copycast.domain.exceptions import Unsupported
from copycast.domain.urls import youtube_playlist_id

PROBE_TIMEOUT_SECONDS = 300.0
SEARCH_DEFAULT_LIMIT = 10
SEARCH_MAX_LIMIT = 50


async def probe_snapshots(
    ctx: ServiceContext,
    url: str,
    *,
    cancel: CancelToken | None = None,
    deadline_seconds: float = PROBE_TIMEOUT_SECONDS,
) -> list[SourceSnapshot]:
    """Run the blocking probe off the loop; the token is set on timeout or disconnect."""
    token = cancel if cancel is not None else CancelToken()
    options: Mapping[str, Any] = ctx.settings.engine.options
    try:
        async with asyncio.timeout(deadline_seconds):
            return await asyncio.to_thread(ctx.sources.probe, url, options=options, cancel=token)
    except TimeoutError:
        token.cancel()
        raise Unsupported(
            f"probing {url} took longer than {int(deadline_seconds)} seconds"
        ) from None
    except asyncio.CancelledError:
        token.cancel()
        raise


@capability("probe_source", request=ProbeRequest, response=ProbeResult)
async def probe_source(
    ctx: ServiceContext, body: ProbeRequest, *, cancel: CancelToken | None = None
) -> ProbeResult:
    """Resolve a feed, page or Source URL into candidates (cached ten minutes)."""
    snapshots = await probe_snapshots(ctx, body.url, cancel=cancel)
    return ProbeResult(input_url=body.url, candidates=[s.candidate for s in snapshots])


@capability("search_podcasts", response=PodcastSearchPage)
async def search_podcasts(
    ctx: ServiceContext, query: str, limit: int = SEARCH_DEFAULT_LIMIT
) -> PodcastSearchPage:
    """Podcasts matching ``query`` through the iTunes Search API (no key needed)."""
    query = " ".join(query.split())
    limit = max(1, min(int(limit), SEARCH_MAX_LIMIT))
    if not query:
        return PodcastSearchPage(query=query, results=[])
    results = await asyncio.to_thread(ctx.sources.search_podcasts, query, limit)
    return PodcastSearchPage(query=query, results=results)


WATCH_LATER_URL = "https://www.youtube.com/playlist?list=WL"


@capability("list_youtube_playlists", response=YouTubePlaylistList)
async def list_youtube_playlists(ctx: ServiceContext) -> YouTubePlaylistList:
    """The signed-in account's playlists (Watch Later first), with the Mirror capturing each.

    Needs the cookie file: without it YouTube shows no account, and the engine's
    error is reported as-is.
    """
    playlists = await asyncio.to_thread(ctx.sources.list_playlists)
    async with ctx.uow_factory() as uow:
        mirrors = await uow.feeds.list(FeedKind.mirror)
    by_list_id = {
        playlist_id: feed.id
        for feed in mirrors
        if feed.source_url and (playlist_id := youtube_playlist_id(feed.source_url))
    }
    marked = [
        playlist.model_copy(update={"captured_feed_id": by_list_id.get(playlist.id)})
        for playlist in playlists
    ]
    return YouTubePlaylistList(playlists=marked)


@capability("search_videos", response=VideoSearchPage)
async def search_videos(
    ctx: ServiceContext, query: str, limit: int = SEARCH_DEFAULT_LIMIT
) -> VideoSearchPage:
    """Videos matching ``query`` on YouTube through the engine (``ytsearch``); no key needed."""
    query = " ".join(query.split())
    limit = max(1, min(int(limit), SEARCH_MAX_LIMIT))
    if not query:
        return VideoSearchPage(query=query, results=[])
    results = await asyncio.to_thread(ctx.sources.search_videos, query, limit)
    return VideoSearchPage(query=query, results=results)


__all__ = [
    "PROBE_TIMEOUT_SECONDS",
    "SEARCH_DEFAULT_LIMIT",
    "SEARCH_MAX_LIMIT",
    "WATCH_LATER_URL",
    "list_youtube_playlists",
    "probe_snapshots",
    "probe_source",
    "search_podcasts",
    "search_videos",
]
