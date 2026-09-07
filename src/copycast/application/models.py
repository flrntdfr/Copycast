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
    Numbering,
    ProgressPhase,
    RequestedVia,
    RequestStatus,
    SourceKind,
)
from copycast.domain.language import normalize_language

INBOX_NAME_MAX_LEN = 80
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
    latest_n: int | None = None


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
    paused: bool = False
    follow: bool = True
    backfill: BackfillPolicy
    engine_options: dict[str, Any] = Field(default_factory=dict[str, Any])
    last_refresh_attempt_at: datetime | None = None
    last_refresh_success_at: datetime | None = None
    last_error: str | None = None
    health: FeedHealth
    counts: CatalogCounts = Field(default_factory=CatalogCounts)
    selection: SelectionSummary | None = None


class InboxRead(_FeedReadBase):
    kind: Literal[FeedKind.inbox] = FeedKind.inbox
    name: str
    autoprune_days: int | None = None
    request_count: int = 0


FeedRead = Annotated[MirrorRead | InboxRead, Field(discriminator="kind")]


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
    added_at: datetime = Field(description="first_seen_at")
    duration_seconds: int | None = None
    state: ArchiveState
    listed: bool = True
    attempt_count: int = 0
    last_error: str | None = None
    media: ItemMedia | None = None
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


class VideoSearchResult(ReadModel):
    """One YouTube search hit; ``url`` is what ``add_to_inbox`` or ``probe_source`` takes."""

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


class BackfillRequest(RequestModel):
    """Backfill policy at creation; ``selection`` implies mode ``selection``."""

    mode: BackfillMode = BackfillMode.all
    latest_n: int | None = Field(default=None, ge=1)
    selection: str | None = Field(default=None, max_length=SELECTION_MAX_LEN)

    @model_validator(mode="after")
    def _consistent(self) -> BackfillRequest:
        if self.selection is not None and self.selection.strip():
            if "mode" in self.model_fields_set and self.mode is not BackfillMode.selection:
                raise ValueError("selection given: backfill.mode must be 'selection'")
            self.mode = BackfillMode.selection
        if self.mode is BackfillMode.latest and self.latest_n is None:
            raise ValueError("backfill.mode 'latest' requires latest_n")
        if self.mode is not BackfillMode.latest:
            self.latest_n = None
        return self


class MirrorCreate(RequestModel):
    source_url: str = Field(min_length=1, max_length=2048)
    candidate_token: str | None = None
    backfill: BackfillRequest = Field(default_factory=BackfillRequest)
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

    @model_validator(mode="after")
    def _follow_default(self) -> MirrorCreate:
        if self.follow is None:
            self.follow = self.backfill.mode is not BackfillMode.selection
        return self

    @property
    def effective_follow(self) -> bool:
        return bool(self.follow)


class MirrorUpdate(RequestModel):
    """PATCH body; only the fields present in ``model_fields_set`` are applied."""

    source_url: str | None = Field(default=None, min_length=1, max_length=2048)
    follow: bool | None = None
    backfill: BackfillRequest | None = None
    engine_options: dict[str, Any] | None = None
    preferred_language: str | None = Field(default=None, max_length=16)
    min_duration_seconds: int | None = Field(default=None, ge=1)

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


class SelectionResult(ReadModel):
    resolved: list[str]
    unresolved: list[str]
    numbering_used: Numbering
    already_archived_count: int
    jobs: list[JobRead] = Field(default_factory=list[JobRead])
    dry_run: bool = False


# --------------------------------------------------------------------------- inboxes and requests


def _clean_name(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("name must not be blank")
    return value


class InboxCreate(RequestModel):
    name: str = Field(min_length=1, max_length=INBOX_NAME_MAX_LEN)
    autoprune_days: int | None = Field(default=None, ge=1)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _clean_name(value)


class InboxUpdate(RequestModel):
    """PATCH body; ``autoprune_days: null`` present in the body switches autoprune off."""

    name: str | None = Field(default=None, min_length=1, max_length=INBOX_NAME_MAX_LEN)
    autoprune_days: int | None = Field(default=None, ge=1)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return None if value is None else _clean_name(value)


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
    inbox_id: str
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

    @field_validator("language")
    @classmethod
    def _language(cls, value: str | None) -> str | None:
        return normalize_language(value)


# --------------------------------------------------------------------------- engine cookies


class EngineCookiesWrite(RequestModel):
    """A Netscape ``cookies.txt`` as exported by a browser extension."""

    content: str = Field(min_length=1, max_length=1024 * 1024)


class EngineCookiesRead(ReadModel):
    """The stored cookie file without any value: presence, size, where it applies."""

    present: bool
    size_bytes: int = 0
    updated_at: datetime | None = None
    cookie_count: int = 0
    domains: list[str] = Field(default_factory=list[str])
    youtube: bool = Field(default=False, description="Whether a youtube.com cookie is stored")


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
    "BackfillPolicy",
    "BackfillRequest",
    "CatalogCounts",
    "EngineCookiesRead",
    "EngineCookiesWrite",
    "EngineRead",
    "FeedCredentials",
    "FeedHealth",
    "FeedList",
    "FeedRead",
    "InboxCreate",
    "InboxRead",
    "InboxUpdate",
    "ItemMedia",
    "ItemPage",
    "ItemRead",
    "JobPage",
    "JobProgress",
    "JobRead",
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
    "Totals",
    "VideoSearchPage",
    "VideoSearchResult",
]
