"""MCP tools: thin wrappers over the services, one per non-exempt capability.

Every tool sets ``meta={"capability": name}`` and takes the capability's
request model as one argument (plus path identifiers), so the convergence
test can hold the UI, the API and MCP to the same contract. Destructive tools
carry the MCP destructive hint, and every tool checks the calling key's scope
first (read-only tools need ``read``, destructive ones ``full``, the rest
``write``; no key means authentication is off).
"""

import functools
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from copycast.adapters.mcp.auth import check_scope, current_principal
from copycast.adapters.mcp.container import ServicesProvider
from copycast.adapters.mcp.errors import guarded, refuse
from copycast.application.capabilities import ACCESS, DESTRUCTIVE
from copycast.application.models import (
    AboutRead,
    ArchiveFlagUpdate,
    BucketCreate,
    BucketRead,
    BucketUpdate,
    ClearFeedRequest,
    ClearFeedResult,
    FeedList,
    FeedObjectList,
    FeedRead,
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
    SerialBucketCreate,
    StatusRead,
    StorageSummary,
    UploadCreate,
    UploadRead,
    UploadsAdd,
    VideoSearchPage,
    YouTubePlaylistList,
)
from copycast.application.services import Services
from copycast.domain.credentials import required_scope
from copycast.domain.enums import (
    ArchiveState,
    FeedKind,
    JobKind,
    JobStatus,
    JobTrigger,
    KeyScope,
    RequestedVia,
)

DEFAULT_BUCKET = "Copycast"
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


_SERVICES: ContextVar[Services] = ContextVar("copycast_mcp_services")
"""The services of the current tool call, acting as its Principal (set by :func:`with_scope`)."""


def services() -> Services:
    """The current tool call's services: the calling key's owner's, or open mode's Admin's."""
    try:
        return _SERVICES.get()
    except LookupError:
        raise RuntimeError("an MCP tool body ran outside with_scope") from None


def with_scope(
    fn: ToolFn, name: str, scope: KeyScope, container: ServicesProvider | None = None
) -> ToolFn:
    """Check the calling key's scope, then run the tool body (signature preserved) with
    :func:`services` bound to the call's Principal when ``container`` is given."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        check_scope(scope, name)
        if container is None:
            return await fn(*args, **kwargs)
        reset = _SERVICES.set(container.services_for(current_principal(container)))
        try:
            return await fn(*args, **kwargs)
        finally:
            _SERVICES.reset(reset)

    return wrapper


def register_tools(mcp: FastMCP[Any], container: ServicesProvider) -> None:
    """Define the tool bodies over the calling Principal's services and register them all."""

    # ------------------------------------------------------------------ sources

    async def search_podcasts(query: str, limit: int = 10) -> PodcastSearchPage:
        """Find podcasts by name through the iTunes Search API (no key needed).

        ALWAYS call this first when asked to mirror a podcast by name; pass the
        chosen result's ``feed_url`` to ``create_mirror``.
        """
        return await services().search_podcasts(query, limit)

    async def list_youtube_playlists() -> YouTubePlaylistList:
        """The signed-in YouTube account's playlists, Watch Later first (needs the cookie file).

        Each ``url`` can go to ``create_mirror`` with ``sync_deletions`` and
        ``playlist_capture`` true to capture it among the Buckets.
        """
        return await services().list_youtube_playlists()

    async def search_videos(query: str, limit: int = 10) -> VideoSearchPage:
        """Find videos by name on YouTube (no key needed); each hit's ``url`` can be pushed
        into a Bucket with ``add_to_bucket`` or probed with ``probe_source``.

        For "add the last X from channel Y" search for the channel and the topic, pick
        the newest matching hit by ``published_at``, then call ``add_to_bucket``.
        """
        return await services().search_videos(query, limit)

    async def probe_source(url: str) -> ProbeResult:
        """Resolve a feed, page, YouTube, SoundCloud or other Source URL into candidates.

        Never guess a Source: create the Mirror from one of the returned
        candidates (``candidate_token`` + ``source_url``).
        """
        return await services().probe_source(ProbeRequest(url=url))

    # ------------------------------------------------------------------ feeds

    async def create_mirror(mirror: MirrorCreate) -> MirrorRead:
        """Create a Mirror of a Source; returns the Mirror with its ``feed_url`` synchronously.

        ``backfill`` absent: the operator's default policy (Ephemeral unless changed on
        the Settings page). ``backfill.mode``: the Archive policy is ``all`` (everything
        listed, then every new item) or ``new`` (only what the Source publishes from now
        on), and it locks every Episode it archives unless ``sync_deletions`` is on; the
        Ephemeral policy is ``automatic``
        (downloaded when a podcast app first asks, expiring ``retention_days`` after the
        last download, null keeps forever).
        Also ``rolling`` with ``latest_n`` (only the newest N stay archived) and
        ``selection`` with ``selection`` such as ``"1-42, 180"`` (follow then defaults to
        false). Rolling and expiring modes delete on their own, so they need a ``full``
        key. Several candidates -> pass ``candidate_token``. ``engine_options`` (allowlisted
        yt-dlp params) also need a ``full`` key.
        """
        return await services().create_mirror(mirror, trigger=JobTrigger.mcp)

    async def list_feeds(kind: FeedKind | None = None) -> FeedList:
        """List Feeds (Mirrors and Buckets), optionally one kind."""
        return await services().list_feeds(kind)

    async def get_feed(feed_id: str) -> FeedRead:
        """One Feed by id (ids are opaque; quote ``feed_url`` to the user)."""
        return await services().get_feed(feed_id)

    async def list_feed_objects(feed_id: str) -> FeedObjectList:
        """Every object a Feed keeps in the Object store (media, assets, descriptor, Source
        copies, sidecars) with its kind, size and download URL; an Episode's files name it
        and carry its Lock (``locked``; null for the feed's own files)."""
        return await services().list_feed_objects(feed_id)

    async def storage_summary() -> StorageSummary:
        """How many files each of your own Feeds keeps in its bucket and their total size
        (one listing of the Object store), plus the totals; a Feed that keeps nothing
        counts zeros."""
        return await services().storage_summary()

    async def delete_feed(feed_id: str, confirm: bool = False) -> dict[str, Any]:
        """Delete a Feed and its archived media, which may be the only copy.

        Refuses unless ``confirm`` is true; the default Bucket cannot be deleted.
        """
        if not confirm:
            raise refuse(DELETE_FEED_WARNING, feed_id=feed_id)
        await services().delete_feed(feed_id)
        return {"deleted": True, "feed_id": feed_id}

    async def clear_feed(feed_id: str, clear: ClearFeedRequest) -> ClearFeedResult:
        """Purge a Feed's bucket: delete every archived Episode that is not locked.

        Locked Episodes (``archive_flag``) stay, as do the Feed, its Catalog and its own
        files. ``dry_run`` (the default) counts what would go and what stays.
        """
        return await services().clear_feed(feed_id, clear)

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
        return await services().list_items(
            feed_id, state=state, listed=listed, q=query, limit=limit, offset=offset
        )

    async def get_item(feed_id: str, item_id: str) -> ItemRead:
        """One Catalog item with its media URL and assets."""
        return await services().get_item(feed_id, item_id)

    async def archive_episodes(feed_id: str, selection: SelectionRequest) -> SelectionResult:
        """Archive an explicit selection of Episodes exactly once.

        ``selection.selection`` is an expression such as ``"1-42, 180"`` in the
        Source's numbering (``numbering="ordinal"`` for Copycast ordinals);
        ``dry_run`` resolves without queuing anything.
        """
        return await services().select_items(feed_id, selection, trigger=JobTrigger.mcp)

    async def fetch_item_metadata(feed_id: str, item_id: str) -> ItemRead:
        """Ask the Source for one item's description, exact date and artwork now.

        One request to the Source; useful for a YouTube video listed without
        a description before it is archived.
        """
        return await services().fetch_item_metadata(feed_id, item_id)

    async def delete_item(feed_id: str, item_id: str) -> dict[str, Any]:
        """Delete one Episode's media.

        The item becomes Available again and is never re-archived automatically. A locked
        Episode is refused (conflict): unlock it with ``set_archive_flag`` first.
        """
        await services().delete_item(feed_id, item_id)
        return {"deleted": True, "feed_id": feed_id, "item_id": item_id}

    async def set_archive_flag(feed_id: str, item_id: str, flag: ArchiveFlagUpdate) -> ItemRead:
        """Lock an Episode (``archive_flag`` true: never deleted) or unlock it (false).

        Retention, a Rolling window, Sync, ``clear_feed`` and ``delete_item`` spare a locked
        Episode. A Mirror on the Archive policy (``all``, ``new``, ``latest``, ``selection``)
        without ``sync_deletions`` locks what it archives on its own.
        """
        return await services().set_archive_flag(feed_id, item_id, flag)

    # ------------------------------------------------------------------ mirrors

    async def refresh_mirror(feed_id: str) -> JobRead | None:
        """Queue a Refresh of a Mirror now (ignores Paused and the fetch cooldown)."""
        return await services().request_refresh(feed_id, JobTrigger.manual)

    async def archive_available(feed_id: str) -> SelectionResult:
        """Queue every Available item of a Feed now, whatever its policy (Shorts included)."""
        return await services().archive_available(feed_id, trigger=JobTrigger.mcp)

    async def retry_failed(feed_id: str) -> SelectionResult:
        """Queue every failed download of a Feed again."""
        return await services().retry_failed(feed_id, trigger=JobTrigger.mcp)

    async def set_mirror_paused(feed_id: str, paused: bool) -> MirrorRead:
        """Pause or resume a Mirror; a Paused Mirror keeps serving its feed."""
        return await services().set_paused(feed_id, paused)

    async def update_mirror(feed_id: str, patch: MirrorUpdate) -> MirrorRead:
        """Change a Mirror's title, follow flag, backfill, engine options or Source URL.

        ``title`` is kept over the Source's across Refreshes; null restores the Source's.
        Switching to ``rolling``, or to ``automatic`` (Ephemeral) with a
        ``retention_days``, schedules deletions, so it needs a ``full`` key; switching to
        ``new`` archives only what the Source publishes from then on. Switching to the
        Archive policy (``all``, ``new``, ``latest``, ``selection``), or turning
        ``sync_deletions`` off while on it, locks every Episode archived so far; a Mirror with
        ``sync_deletions`` is never locked automatically, and switching away unlocks nothing.
        Setting or clearing ``engine_options`` (allowlisted yt-dlp params) needs a ``full`` key.
        """
        return await services().update_mirror(feed_id, patch)

    # ------------------------------------------------------------------ buckets

    async def create_bucket(bucket: BucketCreate) -> BucketRead:
        """Create a Bucket: a feed fed by Requests, prunable on demand or automatically.

        Setting ``autoprune_days`` schedules deletions, so it needs a ``full`` key.
        """
        return await services().create_bucket(bucket)

    async def update_bucket(bucket_id: str, patch: BucketUpdate) -> BucketRead:
        """Rename a Bucket or change ``autoprune_days`` (null switches autoprune off).

        Switching Retention on schedules deletions, so it needs a ``full`` key.
        """
        return await services().update_bucket(bucket_id, patch)

    async def add_to_bucket(request: RequestCreate, bucket: str = DEFAULT_BUCKET) -> RequestRead:
        """Push any URL (video, playlist, page) into a Bucket by id or name (default "Copycast").

        A stream that is live is archived once YouTube publishes its recording (the item
        waits, Queued, up to 48 h); one that has not started yet cannot be listed and the
        Request fails with YouTube's reason: push it again once it is live or recorded.
        """
        return await services().add_request(bucket, request, via=RequestedVia.mcp)

    async def prune_bucket(bucket: str, prune: PruneRequest) -> PruneResult:
        """Delete Bucket Episodes matching every given criterion.

        Criteria: ``downloaded`` (at least once), ``older_than_days`` (since
        added); ``dry_run`` previews the count and bytes.
        """
        return await services().prune_bucket(bucket, prune)

    # ------------------------------------------------------------------ uploads

    async def create_upload(upload: UploadCreate) -> UploadRead:
        """Stage one local audio file (mp3, m4a, m4b) for a Serial Bucket.

        Returns ``upload_url``: send the file's bytes there with an HTTP PUT, for example
        ``curl -sf -T "<file>" "<upload_url>"`` (no other credential; usable once). The
        answer of that PUT, or ``get_upload``, carries the tags Copycast read from the file:
        title, album, artist, track and disc numbers, duration, embedded chapters.
        """
        return await services().create_upload(upload)

    async def get_upload(upload_id: str) -> UploadRead:
        """A staged upload: ``state`` (pending until its PUT, then received) and its tags."""
        return await services().get_upload(upload_id)

    async def delete_upload(upload_id: str) -> None:
        """Give up a staged upload (pending or received) that will not be added, so it
        stops counting against the quota; unadded uploads otherwise expire after 24 h."""
        await services().delete_upload(upload_id)

    async def create_serial_bucket(bucket: SerialBucketCreate) -> BucketRead:
        """Create a Bucket from received uploads, Episodes in exactly the order of ``episodes``.

        Use it for an audiobook or any work split into files. You decide the listening
        order: read every upload's tags and file name (disc, then track, then natural
        file-name order is the usual answer; chapter titles beat file names when the tags
        disagree), then list ``episodes`` first to last. Give each one a clean ``title``
        (the chapter's name, not the file name; Copycast prefixes "07 · " unless
        ``number_titles`` is false), ``part``/``part_title`` for books or parts,
        ``chapter`` when it has one, and ``author`` only when it differs from the Bucket's.
        The feed is published ``itunes:type serial``, one Episode per day at a playful
        posting time so every app plays it in order; each description starts with the
        Episode's place in the work. Quote the returned ``feed_url`` to the user.
        """
        return await services().create_serial_bucket(bucket)

    async def add_uploads(bucket: str, uploads: UploadsAdd) -> BucketRead:
        """Add received uploads to an existing Bucket (id or name) as archived Episodes.

        Copycast orders them by file name, naturally ("Part 2" before "Part 10"), and dates
        them by arrival, after everything already in the Bucket. Titles come from the files'
        tags, else their names. For a work whose order you decide, use
        ``create_serial_bucket`` instead.
        """
        return await services().add_uploads(bucket, uploads)

    # ------------------------------------------------------------------ jobs and about

    async def get_job(job_id: uuid.UUID) -> JobRead:
        """One job with its progress and outcome."""
        return await services().get_job(job_id)

    async def list_jobs(
        feed_id: str | None = None,
        kind: JobKind | None = None,
        status: JobStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> JobPage:
        """Jobs newest first, filtered by feed, kind or status."""
        return await services().list_jobs(
            feed_id=feed_id, kind=kind, status=status, limit=limit, offset=offset
        )

    async def cancel_job(job_id: uuid.UUID) -> JobRead:
        """Cancel a queued job or ask a running one to stop."""
        return await services().cancel_job(job_id)

    async def get_about() -> AboutRead:
        """Application and engine versions, layout version and totals."""
        return await services().about()

    async def get_status() -> StatusRead:
        """Status of every component (database, worker, scheduler, job queue, storage,
        yt-dlp, ffmpeg, crawlers by service): check it when Refreshes or archives fail."""
        return await services().get_status()

    registrations: list[tuple[ToolFn, str, str, bool]] = [
        (search_podcasts, "search_podcasts", "search_podcasts", True),
        (search_videos, "search_videos", "search_videos", True),
        (list_youtube_playlists, "list_youtube_playlists", "list_youtube_playlists", True),
        (probe_source, "probe_source", "probe_source", True),
        (create_mirror, "create_mirror", "create_mirror", False),
        (list_feeds, "list_feeds", "list_feeds", True),
        (get_feed, "get_feed", "get_feed", True),
        (list_feed_objects, "list_feed_objects", "list_feed_objects", True),
        (storage_summary, "storage_summary", "storage_summary", True),
        (delete_feed, "delete_feed", "delete_feed", False),
        (clear_feed, "clear_feed", "clear_feed", False),
        (list_items, "list_items", "list_items", True),
        (get_item, "get_item", "get_item", True),
        (archive_episodes, "archive_episodes", "select_items", False),
        (archive_available, "archive_available", "archive_available", False),
        (retry_failed, "retry_failed", "retry_failed", False),
        (delete_item, "delete_item", "delete_item", False),
        (fetch_item_metadata, "fetch_item_metadata", "fetch_item_metadata", False),
        (set_archive_flag, "set_archive_flag", "set_archive_flag", False),
        (refresh_mirror, "refresh_mirror", "request_refresh", False),
        (set_mirror_paused, "set_mirror_paused", "set_paused", False),
        (update_mirror, "update_mirror", "update_mirror", False),
        (create_bucket, "create_bucket", "create_bucket", False),
        (update_bucket, "update_bucket", "update_bucket", False),
        (add_to_bucket, "add_to_bucket", "add_request", False),
        (prune_bucket, "prune_bucket", "prune_bucket", False),
        (create_upload, "create_upload", "create_upload", False),
        (get_upload, "get_upload", "get_upload", True),
        (delete_upload, "delete_upload", "delete_upload", False),
        (create_serial_bucket, "create_serial_bucket", "create_serial_bucket", False),
        (add_uploads, "add_uploads", "add_uploads", False),
        (get_job, "get_job", "get_job", True),
        (list_jobs, "list_jobs", "list_jobs", True),
        (cancel_job, "cancel_job", "cancel_job", False),
        (get_about, "get_about", "about", True),
        (get_status, "get_status", "get_status", True),
    ]
    for fn, name, capability, read_only in registrations:
        scope = required_scope(destructive=capability in DESTRUCTIVE, read_only=read_only)
        mcp.tool(
            guarded(with_scope(fn, name, scope, container)),
            name=name,
            meta={
                "capability": capability,
                "scope": scope.value,
                "access": ACCESS[capability].value,
            },
            annotations=_annotations(capability, read_only=read_only),
        )


__all__ = [
    "DEFAULT_BUCKET",
    "DELETE_FEED_WARNING",
    "ToolFn",
    "register_tools",
    "services",
    "with_scope",
]
