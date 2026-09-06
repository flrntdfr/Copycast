"""Every enumerated value stored as TEXT (named CHECK constraints) or exchanged over the API."""

from __future__ import annotations

from enum import StrEnum


class FeedKind(StrEnum):
    mirror = "mirror"
    inbox = "inbox"


class SourceKind(StrEnum):
    rss = "rss"
    ytdlp = "ytdlp"


class BackfillMode(StrEnum):
    all = "all"
    latest = "latest"
    selection = "selection"


class ArchiveState(StrEnum):
    available = "available"
    wanted = "wanted"
    archiving = "archiving"
    archived = "archived"
    failed = "failed"
    deleted = "deleted"


class WantedReason(StrEnum):
    backfill = "backfill"
    follow = "follow"
    manual = "manual"
    request = "request"


class CatalogState(StrEnum):
    """Derived display state of a Catalog item (archive_state x listed)."""

    listed = "listed"
    delisted = "delisted"
    available = "available"
    queued = "queued"
    archiving = "archiving"
    failed = "failed"
    hidden = "hidden"


def catalog_state(archive_state: ArchiveState, listed: bool) -> CatalogState:
    """Listed: archived+listed. Delisted: archived+!listed. Available: available|deleted+listed."""
    if archive_state is ArchiveState.archived:
        return CatalogState.listed if listed else CatalogState.delisted
    if archive_state in (ArchiveState.available, ArchiveState.deleted):
        return CatalogState.available if listed else CatalogState.hidden
    if archive_state is ArchiveState.wanted:
        return CatalogState.queued
    if archive_state is ArchiveState.archiving:
        return CatalogState.archiving
    return CatalogState.failed


class AssetKind(StrEnum):
    artwork = "artwork"
    chapters = "chapters"
    transcript = "transcript"


class AssetProvenance(StrEnum):
    mirrored = "mirrored"
    generated = "generated"


class AssetFormat(StrEnum):
    vtt = "vtt"
    srt = "srt"
    json = "json"
    text = "text"
    html = "html"


class AssetState(StrEnum):
    wanted = "wanted"
    archived = "archived"
    failed = "failed"


class RequestedVia(StrEnum):
    ui = "ui"
    mcp = "mcp"


class KeyScope(StrEnum):
    """What an API key may do over MCP: read < write < full (destructive tools included)."""

    read = "read"
    write = "write"
    full = "full"


class RequestStatus(StrEnum):
    queued = "queued"
    expanded = "expanded"
    failed = "failed"


class JobKind(StrEnum):
    refresh = "refresh"
    archive_item = "archive_item"
    expand_request = "expand_request"
    prune = "prune"
    rebuild = "rebuild"


class JobTrigger(StrEnum):
    manual = "manual"
    scheduled = "scheduled"
    feed_fetch = "feed_fetch"
    request = "request"
    policy = "policy"
    ui = "ui"
    mcp = "mcp"


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class ErrorKind(StrEnum):
    transient = "transient"
    permanent = "permanent"
    cancelled = "cancelled"
    storage_full = "storage_full"


class LogLevel(StrEnum):
    debug = "debug"
    info = "info"
    warning = "warning"
    error = "error"


class RefreshRunStatus(StrEnum):
    running = "running"
    succeeded = "succeeded"
    unchanged = "unchanged"
    failed = "failed"
    cancelled = "cancelled"


class ListingOrder(StrEnum):
    newest_first = "newest_first"
    oldest_first = "oldest_first"


class HealthStatus(StrEnum):
    ok = "ok"
    warn = "warn"
    error = "error"
    paused = "paused"
    never = "never"


class ProgressPhase(StrEnum):
    """Phase reported on a job's progress snapshot."""

    listing = "listing"
    downloading = "downloading"
    postprocessing = "postprocessing"
    assets = "assets"


class EnginePhase(StrEnum):
    """Phase reported by the engine's progress hooks."""

    downloading = "downloading"
    postprocessing = "postprocessing"
    finished = "finished"


class FetchKind(StrEnum):
    ytdlp = "ytdlp"
    direct = "direct"


class Numbering(StrEnum):
    source = "source"
    ordinal = "ordinal"


class EngineChannel(StrEnum):
    nightly = "nightly"
    stable = "stable"


class DeleteReason(StrEnum):
    user = "user"
    prune = "prune"


class PruneMode(StrEnum):
    auto = "auto"
    manual = "manual"
