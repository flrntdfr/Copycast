"""Application services: one function per capability, bound by :func:`build_services`.

Importing this package registers every capability in
``application.capabilities.CAPABILITIES``. Routes and MCP tools call the
bound methods of :class:`Services`; the worker calls the same functions for
the operations it shares with the API (Refresh requests, the default Inbox,
autoprune).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from copycast.application.capabilities import CAPABILITIES, CAPABILITY_NAMES
from copycast.application.models import (
    AboutRead,
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyList,
    ApiKeyRead,
    EngineCookiesRead,
    EngineCookiesWrite,
    FeedList,
    FeedRead,
    InboxCreate,
    InboxRead,
    InboxUpdate,
    ItemPage,
    ItemRead,
    JobPage,
    JobRead,
    MirrorChangePreview,
    MirrorCreate,
    MirrorDefaults,
    MirrorRead,
    MirrorUpdate,
    PodcastSearchPage,
    ProbeRequest,
    ProbeResult,
    PruneRequest,
    PruneResult,
    PurgeRequest,
    RebuildRequest,
    RequestCreate,
    RequestPage,
    RequestRead,
    SelectionRequest,
    SelectionResult,
    VideoSearchPage,
    YouTubePlaylistList,
)
from copycast.application.ports import CancelToken
from copycast.application.services import about as _about
from copycast.application.services import defaults as _defaults
from copycast.application.services import engine as _engine
from copycast.application.services import feeds as _feeds
from copycast.application.services import inboxes as _inboxes
from copycast.application.services import items as _items
from copycast.application.services import jobs as _jobs
from copycast.application.services import keys as _keys
from copycast.application.services import mirrors as _mirrors
from copycast.application.services import policy as _policy
from copycast.application.services import purge as _purge
from copycast.application.services import sources as _sources
from copycast.application.services.context import (
    FeedSort,
    ItemSort,
    Order,
    ServiceContext,
)
from copycast.application.services.episodes import DeletedEpisode, delete_episode
from copycast.domain.enums import (
    ArchiveState,
    FeedKind,
    JobKind,
    JobStatus,
    JobTrigger,
    RequestedVia,
)


@dataclass(frozen=True, slots=True)
class Services:
    """Every capability bound to one container (``container.services``)."""

    ctx: ServiceContext

    # feeds
    async def list_feeds(
        self, kind: FeedKind | None = None, *, sort: FeedSort = "title", order: Order = "asc"
    ) -> FeedList:
        return await _feeds.list_feeds(self.ctx, kind, sort=sort, order=order)

    async def get_feed(self, feed_id: str) -> FeedRead:
        return await _feeds.get_feed(self.ctx, feed_id)

    async def delete_feed(self, feed_id: str) -> None:
        await _feeds.delete_feed(self.ctx, feed_id)

    async def export_opml(self) -> str:
        return await _feeds.export_opml(self.ctx)

    async def rotate_feed_credentials(self, feed_id: str) -> FeedRead:
        return await _feeds.rotate_feed_credentials(self.ctx, feed_id)

    async def ensure_default_inbox(self) -> InboxRead:
        return await _feeds.ensure_default_inbox(self.ctx)

    async def resolve_inbox(self, ident: str) -> InboxRead:
        return await _feeds.resolve_inbox(self.ctx, ident)

    # items
    async def list_items(
        self,
        feed_id: str,
        *,
        state: ArchiveState | Sequence[ArchiveState] | None = None,
        listed: bool | None = None,
        q: str | None = None,
        sort: ItemSort = "published",
        order: Order = "desc",
        limit: int = 100,
        offset: int = 0,
    ) -> ItemPage:
        return await _items.list_items(
            self.ctx,
            feed_id,
            state=state,
            listed=listed,
            q=q,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )

    async def get_item(self, feed_id: str, item_id: str) -> ItemRead:
        return await _items.get_item(self.ctx, feed_id, item_id)

    async def archive_item(
        self, feed_id: str, item_id: str, *, trigger: JobTrigger = JobTrigger.manual
    ) -> JobRead:
        return await _items.archive_item(self.ctx, feed_id, item_id, trigger=trigger)

    async def fetch_item_metadata(self, feed_id: str, item_id: str) -> ItemRead:
        return await _items.fetch_item_metadata(self.ctx, feed_id, item_id)

    async def delete_item(self, feed_id: str, item_id: str) -> None:
        await _items.delete_item(self.ctx, feed_id, item_id)

    async def record_download(self, feed_id: str, item_id: str) -> bool:
        return await _items.record_download(self.ctx, feed_id, item_id)

    # sources
    async def probe_source(
        self, body: ProbeRequest, *, cancel: CancelToken | None = None
    ) -> ProbeResult:
        return await _sources.probe_source(self.ctx, body, cancel=cancel)

    async def search_podcasts(self, query: str, limit: int = 10) -> PodcastSearchPage:
        return await _sources.search_podcasts(self.ctx, query, limit)

    async def list_youtube_playlists(self) -> YouTubePlaylistList:
        return await _sources.list_youtube_playlists(self.ctx)

    async def search_videos(self, query: str, limit: int = 10) -> VideoSearchPage:
        return await _sources.search_videos(self.ctx, query, limit)

    # mirrors
    async def create_mirror(
        self,
        body: MirrorCreate,
        *,
        trigger: JobTrigger = JobTrigger.ui,
        cancel: CancelToken | None = None,
    ) -> MirrorRead:
        return await _mirrors.create_mirror(self.ctx, body, trigger=trigger, cancel=cancel)

    async def update_mirror(
        self, feed_id: str, body: MirrorUpdate, *, cancel: CancelToken | None = None
    ) -> MirrorRead:
        return await _mirrors.update_mirror(self.ctx, feed_id, body, cancel=cancel)

    async def set_paused(self, feed_id: str, paused: bool) -> MirrorRead:
        return await _mirrors.set_paused(self.ctx, feed_id, paused)

    async def request_refresh(
        self, feed_id: str, trigger: JobTrigger = JobTrigger.manual
    ) -> JobRead | None:
        return await _mirrors.request_refresh(self.ctx, feed_id, trigger)

    async def select_items(
        self, feed_id: str, body: SelectionRequest, *, trigger: JobTrigger = JobTrigger.ui
    ) -> SelectionResult:
        return await _mirrors.select_items(self.ctx, feed_id, body, trigger=trigger)

    async def preview_mirror_update(self, feed_id: str, body: MirrorUpdate) -> MirrorChangePreview:
        return await _policy.preview_mirror_update(self.ctx, feed_id, body)

    async def archive_available(
        self, feed_id: str, *, trigger: JobTrigger = JobTrigger.ui
    ) -> SelectionResult:
        return await _mirrors.archive_available(self.ctx, feed_id, trigger=trigger)

    async def retry_failed(
        self, feed_id: str, *, trigger: JobTrigger = JobTrigger.ui
    ) -> SelectionResult:
        return await _mirrors.retry_failed(self.ctx, feed_id, trigger=trigger)

    # inboxes and requests
    async def create_inbox(self, body: InboxCreate) -> InboxRead:
        return await _inboxes.create_inbox(self.ctx, body)

    async def update_inbox(self, inbox: str, body: InboxUpdate) -> InboxRead:
        return await _inboxes.update_inbox(self.ctx, inbox, body)

    async def add_request(
        self, inbox: str, body: RequestCreate, *, via: RequestedVia = RequestedVia.ui
    ) -> RequestRead:
        return await _inboxes.add_request(self.ctx, inbox, body, via=via)

    async def list_requests(self, inbox: str, *, limit: int = 100, offset: int = 0) -> RequestPage:
        return await _inboxes.list_requests(self.ctx, inbox, limit=limit, offset=offset)

    async def get_request(self, inbox: str, request_id: uuid.UUID) -> RequestRead:
        return await _inboxes.get_request(self.ctx, inbox, request_id)

    async def prune_inbox(self, inbox: str, body: PruneRequest) -> PruneResult:
        return await _inboxes.prune_inbox(self.ctx, inbox, body)

    async def autoprune(self, feed_id: str) -> PruneResult:
        return await _inboxes.autoprune(self.ctx, feed_id)

    # jobs, about, admin
    async def list_jobs(
        self,
        *,
        feed_id: str | None = None,
        kind: JobKind | None = None,
        status: JobStatus | Sequence[JobStatus] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> JobPage:
        return await _jobs.list_jobs(
            self.ctx, feed_id=feed_id, kind=kind, status=status, limit=limit, offset=offset
        )

    async def get_job(self, job_id: uuid.UUID) -> JobRead:
        return await _jobs.get_job(self.ctx, job_id)

    async def cancel_job(self, job_id: uuid.UUID) -> JobRead:
        return await _jobs.cancel_job(self.ctx, job_id)

    async def rebuild(self, body: RebuildRequest) -> JobRead:
        return await _jobs.rebuild(self.ctx, body)

    async def purge_episodes(self, body: PurgeRequest) -> PruneResult:
        return await _purge.purge_episodes(self.ctx, body)

    async def about(self) -> AboutRead:
        return await _about.about(self.ctx)

    # api keys
    async def list_api_keys(self) -> ApiKeyList:
        return await _keys.list_api_keys(self.ctx)

    async def create_api_key(self, body: ApiKeyCreate) -> ApiKeyCreated:
        return await _keys.create_api_key(self.ctx, body)

    async def revoke_api_key(self, key_id: uuid.UUID) -> None:
        await _keys.revoke_api_key(self.ctx, key_id)

    async def authenticate_api_key(self, secret: str) -> ApiKeyRead | None:
        return await _keys.authenticate_api_key(self.ctx, secret)

    # mirror defaults
    async def get_mirror_defaults(self) -> MirrorDefaults:
        return await _defaults.get_mirror_defaults(self.ctx)

    async def set_mirror_defaults(self, body: MirrorDefaults) -> MirrorDefaults:
        return await _defaults.set_mirror_defaults(self.ctx, body)

    # engine cookies
    async def get_engine_cookies(self) -> EngineCookiesRead:
        return await _engine.get_engine_cookies(self.ctx)

    async def set_engine_cookies(self, body: EngineCookiesWrite) -> EngineCookiesRead:
        return await _engine.set_engine_cookies(self.ctx, body)

    async def delete_engine_cookies(self) -> None:
        await _engine.delete_engine_cookies(self.ctx)


def build_services(container: ServiceContext) -> Services:
    """Bind every capability to ``container`` (``copycast.app.Container.services``)."""
    missing = sorted(CAPABILITY_NAMES - set(CAPABILITIES) - {"subscribe_events"})
    if missing:  # pragma: no cover - a service module forgot its decorator
        raise RuntimeError(f"capabilities without a service: {missing}")
    return Services(container)


__all__ = [
    "DeletedEpisode",
    "Services",
    "build_services",
    "delete_episode",
]
