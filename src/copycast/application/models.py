"""Request and response models shared verbatim by the HTTP API, the MCP tools and the UI.

Every capability takes and returns these classes; routes and tools are thin
wrappers around them, which is what the convergence test enforces.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from copycast.domain.engine_options import EngineOptions
from copycast.domain.enums import (
    ArchiveState,
    AssetFormat,
    AssetKind,
    AssetProvenance,
    AssetState,
    BackfillMode,
    ErrorKind,
    FeedKind,
    HealthStatus,
    JobKind,
    JobStatus,
    JobTrigger,
    KeyScope,
    LiveStatus,
    Numbering,
    ProgressPhase,
    RequestedVia,
    RequestStatus,
    SourceKind,
)
from copycast.domain.language import normalize_language

BUCKET_NAME_MAX_LEN = 80
SELECTION_MAX_LEN = 4096
API_KEY_NAME_MAX_LEN = 80


class ReadModel(BaseModel):
    """Base of every response model: frozen, tolerant of ORM attribute access."""

    model_config = ConfigDict(frozen=True, from_attributes=True)


class RequestModel(BaseModel):
    """Base of every request model: unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- feeds


class BackfillPolicy(ReadModel):
    mode: BackfillMode
    latest_n: int | None = Field(default=None, description="N for latest and rolling")
    retention_days: int | None = Field(
        default=None, description="Automatic: days after the last download before expiry"
    )


class FeedHealth(ReadModel):
    status: HealthStatus
    reason: str | None = None


class CatalogCounts(ReadModel):
    listed: int = 0
    available: int = 0
    delisted: int = 0
    wanted: int = 0
    archived: int = 0
    failed: int = 0


class SelectionSummary(ReadModel):
    """What a selection-mode Mirror has selected so far."""

    count: int = Field(description="Items selected for archiving so far")
    expression: str | None = Field(default=None, description="The last selection expression")
    applied_at: datetime | None = None


class FeedCredentials(ReadModel):
    """The feed's own HTTP Basic pair; present only while authentication is on."""

    username: str
    password: str


class _FeedReadBase(ReadModel):
    id: str
    title: str
    description: str | None = None
    artwork_url: str | None = None
    feed_url: str = Field(description="Carries user:pass@ while authentication is on")
    feed_credentials: FeedCredentials | None = None
    episode_count: int = 0
    storage_bytes: int = 0
    revision: int = 1
    created_at: datetime


class MirrorRead(_FeedReadBase):
    kind: Literal[FeedKind.mirror] = FeedKind.mirror
    source_title: str = Field(
        description="The title the Source reports; ``title`` is yours when set"
    )
    title_override: str | None = Field(default=None, description="Your own title, if any")
    source_url: str
    service: str | None = None
    source_kind: SourceKind
    language: str | None = Field(default=None, description="The language the Source reports")
    preferred_language: str | None = Field(
        default=None, description="This Mirror's metadata language override, else the default"
    )
    min_duration_seconds: int | None = Field(
        default=None, description="Shorter items are listed but never archived automatically"
    )
    refresh_interval_hours: int | None = Field(
        default=None, description="This Mirror's Refresh interval override, else the default"
    )
    sync_deletions: bool = Field(
        default=False, description="Episodes are deleted when their item leaves the Source"
    )
    playlist_capture: bool = Field(
        default=False, description="Captured from the Buckets; listed among them, not as a Mirror"
    )
    paused: bool = False
    follow: bool = True
    backfill: BackfillPolicy
    engine_options: dict[str, Any] = Field(default_factory=dict[str, Any])
    last_refresh_attempt_at: datetime | None = None
    last_refresh_success_at: datetime | None = None
    last_light_refresh_at: datetime | None = Field(
        default=None,
        description="When a fetch of the Mirror Feed last checked the Source's newest items",
    )
    last_error: str | None = None
    health: FeedHealth
    counts: CatalogCounts = Field(default_factory=CatalogCounts)
    selection: SelectionSummary | None = None


class BucketRead(_FeedReadBase):
    kind: Literal[FeedKind.bucket] = FeedKind.bucket
    name: str
    autoprune_days: int | None = None
    request_count: int = 0
    serial: bool = Field(
        default=False, description="A Serial Bucket: Episodes in the order given at creation"
    )
    is_default: bool = Field(
        default=False, description="The default Bucket: it can be renamed but never deleted"
    )


FeedRead = Annotated[MirrorRead | BucketRead, Field(discriminator="kind")]


class FeedList(ReadModel):
    feeds: list[FeedRead] = Field(default_factory=list[FeedRead])


# --------------------------------------------------------------------------- items and assets


class AssetRead(ReadModel):
    id: str
    feed_id: str
    item_id: str | None = None
    kind: AssetKind
    provenance: AssetProvenance = AssetProvenance.mirrored
    language: str | None = None
    format: AssetFormat | None = None
    url: str | None = Field(default=None, description="Local URL once archived")
    remote_url: str | None = None
    mime: str | None = None
    size_bytes: int | None = None
    state: AssetState
    last_error: str | None = None
    fetched_at: datetime | None = None


class ItemMedia(ReadModel):
    url: str
    bytes: int
    mime: str
    ext: str


class ItemRead(ReadModel):
    id: str
    feed_id: str
    ordinal: int
    source_number: int | None = None
    source_season: int | None = None
    title: str
    description: str | None = None
    published_at: datetime | None = None
    published_at_approximate: bool = Field(
        default=False,
        description='True while the date is YouTube\'s "3 weeks ago", until the video is archived',
    )
    added_at: datetime = Field(description="first_seen_at")
    duration_seconds: int | None = None
    state: ArchiveState
    listed: bool = True
    archive_flag: bool = Field(
        default=False,
        description="Kept for good: Retention, a Rolling window, Sync and clearing the Feed "
        "never delete it",
    )
    live_status: LiveStatus | None = Field(
        default=None,
        description="What the Source last said the item is doing as a live stream: an "
        "upcoming or live stream is listed but archived only once its recording is "
        "published; post_live means the recording is still being processed",
    )
    attempt_count: int = 0
    last_error: str | None = None
    media: ItemMedia | None = None
    public_media_url: str = Field(
        description="The media URL the feed advertises: fixed for the item's life, whatever "
        "container the archive gets (media.url names the archived file itself)"
    )
    artwork_url: str | None = None
    assets: list[AssetRead] = Field(default_factory=list[AssetRead])
    download_count: int = 0
    first_downloaded_at: datetime | None = None
    last_downloaded_at: datetime | None = None
    request_ids: list[UUID] = Field(default_factory=list[UUID])
    item_url: str | None = Field(default=None, description="The item's page at the Source")


class ItemPage(ReadModel):
    items: list[ItemRead]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------- jobs


class JobProgress(ReadModel):
    phase: ProgressPhase
    item_id: str | None = None
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    percent: float | None = None
    speed_bps: float | None = None
    eta_seconds: int | None = None


class JobRead(ReadModel):
    id: UUID
    kind: JobKind
    feed_id: str | None = None
    item_id: str | None = None
    request_id: UUID | None = None
    trigger: JobTrigger
    status: JobStatus
    progress: JobProgress | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    error_kind: ErrorKind | None = None
    attempt: int = 0
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobPage(ReadModel):
    jobs: list[JobRead]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------- probe and search


class ProbeRequest(RequestModel):
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("url")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("url must not be blank")
        return value


class ProbeCandidate(ReadModel):
    candidate_token: str
    source_url: str
    source_kind: SourceKind
    service: str | None = None
    title: str | None = None
    description: str | None = None
    artwork_url: str | None = None
    author: str | None = None
    item_count: int | None = None


class ProbeResult(ReadModel):
    input_url: str
    candidates: list[ProbeCandidate]


class PodcastSearchResult(ReadModel):
    title: str
    author: str | None = None
    feed_url: str
    artwork_url: str | None = None
    itunes_id: int | None = None
    episode_count: int | None = None
    genre: str | None = None
    latest_release_at: datetime | None = None


class PodcastSearchPage(ReadModel):
    query: str
    results: list[PodcastSearchResult]


class YouTubePlaylist(ReadModel):
    """One of the signed-in account's playlists (needs the cookie file), Watch Later first."""

    id: str
    title: str
    url: str
    item_count: int | None = None
    captured_feed_id: str | None = Field(
        default=None, description="The Mirror already capturing it, if any"
    )


class YouTubePlaylistList(ReadModel):
    playlists: list[YouTubePlaylist]


class VideoSearchResult(ReadModel):
    """One YouTube search hit; ``url`` is what ``add_to_bucket`` or ``probe_source`` takes."""

    title: str
    url: str
    channel: str | None = None
    duration_seconds: int | None = None
    published_at: datetime | None = None
    artwork_url: str | None = None


class VideoSearchPage(ReadModel):
    query: str
    results: list[VideoSearchResult]


# --------------------------------------------------------------------------- mirrors


DEFAULT_RETENTION_DAYS = 7
WINDOW_MODES = frozenset({BackfillMode.latest, BackfillMode.rolling})


class BackfillRequest(RequestModel):
    """The archive policy; ``selection`` implies mode ``selection``.

    Archive: ``all`` (everything, then Follow) or ``new`` (nothing listed so far, then
    every new item). Ephemeral: ``automatic``, nothing ahead of time; an Episode is
    downloaded when a podcast app first asks for it and expires ``retention_days``
    (default 7) after its last download. ``rolling``: the newest ``latest_n`` only, older
    Episodes tombstoned as newer ones arrive. ``selection``: exactly the Episodes named.
    ``latest`` (N at creation, then Follow) remains for existing Mirrors.
    """

    mode: BackfillMode = BackfillMode.all
    latest_n: int | None = Field(default=None, ge=1, description="N for latest and rolling")
    retention_days: int | None = Field(default=None, ge=1, description="Automatic only")
    selection: str | None = Field(default=None, max_length=SELECTION_MAX_LEN)

    @model_validator(mode="after")
    def _consistent(self) -> BackfillRequest:
        if self.selection is not None and self.selection.strip():
            if "mode" in self.model_fields_set and self.mode is not BackfillMode.selection:
                raise ValueError("selection given: backfill.mode must be 'selection'")
            self.mode = BackfillMode.selection
        if self.mode in WINDOW_MODES and self.latest_n is None:
            raise ValueError(f"backfill.mode '{self.mode.value}' requires latest_n")
        if self.mode not in WINDOW_MODES:
            self.latest_n = None
        if self.mode is not BackfillMode.automatic:
            self.retention_days = None
        elif "retention_days" not in self.model_fields_set:
            self.retention_days = DEFAULT_RETENTION_DAYS  # an explicit None keeps forever
        return self


class MirrorCreate(RequestModel):
    source_url: str = Field(min_length=1, max_length=2048)
    candidate_token: str | None = None
    backfill: BackfillRequest | None = Field(
        default=None, description="Absent: the operator's default policy (Settings page)"
    )
    follow: bool | None = Field(
        default=None, description="Defaults to true, or false under a selection backfill"
    )
    engine_options: dict[str, Any] = Field(default_factory=dict[str, Any])
    preferred_language: str | None = Field(
        default=None, max_length=16, description="BCP 47 tag such as fr; YouTube metadata language"
    )
    min_duration_seconds: int | None = Field(
        default=None,
        ge=1,
        description="Skip items shorter than this (Shorts); unknown lengths pass",
    )
    refresh_interval_hours: int | None = Field(
        default=None, ge=1, description="This Mirror's Refresh interval; null follows the default"
    )
    sync_deletions: bool | None = Field(
        default=None,
        description="Delete an Episode when its item leaves the Source (a playlist kept in sync)",
    )
    playlist_capture: bool | None = Field(
        default=None,
        description="A YouTube playlist captured from the Buckets (listed among them)",
    )

    @field_validator("preferred_language")
    @classmethod
    def _language(cls, value: str | None) -> str | None:
        return normalize_language(value)

    @field_validator("source_url")
    @classmethod
    def _strip_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source_url must not be blank")
        return value

    @field_validator("engine_options")
    @classmethod
    def _feed_scope(cls, value: dict[str, Any]) -> dict[str, Any]:
        return EngineOptions.validate(value, scope="feed")

    def resolve(self, default: BackfillRequest) -> tuple[BackfillRequest, bool]:
        """The policy to apply and the Follow flag, filling both from ``default``."""
        backfill = self.backfill if self.backfill is not None else default
        follow = (
            self.follow if self.follow is not None else backfill.mode is not BackfillMode.selection
        )
        return backfill, follow


class MirrorUpdate(RequestModel):
    """PATCH body; only the fields present in ``model_fields_set`` are applied."""

    source_url: str | None = Field(default=None, min_length=1, max_length=2048)
    title: str | None = Field(
        default=None,
        max_length=512,
        description="Your own title, kept over the Source's; null or blank uses the Source's",
    )
    follow: bool | None = None
    backfill: BackfillRequest | None = None
    engine_options: dict[str, Any] | None = None
    preferred_language: str | None = Field(default=None, max_length=16)
    min_duration_seconds: int | None = Field(default=None, ge=1)
    refresh_interval_hours: int | None = Field(default=None, ge=1)
    sync_deletions: bool | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("source_url")
    @classmethod
    def _strip_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("source_url must not be blank")
        return value

    @field_validator("preferred_language")
    @classmethod
    def _language(cls, value: str | None) -> str | None:
        return normalize_language(value)

    @field_validator("engine_options")
    @classmethod
    def _feed_scope(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else EngineOptions.validate(value, scope="feed")


class SelectionRequest(RequestModel):
    selection: str | None = Field(
        default=None, max_length=SELECTION_MAX_LEN, description='e.g. "1-42, 180"'
    )
    item_ids: list[str] = Field(default_factory=list[str])
    numbering: Numbering = Numbering.source
    dry_run: bool = False

    @model_validator(mode="after")
    def _non_empty(self) -> SelectionRequest:
        if not (self.selection and self.selection.strip()) and not self.item_ids:
            raise ValueError("give a selection expression or item_ids")
        return self


class MirrorChangePreview(ReadModel):
    """What applying a ``MirrorUpdate`` would do to archived content before it is applied."""

    would_delete_count: int = 0
    would_delete_bytes: int = 0
    would_archive_count: int = 0


class SelectionResult(ReadModel):
    resolved: list[str]
    unresolved: list[str]
    numbering_used: Numbering
    already_archived_count: int
    jobs: list[JobRead] = Field(default_factory=list[JobRead])
    dry_run: bool = False


# --------------------------------------------------------------------------- buckets and requests


def _clean_name(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("name must not be blank")
    return value


class BucketCreate(RequestModel):
    name: str = Field(min_length=1, max_length=BUCKET_NAME_MAX_LEN)
    autoprune_days: int | None = Field(default=None, ge=1)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _clean_name(value)


class BucketUpdate(RequestModel):
    """PATCH body; ``autoprune_days: null`` present in the body switches autoprune off."""

    name: str | None = Field(default=None, min_length=1, max_length=BUCKET_NAME_MAX_LEN)
    autoprune_days: int | None = Field(default=None, ge=1)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return None if value is None else _clean_name(value)


class UploadCreate(RequestModel):
    filename: str = Field(min_length=1, max_length=255, description="The file's own name")

    @field_validator("filename")
    @classmethod
    def _filename(cls, value: str) -> str:
        value = value.strip().replace("\\", "/").rsplit("/", 1)[-1]
        if not value or value in {".", ".."}:
            raise ValueError("filename must name a file")
        return value


class AudioChapter(ReadModel):
    start_seconds: float
    title: str | None = None


class AudioTags(ReadModel):
    """What Copycast read from an uploaded file (ID3, MP4, Vorbis tags); null when absent."""

    title: str | None = None
    album: str | None = None
    artist: str | None = None
    album_artist: str | None = None
    track: int | None = None
    track_total: int | None = None
    disc: int | None = None
    disc_total: int | None = None
    genre: str | None = None
    year: str | None = None
    comment: str | None = None
    duration_seconds: int | None = None
    mime: str | None = None
    chapters: list[AudioChapter] = Field(default_factory=list[AudioChapter])


class UploadRead(ReadModel):
    id: str
    filename: str
    state: Literal["pending", "received", "used"]
    upload_url: str | None = Field(
        default=None,
        description="PUT the file's bytes here (no other credential); null once received",
    )
    size_bytes: int | None = None
    tags: AudioTags | None = None
    created_at: datetime


class SerialEpisode(RequestModel):
    """One file of a Serial Bucket, described the way it should read in a podcast app."""

    upload_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=300)
    author: str | None = Field(default=None, max_length=300, description="Else the Bucket's")
    description: str | None = Field(default=None, max_length=20000)
    part: int | None = Field(default=None, ge=0, description="Part, book or disc number")
    part_title: str | None = Field(default=None, max_length=300)
    chapter: int | None = Field(default=None, ge=0)


class SerialBucketCreate(RequestModel):
    """A Serial Bucket: ``episodes`` in listening order, first to last (ADR 0015)."""

    name: str = Field(min_length=1, max_length=BUCKET_NAME_MAX_LEN)
    author: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=20000)
    number_titles: bool = Field(
        default=True, description='Prefix each title with its position ("07 · Title")'
    )
    episodes: list[SerialEpisode] = Field(min_length=1, max_length=2000)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _clean_name(value)

    @model_validator(mode="after")
    def _distinct(self) -> SerialBucketCreate:
        ids = [episode.upload_id for episode in self.episodes]
        if len(set(ids)) != len(ids):
            raise ValueError("each upload may appear only once")
        return self


class UploadsAdd(RequestModel):
    """Received uploads to add to a Bucket; Copycast orders them by file name, naturally."""

    upload_ids: list[str] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _distinct(self) -> UploadsAdd:
        if len(set(self.upload_ids)) != len(self.upload_ids):
            raise ValueError("each upload may appear only once")
        return self


class RequestCreate(RequestModel):
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("url")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("url must not be blank")
        return value


class RequestRead(ReadModel):
    id: UUID
    bucket_id: str
    url: str
    requested_via: RequestedVia
    status: RequestStatus
    item_count: int = 0
    error: str | None = None
    items: list[ItemRead] = Field(default_factory=list[ItemRead])
    job: JobRead | None = None
    created_at: datetime


class RequestPage(ReadModel):
    requests: list[RequestRead]
    total: int
    limit: int
    offset: int


class PruneRequest(RequestModel):
    """Criteria are ANDed; at least one is required."""

    downloaded: bool = Field(default=False, description="Downloaded at least once")
    older_than_days: int | None = Field(
        default=None, ge=0, description="Added more than N days ago"
    )
    dry_run: bool = False

    @model_validator(mode="after")
    def _one_criterion(self) -> PruneRequest:
        if not self.downloaded and self.older_than_days is None:
            raise ValueError("give at least one criterion: downloaded or older_than_days")
        return self


class PruneResult(ReadModel):
    matched: int
    deleted_count: int
    bytes_freed: int
    dry_run: bool


class PurgeRequest(RequestModel):
    """Delete every archived Episode of every feed; feeds, Catalogs and artwork stay."""

    dry_run: bool = True


class ClearFeedRequest(RequestModel):
    """Delete every Episode of one Feed that is not kept for good; the Feed stays."""

    dry_run: bool = Field(default=True, description="Only count what would go and what stays")


class ClearFeedResult(PruneResult):
    kept_count: int = Field(
        default=0,
        description="Episodes spared: flagged Archive, or every Episode of a Mirror whose "
        "policy is Archive",
    )
    kept_bytes: int = 0


class ArchiveFlagUpdate(RequestModel):
    archive_flag: bool = Field(
        description="True keeps the Episode for good: Retention, a Rolling window, Sync and "
        "clearing the Feed spare it"
    )


# --------------------------------------------------------------------------- mirror defaults


class MirrorDefaults(RequestModel):
    """Operator defaults applied to every Mirror whose own value is null.

    Stored under the data directory (``engine/defaults.json``), edited on the
    Settings page; a Mirror overrides a value by setting its own, and resets
    to the default by clearing it.
    """

    language: str | None = Field(
        default=None, max_length=16, description="Preferred YouTube metadata language (fr, pt-BR)"
    )
    min_duration_seconds: int | None = Field(
        default=None, ge=1, description="Items shorter than this stay Available (Shorts)"
    )
    backfill: BackfillRequest = Field(
        default_factory=lambda: BackfillRequest(mode=BackfillMode.automatic),
        description="The policy a new Mirror gets unless the request names one",
    )
    refresh_interval_hours: int = Field(
        default=24, ge=1, le=24 * 30, description="How often Mirrors Refresh unless they say"
    )

    @field_validator("backfill")
    @classmethod
    def _no_selection_default(cls, value: BackfillRequest) -> BackfillRequest:
        if value.mode is BackfillMode.selection:
            raise ValueError("the default policy cannot be a selection")
        return value

    @field_validator("language")
    @classmethod
    def _language(cls, value: str | None) -> str | None:
        return normalize_language(value)


# --------------------------------------------------------------------------- engine cookies


class EngineCookiesWrite(RequestModel):
    """A Netscape ``cookies.txt`` as exported by a browser extension, stored under ``name``."""

    content: str = Field(min_length=1, max_length=1024 * 1024)
    name: str = Field(
        default="cookies",
        min_length=1,
        max_length=128,
        description="The file's name in the table (an upload's file name); the same name "
        "replaces that file, a new one adds a row",
    )


class EngineCookieFileRead(ReadModel):
    """One stored cookie file without any value."""

    name: str
    size_bytes: int = 0
    updated_at: datetime | None = None
    cookie_count: int = 0
    domains: list[str] = Field(default_factory=list[str])
    services: list[str] = Field(
        default_factory=list[str], description="The ids of the services the file unlocks"
    )


class CookieServiceRead(ReadModel):
    """A site shown on the Settings page, unlocked once a stored file covers it."""

    id: str
    name: str
    domains: list[str]
    authenticated: bool
    files: list[str] = Field(default_factory=list[str], description="The files unlocking it")


class EngineCookiesRead(ReadModel):
    """The stored cookie files without any value: presence, size, where they apply."""

    present: bool
    size_bytes: int = 0
    updated_at: datetime | None = None
    cookie_count: int = 0
    domains: list[str] = Field(default_factory=list[str])
    youtube: bool = Field(default=False, description="Whether a youtube.com cookie is stored")
    files: list[EngineCookieFileRead] = Field(default_factory=list[EngineCookieFileRead])
    services: list[CookieServiceRead] = Field(default_factory=list[CookieServiceRead])


# --------------------------------------------------------------------------- api keys


class ApiKeyCreate(RequestModel):
    name: str = Field(min_length=1, max_length=API_KEY_NAME_MAX_LEN, description="Who holds it")
    scope: KeyScope = Field(
        default=KeyScope.write,
        description="read: read-only tools; write: everything but deletions; full: everything",
    )

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _clean_name(value)


class ApiKeyRead(ReadModel):
    id: UUID
    name: str
    scope: KeyScope
    prefix: str = Field(description="The displayable head of the key (cck_XXXXXXXX)")
    created_at: datetime
    last_used_at: datetime | None = None


class ApiKeyCreated(ReadModel):
    """The new key and its secret, shown exactly once."""

    key: ApiKeyRead
    secret: str


class ApiKeyList(ReadModel):
    keys: list[ApiKeyRead] = Field(default_factory=list[ApiKeyRead])


# --------------------------------------------------------------------------- about, health, admin


class EngineRead(ReadModel):
    name: str
    version: str
    channel: str
    release_date: date | None = None
    git_head: str | None = None


class Totals(ReadModel):
    feeds: int = 0
    episodes: int = 0
    storage_bytes: int = 0


class AboutRead(ReadModel):
    version: str
    engine: EngineRead
    ffmpeg_version: str | None = None
    base_url: str
    layout_version: str
    totals: Totals = Field(default_factory=Totals)
    auth_enabled: bool = Field(
        default=False, description="Whether an operator password gates the UI, API and MCP"
    )


class ReadyCheck(ReadModel):
    ok: bool
    detail: str | None = None


class ReadyRead(ReadModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, ReadyCheck]


StatusState = Literal["ok", "warn", "error", "unknown"]


class StatusFact(ReadModel):
    """One line under a component: a value, a moment (shown relative) or both."""

    label: str
    value: str | None = None
    at: datetime | None = None
    detail: str | None = Field(default=None, description="A longer note, such as the last error")
    state: StatusState | None = Field(
        default=None, description="Set when this line has a state of its own (a crawler)"
    )


class ComponentStatus(ReadModel):
    id: str
    name: str
    state: StatusState
    summary: str
    facts: list[StatusFact] = Field(default_factory=list[StatusFact])


class StatusRead(ReadModel):
    """Every component Copycast depends on; ``state`` is the worst of them (unknown counts ok)."""

    state: StatusState
    checked_at: datetime
    components: list[ComponentStatus]


class QueueStats(ReadModel):
    """The job queue as the status report reads it (not part of any response)."""

    queued: int = 0
    waiting: int = 0
    running: int = 0
    failed_recently: int = 0
    oldest_queued_at: datetime | None = None


class CrawlerStats(ReadModel):
    """Finished Refresh runs of one service since a moment (not part of any response)."""

    service: str | None
    runs: int = 0
    failed: int = 0
    last_success_at: datetime | None = None
    last_error: str | None = None


class RebuildRequest(RequestModel):
    dry_run: bool = True


class Problem(BaseModel):
    """RFC 9457 problem details; ``type`` is ``urn:copycast:problem:<slug>``."""

    model_config = ConfigDict(extra="allow", frozen=True)

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None

    @classmethod
    def problem_type(cls, slug: str) -> str:
        return f"urn:copycast:problem:{slug}"


__all__ = [
    "AboutRead",
    "ApiKeyCreate",
    "ApiKeyCreated",
    "ApiKeyList",
    "ApiKeyRead",
    "AssetRead",
    "AudioChapter",
    "AudioTags",
    "BackfillPolicy",
    "BackfillRequest",
    "BucketCreate",
    "BucketRead",
    "BucketUpdate",
    "CatalogCounts",
    "ComponentStatus",
    "CrawlerStats",
    "EngineCookiesRead",
    "EngineCookiesWrite",
    "EngineRead",
    "FeedCredentials",
    "FeedHealth",
    "FeedList",
    "FeedRead",
    "ItemMedia",
    "ItemPage",
    "ItemRead",
    "JobPage",
    "JobProgress",
    "JobRead",
    "MirrorChangePreview",
    "MirrorCreate",
    "MirrorDefaults",
    "MirrorRead",
    "MirrorUpdate",
    "PodcastSearchPage",
    "PodcastSearchResult",
    "ProbeCandidate",
    "ProbeRequest",
    "ProbeResult",
    "Problem",
    "PruneRequest",
    "PruneResult",
    "QueueStats",
    "ReadModel",
    "ReadyCheck",
    "ReadyRead",
    "RebuildRequest",
    "RequestCreate",
    "RequestModel",
    "RequestPage",
    "RequestRead",
    "SelectionRequest",
    "SelectionResult",
    "SelectionSummary",
    "SerialBucketCreate",
    "SerialEpisode",
    "StatusFact",
    "StatusRead",
    "StatusState",
    "Totals",
    "UploadCreate",
    "UploadRead",
    "VideoSearchPage",
    "VideoSearchResult",
]
