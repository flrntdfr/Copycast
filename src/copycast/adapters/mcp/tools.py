"""MCP tools: thin wrappers over the services, one per non-exempt capability.

Every tool sets ``meta={"capability": name}`` and takes the capability's
request model as one argument (plus path identifiers), so the convergence
test can hold the UI, the API and MCP to the same contract. Destructive tools
carry the MCP destructive hint.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from copycast.adapters.mcp.container import ServicesProvider
from copycast.adapters.mcp.errors import guarded, refuse
from copycast.application.capabilities import DESTRUCTIVE
from copycast.application.models import (
    AboutRead,
    FeedList,
    FeedRead,
    InboxCreate,
    InboxRead,
    InboxUpdate,
    ItemPage,
    ItemRead,
    JobPage,
    JobRead,
    MirrorCreate,
    MirrorRead,
    MirrorUpdate,
    PodcastSearchPage,
    ProbeRequest,
    ProbeResult,
    PruneRequest,
    PruneResult,
    RequestCreate,
    RequestRead,
    SelectionRequest,
    SelectionResult,
)
from copycast.domain.enums import (
    ArchiveState,
    FeedKind,
    JobKind,
    JobStatus,
    JobTrigger,
    RequestedVia,
)

DEFAULT_INBOX = "Copycast"
DELETE_FEED_WARNING = (
    "Refused: deleting a Feed removes its archived media, which may be the only copy. "
    "Call delete_feed again with confirm=true to proceed."
)


def _annotations(capability: str, *, read_only: bool = False) -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=read_only,
        destructive_hint=capability in DESTRUCTIVE,
        idempotent_hint=read_only,
        open_world_hint=False,
    )


ToolFn = Callable[..., Awaitable[Any]]


def register_tools(mcp: FastMCP[Any], container: ServicesProvider) -> None:
    """Define the tool bodies over ``container.services`` and register them all."""

    # ------------------------------------------------------------------ sources

    async def search_podcasts(query: str, limit: int = 10) -> PodcastSearchPage:
        """Find podcasts by name through the iTunes Search API (no key needed).

        ALWAYS call this first when asked to mirror a podcast by name; pass the
        chosen result's ``feed_url`` to ``create_mirror``.
        """
        return await container.services.search_podcasts(query, limit)

    async def probe_source(url: str) -> ProbeResult:
        """Resolve a feed, page, YouTube, SoundCloud or other Source URL into candidates.

        Never guess a Source: create the Mirror from one of the returned
        candidates (``candidate_token`` + ``source_url``).
        """
        return await container.services.probe_source(ProbeRequest(url=url))

    # ------------------------------------------------------------------ feeds

    async def create_mirror(mirror: MirrorCreate) -> MirrorRead:
        """Create a Mirror of a Source; returns the Mirror with its ``feed_url`` synchronously.

        ``backfill.mode``: ``all`` (everything), ``latest`` with ``latest_n``,
        or ``selection`` with ``selection`` such as ``"1-42, 180"`` (follow
        then defaults to false). Several candidates -> pass ``candidate_token``.
        """
        return await container.services.create_mirror(mirror, trigger=JobTrigger.mcp)

    async def list_feeds(kind: FeedKind | None = None) -> FeedList:
        """List Feeds (Mirrors and Inboxes), optionally one kind."""
        return await container.services.list_feeds(kind)

    async def get_feed(feed_id: str) -> FeedRead:
        """One Feed by id (ids are opaque; quote ``feed_url`` to the user)."""
        return await container.services.get_feed(feed_id)

    async def delete_feed(feed_id: str, confirm: bool = False) -> dict[str, Any]:
        """Delete a Feed and its archived media, which may be the only copy.

        Refuses unless ``confirm`` is true; the default Inbox cannot be deleted.
        """
        if not confirm:
            raise refuse(DELETE_FEED_WARNING, feed_id=feed_id)
        await container.services.delete_feed(feed_id)
        return {"deleted": True, "feed_id": feed_id}

    # ------------------------------------------------------------------ items

    async def list_items(
        feed_id: str,
        state: ArchiveState | None = None,
        listed: bool | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> ItemPage:
        """Page through a feed's Catalog (``source_number`` is what "Episode N" means)."""
        return await container.services.list_items(
            feed_id, state=state, listed=listed, q=query, limit=limit, offset=offset
        )

    async def get_item(feed_id: str, item_id: str) -> ItemRead:
        """One Catalog item with its media URL and assets."""
        return await container.services.get_item(feed_id, item_id)

    async def archive_episodes(feed_id: str, selection: SelectionRequest) -> SelectionResult:
        """Archive an explicit selection of Episodes exactly once.

        ``selection.selection`` is an expression such as ``"1-42, 180"`` in the
        Source's numbering (``numbering="ordinal"`` for Copycast ordinals);
        ``dry_run`` resolves without queuing anything.
        """
        return await container.services.select_items(feed_id, selection, trigger=JobTrigger.mcp)

    async def delete_item(feed_id: str, item_id: str) -> dict[str, Any]:
        """Delete one Episode's media.

        The item becomes Available again and is never re-archived automatically.
        """
        await container.services.delete_item(feed_id, item_id)
        return {"deleted": True, "feed_id": feed_id, "item_id": item_id}

    # ------------------------------------------------------------------ mirrors

    async def refresh_mirror(feed_id: str) -> JobRead | None:
        """Queue a Refresh of a Mirror now (ignores Paused and the fetch cooldown)."""
        return await container.services.request_refresh(feed_id, JobTrigger.manual)

    async def set_mirror_paused(feed_id: str, paused: bool) -> MirrorRead:
        """Pause or resume a Mirror; a Paused Mirror keeps serving its feed."""
        return await container.services.set_paused(feed_id, paused)

    async def update_mirror(feed_id: str, patch: MirrorUpdate) -> MirrorRead:
        """Change a Mirror's follow flag, backfill, engine options or Source URL."""
        return await container.services.update_mirror(feed_id, patch)

    # ------------------------------------------------------------------ inboxes

    async def create_inbox(inbox: InboxCreate) -> InboxRead:
        """Create an Inbox: a feed fed by Requests, prunable on demand or automatically."""
        return await container.services.create_inbox(inbox)

    async def update_inbox(inbox_id: str, patch: InboxUpdate) -> InboxRead:
        """Rename an Inbox or change ``autoprune_days`` (null switches autoprune off)."""
        return await container.services.update_inbox(inbox_id, patch)

    async def add_to_inbox(request: RequestCreate, inbox: str = DEFAULT_INBOX) -> RequestRead:
        """Push any URL (video, playlist, page) into an Inbox by id or name (default "Copycast")."""
        return await container.services.add_request(inbox, request, via=RequestedVia.mcp)

    async def prune_inbox(inbox: str, prune: PruneRequest) -> PruneResult:
        """Delete Inbox Episodes matching every given criterion.

        Criteria: ``downloaded`` (at least once), ``older_than_days`` (since
        added); ``dry_run`` previews the count and bytes.
        """
        return await container.services.prune_inbox(inbox, prune)

    # ------------------------------------------------------------------ jobs and about

    async def get_job(job_id: uuid.UUID) -> JobRead:
        """One job with its progress and outcome."""
        return await container.services.get_job(job_id)

    async def list_jobs(
        feed_id: str | None = None,
        kind: JobKind | None = None,
        status: JobStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> JobPage:
        """Jobs newest first, filtered by feed, kind or status."""
        return await container.services.list_jobs(
            feed_id=feed_id, kind=kind, status=status, limit=limit, offset=offset
        )

    async def cancel_job(job_id: uuid.UUID) -> JobRead:
        """Cancel a queued job or ask a running one to stop."""
        return await container.services.cancel_job(job_id)

    async def get_about() -> AboutRead:
        """Application and engine versions, layout version and totals."""
        return await container.services.about()

    registrations: list[tuple[ToolFn, str, str, bool]] = [
        (search_podcasts, "search_podcasts", "search_podcasts", True),
        (probe_source, "probe_source", "probe_source", True),
        (create_mirror, "create_mirror", "create_mirror", False),
        (list_feeds, "list_feeds", "list_feeds", True),
        (get_feed, "get_feed", "get_feed", True),
        (delete_feed, "delete_feed", "delete_feed", False),
        (list_items, "list_items", "list_items", True),
        (get_item, "get_item", "get_item", True),
        (archive_episodes, "archive_episodes", "select_items", False),
        (delete_item, "delete_item", "delete_item", False),
        (refresh_mirror, "refresh_mirror", "request_refresh", False),
        (set_mirror_paused, "set_mirror_paused", "set_paused", False),
        (update_mirror, "update_mirror", "update_mirror", False),
        (create_inbox, "create_inbox", "create_inbox", False),
        (update_inbox, "update_inbox", "update_inbox", False),
        (add_to_inbox, "add_to_inbox", "add_request", False),
        (prune_inbox, "prune_inbox", "prune_inbox", False),
        (get_job, "get_job", "get_job", True),
        (list_jobs, "list_jobs", "list_jobs", True),
        (cancel_job, "cancel_job", "cancel_job", False),
        (get_about, "get_about", "about", True),
    ]
    for fn, name, capability, read_only in registrations:
        mcp.tool(
            guarded(fn),
            name=name,
            meta={"capability": capability},
            annotations=_annotations(capability, read_only=read_only),
        )


__all__ = ["DEFAULT_INBOX", "DELETE_FEED_WARNING", "ToolFn", "register_tools"]
