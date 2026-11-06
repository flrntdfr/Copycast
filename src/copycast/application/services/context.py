"""What a service needs from its surroundings, described structurally.

The application layer never imports an adapter. Services receive a
:class:`ServiceContext` (the composition root's container) and open units of
work through it; everything they touch is typed against the Protocols below,
which ``adapters.db`` (repositories, ORM rows), ``adapters.storage`` (layout)
and the gateways wired in ``copycast.app`` satisfy at runtime. The boundary
itself is dynamic: SQLAlchemy's ``Mapped`` descriptors are not statically
assignable to these row protocols, so the container exposes its factory and
layout untyped and the worker uses the concrete classes directly.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal, Protocol

from pydantic import BaseModel

from copycast.application.models import (
    AudioTags,
    CatalogCounts,
    CrawlerStats,
    FeedCredentials,
    PodcastSearchResult,
    ProbeCandidate,
    QueueStats,
    Totals,
    VideoSearchResult,
    YouTubePlaylist,
)
from copycast.application.ports import CancelToken, Engine, StoredObject
from copycast.domain.enums import (
    ArchiveState,
    AssetFormat,
    AssetKind,
    AssetProvenance,
    AssetState,
    BlockedReason,
    FeedKind,
    JobKind,
    JobStatus,
    JobTrigger,
    KeyScope,
    LiveStatus,
    LogLevel,
    Numbering,
    PrincipalVia,
    RefreshRunStatus,
    RequestedVia,
    SourceKind,
    WantedReason,
)
from copycast.domain.listing import SourceListing, SourceListingItem
from copycast.domain.selection import ResolvedSelection, SelectionCandidate
from copycast.domain.users import Principal
from copycast.settings import Settings

if TYPE_CHECKING:
    from copycast.application.users import UserDirectory

FeedSort = Literal["title", "created_at", "updated_at", "storage_bytes"]
ItemSort = Literal["published", "ordinal", "title", "added"]
Order = Literal["asc", "desc"]

DEFAULT_BUCKET_TITLE: Final = "Copycast"
"""The name every User's default Local feed starts with."""

# --------------------------------------------------------------------------- scope


@dataclass(frozen=True, slots=True)
class Scope:
    """What a unit of work stamps on new rows and which Owner's rows it may see.

    ``actor`` is the User rows the caller creates belong to (None for SYSTEM);
    ``visible_owner`` filters every Feed lookup (None sees every Owner: an Admin
    session, the open-mode Admin and SYSTEM).
    """

    actor: str | None
    visible_owner: str | None

    SYSTEM: ClassVar[Scope]

    @classmethod
    def for_principal(cls, principal: Principal) -> Scope:
        """SYSTEM sees all and stamps nothing; an Admin session or the open-mode Admin sees
        all; a Member sees their own; any API key, an Admin's included, sees its owner's own
        (a key never widens what its owner's Feeds are)."""
        if principal.is_system:
            return cls.SYSTEM
        if principal.via is PrincipalVia.api_key or not principal.admin:
            return cls(principal.username, principal.username)
        return cls(principal.username, None)


Scope.SYSTEM = Scope(None, None)


class LookupSentinel(Enum):
    """``UnitOfWorkPort.publish``'s default owner: look it up from the event."""

    LOOKUP = "lookup"


LOOKUP: Final = LookupSentinel.LOOKUP

# --------------------------------------------------------------------------- rows


class FeedRow(Protocol):
    """A ``feeds`` row; policy and metadata columns are mutable through the session."""

    @property
    def id(self) -> str: ...
    @property
    def kind(self) -> str: ...
    @property
    def is_default(self) -> bool: ...
    @property
    def created_at(self) -> datetime: ...
    @property
    def storage_bytes(self) -> int: ...
    @property
    def revision(self) -> int: ...
    @property
    def intent_version(self) -> int: ...
    @property
    def last_refresh_attempt_at(self) -> datetime | None: ...
    @property
    def last_refresh_success_at(self) -> datetime | None: ...
    @property
    def last_error(self) -> str | None: ...
    @property
    def last_light_refresh_at(self) -> datetime | None: ...

    owner: str
    title: str
    title_override: str | None
    description: str | None
    author: str | None
    artwork_url: str | None
    language: str | None
    auth_username: str
    auth_password: str
    source_url: str | None
    source_dedup_key: str | None
    source_kind: str | None
    service: str | None
    source_channel_xml: str | None
    source_etag: str | None
    source_last_modified: str | None
    backfill_mode: str | None
    backfill_latest_n: int | None
    min_duration_seconds: int | None
    preferred_language: str | None
    retention_days: int | None
    refresh_interval_hours: int | None
    sync_deletions: bool
    playlist_capture: bool
    serial: bool
    follow: bool
    paused: bool
    policy_applied_at: datetime | None
    engine_options: dict[str, Any]
    autoprune_days: int | None
    last_autoprune_at: datetime | None


class ItemRow(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def feed_id(self) -> str: ...
    @property
    def source_key(self) -> str: ...
    @property
    def ordinal(self) -> int: ...
    @property
    def source_number(self) -> int | None: ...
    @property
    def source_season(self) -> int | None: ...
    @property
    def title(self) -> str: ...
    @property
    def description(self) -> str | None: ...
    @property
    def author(self) -> str | None: ...
    @property
    def artwork_url(self) -> str | None: ...
    @property
    def published_at(self) -> datetime | None: ...
    @property
    def published_at_approximate(self) -> bool: ...
    @property
    def duration_seconds(self) -> int | None: ...
    @property
    def source_url(self) -> str | None: ...
    @property
    def archivable(self) -> bool: ...
    @property
    def live_status(self) -> str | None: ...
    @property
    def listed(self) -> bool: ...
    @property
    def first_seen_at(self) -> datetime: ...
    @property
    def archive_state(self) -> str: ...
    @property
    def wanted_reason(self) -> str | None: ...
    @property
    def blocked_reason(self) -> str | None: ...
    @property
    def blocked_bytes(self) -> int | None: ...
    @property
    def archive_flag(self) -> bool: ...
    @property
    def attempt_count(self) -> int: ...
    @property
    def last_error(self) -> str | None: ...
    @property
    def archived_at(self) -> datetime | None: ...
    @property
    def media_path(self) -> str | None: ...
    @property
    def media_mime(self) -> str | None: ...
    @property
    def media_bytes(self) -> int | None: ...
    @property
    def media_ext(self) -> str | None: ...
    @property
    def public_ext(self) -> str: ...
    @property
    def download_count(self) -> int: ...
    @property
    def first_downloaded_at(self) -> datetime | None: ...
    @property
    def last_downloaded_at(self) -> datetime | None: ...


class AssetRow(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def feed_id(self) -> str: ...
    @property
    def item_id(self) -> str | None: ...
    @property
    def kind(self) -> str: ...
    @property
    def provenance(self) -> str: ...
    @property
    def language(self) -> str | None: ...
    @property
    def format(self) -> str | None: ...
    @property
    def remote_url(self) -> str | None: ...
    @property
    def local_path(self) -> str | None: ...
    @property
    def mime(self) -> str | None: ...
    @property
    def size_bytes(self) -> int | None: ...
    @property
    def state(self) -> str: ...
    @property
    def last_error(self) -> str | None: ...
    @property
    def fetched_at(self) -> datetime | None: ...


class JobRow(Protocol):
    @property
    def id(self) -> uuid.UUID: ...
    @property
    def kind(self) -> str: ...
    @property
    def trigger(self) -> str: ...
    @property
    def feed_id(self) -> str | None: ...
    @property
    def item_id(self) -> str | None: ...
    @property
    def request_id(self) -> uuid.UUID | None: ...
    @property
    def requested_by(self) -> str | None: ...
    @property
    def payload(self) -> dict[str, Any]: ...
    @property
    def status(self) -> str: ...
    @property
    def priority(self) -> int: ...
    @property
    def attempt(self) -> int: ...
    @property
    def max_attempts(self) -> int: ...
    @property
    def cancel_requested(self) -> bool: ...
    @property
    def progress(self) -> dict[str, Any] | None: ...
    @property
    def result(self) -> dict[str, Any] | None: ...
    @property
    def error(self) -> str | None: ...
    @property
    def error_kind(self) -> str | None: ...
    @property
    def created_at(self) -> datetime: ...
    @property
    def started_at(self) -> datetime | None: ...
    @property
    def finished_at(self) -> datetime | None: ...
    @property
    def is_active(self) -> bool: ...


class RequestRow(Protocol):
    @property
    def id(self) -> uuid.UUID: ...
    @property
    def feed_id(self) -> str: ...
    @property
    def url(self) -> str: ...
    @property
    def requested_via(self) -> str: ...
    @property
    def status(self) -> str: ...
    @property
    def item_count(self) -> int: ...
    @property
    def error(self) -> str | None: ...
    @property
    def created_at(self) -> datetime: ...


class ApiKeyRow(Protocol):
    @property
    def id(self) -> uuid.UUID: ...
    @property
    def owner(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def scope(self) -> str: ...
    @property
    def prefix(self) -> str: ...
    @property
    def created_at(self) -> datetime: ...
    @property
    def last_used_at(self) -> datetime | None: ...


class UserRow(Protocol):
    """A ``users`` row: a configured (or formerly configured) User; no secret."""

    @property
    def username(self) -> str: ...
    @property
    def first_seen_at(self) -> datetime: ...
    @property
    def last_login_at(self) -> datetime | None: ...
    @property
    def totp_last_step(self) -> int | None: ...
    @property
    def credential_digest(self) -> bytes | None: ...


class SessionRow(Protocol):
    """A ``sessions`` row: one browser login, keyed by the SHA-256 of its token."""

    @property
    def id_hash(self) -> bytes: ...
    @property
    def username(self) -> str: ...
    @property
    def created_at(self) -> datetime: ...
    @property
    def last_seen_at(self) -> datetime: ...
    @property
    def expires_at(self) -> datetime: ...
    @property
    def user_agent(self) -> str | None: ...


class LoginAttemptRow(Protocol):
    """A ``login_attempts`` row: the failures and lock of one typed username."""

    @property
    def username(self) -> str: ...
    @property
    def failed_count(self) -> int: ...
    @property
    def locked_until(self) -> datetime | None: ...
    @property
    def updated_at(self) -> datetime: ...


class RefreshRunRow(Protocol):
    @property
    def id(self) -> int: ...


class HeartbeatRow(Protocol):
    @property
    def worker_id(self) -> str: ...
    @property
    def seen_at(self) -> datetime: ...
    @property
    def status(self) -> dict[str, Any]: ...


class ListingUpsertResult(Protocol):
    @property
    def listed_count(self) -> int: ...
    @property
    def new_count(self) -> int: ...
    @property
    def delisted_count(self) -> int: ...
    @property
    def new_item_ids(self) -> list[str]: ...
    @property
    def seen_item_ids(self) -> list[str]: ...
    @property
    def wanted_item_ids(self) -> list[str]: ...
    @property
    def archivable_changed_count(self) -> int: ...


# --------------------------------------------------------------------------- repositories


class FeedRepositoryPort(Protocol):
    async def get(self, feed_id: str) -> FeedRow | None: ...
    async def require(self, feed_id: str) -> FeedRow: ...
    async def get_for_update(self, feed_id: str) -> FeedRow: ...
    async def try_get_for_update(self, feed_id: str) -> FeedRow | None: ...
    async def by_dedup_key(self, owner: str, key: str) -> FeedRow | None:
        """``owner``'s Mirror of the Source ``key``: by owner, never by visibility."""
        ...

    async def exists_id(self, feed_id: str) -> bool:
        """Whether any Feed, whoever owns it, has this id (the id collision check)."""
        ...

    async def mirrors_due_expiry(
        self, before: datetime, *, configured: Collection[str]
    ) -> Sequence[FeedRow]: ...
    async def list(
        self, kind: FeedKind | None = None, *, sort: FeedSort = "title", order: Order = "asc"
    ) -> Sequence[FeedRow]: ...
    async def find_bucket(self, ident: str) -> FeedRow | None:
        """A visible Local feed by id, else by name, the actor's own first."""
        ...

    async def default_bucket(self, owner: str) -> FeedRow | None: ...
    async def totals(self) -> Totals: ...
    async def add(self, feed: Any) -> FeedRow: ...
    async def delete(self, feed_id: str) -> bool: ...
    async def ensure_default_bucket(
        self, owner: str, title: str = DEFAULT_BUCKET_TITLE
    ) -> FeedRow: ...
    async def bump_revision(self, feed_id: str) -> int: ...
    async def recount_storage(self, feed_id: str) -> int: ...
    async def set_light_refresh_at(self, feed_id: str, at: datetime | None = None) -> None: ...
    async def apply_metadata(
        self,
        feed_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        author: str | None = None,
        artwork_url: str | None = None,
        language: str | None = None,
        service: str | None = None,
    ) -> None: ...


class CatalogRepositoryPort(Protocol):
    async def get(self, item_id: str) -> ItemRow | None: ...
    async def require(self, item_id: str, feed_id: str | None = None) -> ItemRow: ...
    async def get_many(self, item_ids: Iterable[str]) -> Sequence[ItemRow]: ...
    async def list_page(
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
    ) -> tuple[Sequence[ItemRow], int]: ...
    async def count_by_state(self, feed_id: str) -> CatalogCounts: ...
    async def count_by_state_many(self, feed_ids: Iterable[str]) -> dict[str, CatalogCounts]: ...
    async def selection_candidates(self, feed_id: str) -> Sequence[SelectionCandidate]: ...
    async def resolve_numbers(
        self,
        feed_id: str,
        *,
        numbers: Sequence[int] = (),
        item_ids: Sequence[str] = (),
        numbering: Numbering = Numbering.source,
    ) -> ResolvedSelection: ...
    async def ids_in_state(self, feed_id: str, state: ArchiveState) -> Sequence[str]: ...
    async def delisted_archived(self, feed_id: str) -> Sequence[ItemRow]: ...
    async def available_ids(
        self,
        feed_id: str,
        *,
        first_seen_after: datetime | None = None,
        latest_n: int | None = None,
        min_duration_seconds: int | None = None,
        include_deleted: bool = False,
    ) -> Sequence[str]: ...
    async def window_ids(
        self, feed_id: str, size: int, *, min_duration_seconds: int | None = None
    ) -> Sequence[str]: ...
    async def prunable(
        self,
        feed_id: str,
        *,
        downloaded: bool = False,
        added_before: datetime | None = None,
        downloaded_before: datetime | None = None,
        last_activity_before: datetime | None = None,
    ) -> Sequence[ItemRow]: ...
    async def upsert_listing(
        self,
        feed_id: str,
        listing: SourceListing,
        *,
        now: datetime | None = None,
        wanted_reason: WantedReason | None = None,
        partial: bool = False,
    ) -> ListingUpsertResult: ...
    async def set_wanted(self, item_ids: Iterable[str], reason: WantedReason) -> Sequence[str]: ...
    async def fill_metadata(
        self,
        item_id: str,
        *,
        description: str | None = None,
        published_at: datetime | None = None,
        author: str | None = None,
        artwork_url: str | None = None,
        overwrite: bool = False,
    ) -> bool: ...
    async def mark_state(
        self,
        item_id: str,
        state: ArchiveState,
        *,
        only_from: ArchiveState | Sequence[ArchiveState] | None = None,
        error: str | None = None,
        count_attempt: bool = False,
        media_path: str | None = None,
        media_mime: str | None = None,
        media_bytes: int | None = None,
        duration_seconds: int | None = None,
        archived_at: datetime | None = None,
        wanted_reason: WantedReason | None = None,
    ) -> bool: ...
    async def tombstone(
        self, item_id: str, *, listed: bool, at: datetime | None = None
    ) -> bool: ...
    async def record_download(self, item_id: str, at: datetime | None = None) -> bool: ...
    async def set_archive_flag(self, item_id: str, flag: bool) -> None: ...
    async def lock_archived(self, feed_id: str) -> int: ...
    async def set_live_status(self, item_id: str, status: LiveStatus | None) -> None: ...


class AssetRepositoryPort(Protocol):
    async def for_item(self, item_id: str) -> Sequence[AssetRow]: ...
    async def for_items(self, item_ids: Iterable[str]) -> Mapping[str, Sequence[AssetRow]]: ...
    async def feed_artwork(self, feed_id: str) -> AssetRow | None: ...
    async def upsert(
        self,
        feed_id: str,
        item_id: str | None,
        kind: AssetKind,
        *,
        provenance: AssetProvenance = AssetProvenance.mirrored,
        language: str | None = None,
        format: AssetFormat | None = None,
        remote_url: str | None = None,
        local_path: str | None = None,
        mime: str | None = None,
        size_bytes: int | None = None,
        state: AssetState = AssetState.wanted,
        last_error: str | None = None,
        fetched_at: datetime | None = None,
        slot: str | None = None,
    ) -> AssetRow: ...
    async def delete_for_item(self, item_id: str) -> Sequence[AssetRow]: ...


class RequestRepositoryPort(Protocol):
    async def get(self, request_id: uuid.UUID) -> RequestRow | None: ...
    async def require(self, request_id: uuid.UUID, feed_id: str | None = None) -> RequestRow: ...
    async def add(
        self, feed_id: str, url: str, via: RequestedVia, *, request_id: uuid.UUID | None = None
    ) -> RequestRow: ...
    async def list_page(
        self, feed_id: str, *, limit: int = 100, offset: int = 0
    ) -> tuple[Sequence[RequestRow], int]: ...
    async def count_for_feeds(self, feed_ids: Iterable[str]) -> Mapping[str, int]: ...
    async def link_items(self, request_id: uuid.UUID, item_ids: Iterable[str]) -> int: ...
    async def item_ids(self, request_id: uuid.UUID) -> Sequence[str]: ...
    async def item_ids_many(
        self, request_ids: Iterable[uuid.UUID]
    ) -> Mapping[uuid.UUID, Sequence[str]]: ...
    async def request_ids_for_items(
        self, item_ids: Iterable[str]
    ) -> Mapping[str, Sequence[uuid.UUID]]: ...
    async def mark_expanded(
        self, request_id: uuid.UUID, item_count: int, *, at: datetime | None = None
    ) -> None: ...
    async def mark_failed(self, request_id: uuid.UUID, error: str) -> None: ...


class JobRepositoryPort(Protocol):
    async def get(self, job_id: uuid.UUID) -> JobRow | None: ...
    async def require(self, job_id: uuid.UUID) -> JobRow: ...
    async def list_page(
        self,
        *,
        feed_id: str | None = None,
        kind: JobKind | None = None,
        status: JobStatus | Sequence[JobStatus] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Sequence[JobRow], int]: ...
    async def active(
        self, *, feed_id: str | None = None, kind: JobKind | None = None
    ) -> Sequence[JobRow]: ...
    async def active_by_dedup_key(self, key: str) -> JobRow | None: ...
    async def latest_for_request(self, request_id: uuid.UUID) -> JobRow | None: ...
    async def latest_for_item(self, item_id: str) -> JobRow | None: ...
    async def enqueue(
        self,
        kind: JobKind,
        trigger: JobTrigger,
        *,
        feed_id: str | None = None,
        item_id: str | None = None,
        request_id: uuid.UUID | None = None,
        payload: dict[str, Any] | None = None,
        priority: int = ...,
        max_attempts: int | None = None,
        run_after: datetime | None = None,
        dedup: str | None = None,
        job_id: uuid.UUID | None = None,
    ) -> JobRow | None: ...
    async def cancel(self, job_id: uuid.UUID) -> JobRow: ...
    async def cancel_for_feed(
        self, feed_id: str, *, kinds: Iterable[JobKind] | None = None
    ) -> int: ...
    async def queue_stats(self, *, now: datetime, since: datetime) -> QueueStats: ...
    async def requeue_stale(self, older_than: timedelta) -> Sequence[uuid.UUID]: ...
    async def purge_finished(self, before: datetime) -> int: ...
    async def append_log(
        self, job_id: uuid.UUID, lines: Sequence[tuple[int, datetime, LogLevel, str]]
    ) -> int: ...


class ApiKeyRepositoryPort(Protocol):
    async def get(self, key_id: uuid.UUID) -> ApiKeyRow | None: ...
    async def require(self, key_id: uuid.UUID) -> ApiKeyRow: ...
    async def by_hash(self, key_hash: str) -> ApiKeyRow | None: ...
    async def list(self) -> Sequence[ApiKeyRow]: ...
    async def add(
        self, *, owner: str, name: str, scope: KeyScope, key_hash: str, prefix: str
    ) -> ApiKeyRow: ...
    async def delete(self, key_id: uuid.UUID) -> bool: ...
    async def touch(self, key_id: uuid.UUID, at: datetime | None = None) -> None: ...


class InstanceSecretRepositoryPort(Protocol):
    async def get_or_create(self, name: str, mint: Callable[[], bytes]) -> bytes:
        """The installation's secret called ``name``, minted with ``mint`` when absent."""
        ...


class UserRepositoryPort(Protocol):
    async def ensure(self, usernames: Iterable[str]) -> None:
        """Insert the missing ``users`` rows; never deletes one."""
        ...

    async def all(self) -> list[UserRow]: ...
    async def touch_login(self, username: str, at: datetime) -> None: ...
    async def claim_totp_step(self, username: str, step: int) -> bool:
        """Store ``step`` as the last TOTP step unless one as late is stored already."""
        ...

    async def totp_last_step(self, username: str) -> int | None: ...
    async def credential_digests(self) -> dict[str, bytes | None]: ...
    async def set_credential_digest(
        self, username: str, digest: bytes, *, reset_totp: bool
    ) -> None: ...


class SessionRepositoryPort(Protocol):
    async def add(
        self,
        digest: bytes,
        username: str,
        *,
        now: datetime,
        expires_at: datetime,
        user_agent: str | None,
    ) -> None: ...
    async def get(self, digest: bytes) -> SessionRow | None: ...
    async def touch(self, digest: bytes, *, now: datetime, expires_at: datetime) -> None: ...
    async def delete(self, digest: bytes) -> None: ...
    async def delete_expired(self, now: datetime) -> int: ...
    async def delete_for_users_not_in(self, usernames: Collection[str]) -> int: ...
    async def delete_for_user(self, username: str) -> int: ...


@dataclass(frozen=True, slots=True)
class AttemptClaim:
    """What :meth:`LoginAttemptRepositoryPort.claim` decided for one sign-in attempt."""

    allowed: bool
    """True: the attempt was counted as a failure up front and may run its verify."""
    locked_until: datetime | None
    """Refused: the lock in force. Allowed: the lock the attempt leaves if it fails."""


class LoginAttemptRepositoryPort(Protocol):
    async def get(self, username: str) -> LoginAttemptRow | None: ...
    async def claim(self, username: str, now: datetime) -> AttemptClaim:
        """Atomically check the lock and count one failure before the verify runs.

        Refused (nothing counted) while ``locked_until > now``; otherwise the count goes
        up by one and, from the lock threshold on, the lock is set at once, so concurrent
        attempts (in any process) never verify more guesses than the threshold allows.
        A success then resets the row.
        """
        ...

    async def reset(self, username: str) -> None: ...
    async def prune(self, now: datetime) -> int:
        """Delete unlocked rows untouched for 24 hours."""
        ...


class QuotaRepositoryPort(Protocol):
    async def owned_storage_bytes(self, owner: str) -> int:
        """``SUM(feeds.storage_bytes)`` over ``owner``'s Feeds."""
        ...

    async def storage_by_owner(self) -> dict[str, int]:
        """Usage of every Owner having Feeds."""
        ...

    async def mark_blocked(
        self, item_id: str, reason: BlockedReason, *, estimate_bytes: int | None
    ) -> None:
        """``archive_state`` -> wanted, with the reason and the estimate; publishes nothing."""
        ...

    async def owners_with_blocked(self) -> list[str]: ...
    async def blocked_for_release(self, owner: str, *, limit: int) -> Sequence[ItemRow]:
        """``owner``'s blocked items in archive priority, unknown sizes first, newest first."""
        ...

    async def unblock(self, item_ids: Collection[str]) -> None:
        """Clear ``blocked_reason`` and ``blocked_bytes``."""
        ...


class TelemetryRepositoryPort(Protocol):
    async def start_refresh_run(
        self,
        feed_id: str,
        *,
        trigger: JobTrigger,
        job_id: uuid.UUID | None = None,
        light: bool = False,
    ) -> RefreshRunRow: ...
    async def finish_refresh_run(
        self,
        run_id: int,
        status: RefreshRunStatus,
        *,
        listed_count: int = 0,
        new_count: int = 0,
        delisted_count: int = 0,
        wanted_count: int = 0,
        error: str | None = None,
        engine_version: str | None = None,
    ) -> None: ...
    async def crawler_stats(self, since: datetime) -> list[CrawlerStats]: ...
    async def purge_refresh_runs(self, before: datetime) -> int: ...
    async def heartbeat(self, worker_id: str, status: dict[str, Any]) -> None: ...
    async def latest_heartbeat(self) -> HeartbeatRow | None: ...
    async def record_engine_version(
        self,
        version: str,
        *,
        channel: str | None = None,
        release_date: Any = None,
        git_head: str | None = None,
        app_version: str | None = None,
    ) -> Any: ...


# --------------------------------------------------------------------------- unit of work


class ObjectStorePort(Protocol):
    """The slice of ``adapters.storage.objects.ObjectStore`` the services use."""

    @property
    def url(self) -> str: ...
    def head(self, key: str) -> StoredObject | None: ...
    def get_bytes(self, key: str) -> bytes | None: ...
    def put_bytes(self, key: str, data: bytes | str, *, content_type: str | None = None) -> int: ...
    def put_file(self, key: str, path: Path, *, content_type: str | None = None) -> int: ...
    def copy(self, source: str, target: str) -> None: ...
    def delete(self, keys: str | Iterable[str]) -> None: ...
    def delete_prefix(self, prefix: str) -> int: ...
    def list(self, prefix: str) -> list[StoredObject]: ...
    def list_dirs(self, prefix: str) -> list[str]: ...


class ScratchPort(Protocol):
    """The slice of ``adapters.storage.scratch.Scratch`` the services use."""

    def temp_file(self, suffix: str) -> Path: ...


class AssetNameLike(Protocol):
    """A parsed ``assets/`` basename (``adapters.storage.layout.AssetFileName``)."""

    @property
    def kind(self) -> AssetKind: ...
    @property
    def item_id(self) -> str | None: ...


class LayoutPort(Protocol):
    """The slice of ``adapters.storage.layout.Layout`` the services and jobs use."""

    @property
    def store(self) -> ObjectStorePort: ...
    def read_version(self) -> str | None: ...
    def cookies_key(self) -> str: ...
    def cookie_files_prefix(self) -> str: ...
    def uploads_prefix(self) -> str: ...
    def defaults_key(self) -> str: ...
    def user_prefix(self, username: str) -> str: ...
    def user_defaults_key(self, username: str) -> str: ...
    def user_cookies_key(self, username: str) -> str: ...
    def user_cookie_files_prefix(self, username: str) -> str: ...
    def user_cookie_file_key(self, username: str, name: str) -> str: ...
    def feeds_prefix(self) -> str: ...
    def feed_prefix(self, feed_id: str) -> str: ...
    def descriptor_key(self, feed_id: str) -> str: ...
    def media_prefix(self, feed_id: str) -> str: ...
    def assets_prefix(self, feed_id: str) -> str: ...
    def source_xml_key(self, feed_id: str) -> str: ...
    def source_listing_key(self, feed_id: str) -> str: ...
    def media_key(self, feed_id: str, item_id: str, ext: str) -> str: ...
    def info_json_key(self, feed_id: str, item_id: str) -> str: ...
    def item_xml_key(self, feed_id: str, item_id: str) -> str: ...
    def relative(self, feed_id: str, key: str) -> str: ...
    def resolve(self, feed_id: str, relative: str) -> str: ...
    def read_json(self, key: str) -> Any | None: ...
    def write_json(self, key: str, payload: Any, *, indent: int | None = 2) -> int: ...
    def remove_feed(self, feed_id: str) -> bool: ...
    @staticmethod
    def parse_asset_name(name: str) -> AssetNameLike | None: ...
    @staticmethod
    def media_stem(name: str) -> str | None: ...


Hook = Callable[[], Awaitable[None] | None]


class UnitOfWorkPort(Protocol):
    """One transaction: ``async with ctx.uow_factory() as uow`` commits on a clean exit."""

    scope: Scope

    @property
    def feeds(self) -> FeedRepositoryPort: ...
    @property
    def catalog(self) -> CatalogRepositoryPort: ...
    @property
    def assets(self) -> AssetRepositoryPort: ...
    @property
    def requests(self) -> RequestRepositoryPort: ...
    @property
    def jobs(self) -> JobRepositoryPort: ...
    @property
    def telemetry(self) -> TelemetryRepositoryPort: ...
    @property
    def api_keys(self) -> ApiKeyRepositoryPort: ...
    @property
    def users(self) -> UserRepositoryPort: ...
    @property
    def sessions(self) -> SessionRepositoryPort: ...
    @property
    def login_attempts(self) -> LoginAttemptRepositoryPort: ...
    @property
    def instance_secrets(self) -> InstanceSecretRepositoryPort: ...
    @property
    def quota(self) -> QuotaRepositoryPort: ...
    @property
    def layout(self) -> LayoutPort: ...

    def after_commit(self, hook: Hook) -> None: ...
    async def update_intent(self, feed_id: str, *, debounce: bool = False) -> tuple[int, int]: ...
    async def bump_revision(self, feed_id: str) -> int: ...
    async def publish(
        self, event: BaseModel, *, owner: str | LookupSentinel | None = LOOKUP
    ) -> None:
        """NOTIFY ``event`` on commit; ``owner`` routes it to that User's subscribers.

        The default looks the owner up from the event's Feed (or job); a caller that
        publishes after deleting the Feed passes it explicitly.
        """
        ...

    async def notify_jobs(self, payload: str = "") -> None: ...
    async def flush(self) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    async def __aenter__(self) -> UnitOfWorkPort: ...
    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...


class UnitOfWorkFactoryPort(Protocol):
    def __call__(self, scope: Scope = Scope.SYSTEM) -> UnitOfWorkPort: ...
    async def flush_exports(self) -> None: ...
    async def aclose(self) -> None: ...


# --------------------------------------------------------------------------- gateways


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    """One probed Source: the candidate, its listing and, for RSS, the verbatim document.

    ``channel_xml`` is the ``<rss>`` root minus items (``feeds.source_channel_xml``);
    ``body`` is what ``source/feed.xml`` receives. yt-dlp Sources carry the
    listing only (``source/listing.json`` is written from ``listing.raw``).
    """

    candidate: ProbeCandidate
    listing: SourceListing
    body: bytes | None = None
    etag: str | None = None
    last_modified: str | None = None
    channel_xml: str | None = None

    @property
    def source_url(self) -> str:
        return self.candidate.source_url

    @property
    def source_kind(self) -> SourceKind:
        return self.candidate.source_kind


@dataclass(frozen=True, slots=True)
class RssFetch:
    """One conditional GET of an RSS Source (a light Refresh's listing).

    ``not_modified`` marks a 304: ``listing``, ``body`` and ``channel_xml`` are
    then None and ``etag``/``last_modified`` carry what the Source answered or
    what was sent. Otherwise ``listing`` is the document's complete listing
    (first page only), ``body`` the verbatim bytes for ``source/feed.xml`` and
    ``channel_xml`` the ``<rss>`` root minus items.
    """

    not_modified: bool
    listing: SourceListing | None
    body: bytes | None = None
    etag: str | None = None
    last_modified: str | None = None
    channel_xml: str | None = None


class SourceGateway(Protocol):
    """Probing, searching and Source snapshots; every method is synchronous and blocking.

    ``owner`` is the User whose Cookie files an Engine call carries (and whose probe
    cache it reads): the Feed's Owner for its Refreshes and inspections, the acting
    User for probes, searches and playlists.
    """

    def probe(
        self, url: str, *, options: Mapping[str, Any], cancel: CancelToken, owner: str
    ) -> list[SourceSnapshot]: ...
    def cached(
        self, *, token: str | None, url: str | None, owner: str
    ) -> SourceSnapshot | None: ...
    def search_podcasts(self, query: str, limit: int) -> list[PodcastSearchResult]: ...
    def search_videos(self, query: str, limit: int, *, owner: str) -> list[VideoSearchResult]: ...
    def list_playlists(self, *, owner: str) -> list[YouTubePlaylist]: ...
    def inspect_video(
        self,
        url: str,
        *,
        feed_id: str,
        options: Mapping[str, Any],
        language: str | None,
        owner: str,
    ) -> SourceListingItem | None: ...
    def save_snapshot(self, feed_id: str, snapshot: SourceSnapshot) -> None: ...
    def fetch_rss(
        self,
        url: str,
        *,
        etag: str | None,
        last_modified: str | None,
        timeout: float | None = None,
    ) -> RssFetch: ...
    def list_shallow(
        self,
        url: str,
        *,
        feed_id: str,
        options: Mapping[str, Any],
        language: str | None,
        cancel: CancelToken,
        limit: int,
        owner: str,
    ) -> SourceListing: ...
    def save_rss_snapshot(self, feed_id: str, body: bytes) -> bool: ...
    def read_audio_tags(self, path: Path) -> AudioTags | None: ...


class PublicUrls(Protocol):
    """The URLs Copycast publishes (``adapters.feeds.urls`` bound to ``base_url``).

    While authentication is on, ``feed_url`` carries the feed's own pair as
    ``user:pass@`` and ``feed_credentials`` exposes it; otherwise both stay bare.
    ``artwork_url`` is the public ``/art/`` URL of an artwork asset in both modes;
    it needs the instance secret, which ``prepare`` reads once per process.
    """

    async def prepare(self) -> None: ...

    def feed_url(
        self, feed_id: str, *, username: str | None = None, password: str | None = None
    ) -> str: ...
    def feed_credentials(self, username: str, password: str) -> FeedCredentials | None: ...
    def media_url(self, feed_id: str, item_id: str, ext: str) -> str: ...
    def asset_url(self, feed_id: str, local_path: str) -> str: ...
    def artwork_url(self, asset_id: str, local_path: str) -> str: ...
    def feed_object_url(self, feed_id: str, path: str) -> str: ...
    def play_media_url(self, feed_id: str, item_id: str) -> str:
        """The web player's media URL (``/api/feeds/{f}/items/{i}/media``), session only."""
        ...

    def play_asset_url(self, feed_id: str, item_id: str, name: str) -> str:
        """The web player's URL of an item asset, by its local name, session only."""
        ...


class RowFactory(Protocol):
    """Builds new ORM rows the repositories have no constructor for."""

    def feed(self, **values: Any) -> Any: ...


class ServiceContext(Protocol):
    """The container as the services see it."""

    @property
    def settings(self) -> Settings: ...
    @property
    def principal(self) -> Principal:
        """Whoever the services act as (the Container: ``Principal.SYSTEM``)."""
        ...

    @property
    def users(self) -> UserDirectory: ...
    @property
    def engine(self) -> Engine: ...
    @property
    def uow_factory(self) -> UnitOfWorkFactoryPort: ...
    @property
    def layout(self) -> LayoutPort: ...
    @property
    def scratch(self) -> ScratchPort: ...
    @property
    def sources(self) -> SourceGateway: ...
    @property
    def urls(self) -> PublicUrls: ...
    @property
    def rows(self) -> RowFactory: ...
    @property
    def expected_layout_version(self) -> str: ...
    async def schema_is_current(self) -> bool: ...


__all__ = [
    "DEFAULT_BUCKET_TITLE",
    "LOOKUP",
    "ApiKeyRepositoryPort",
    "ApiKeyRow",
    "AssetNameLike",
    "AssetRepositoryPort",
    "AssetRow",
    "AttemptClaim",
    "CatalogRepositoryPort",
    "FeedRepositoryPort",
    "FeedRow",
    "FeedSort",
    "HeartbeatRow",
    "Hook",
    "InstanceSecretRepositoryPort",
    "ItemRow",
    "ItemSort",
    "JobRepositoryPort",
    "JobRow",
    "LayoutPort",
    "ListingUpsertResult",
    "LoginAttemptRepositoryPort",
    "LoginAttemptRow",
    "LookupSentinel",
    "ObjectStorePort",
    "Order",
    "PublicUrls",
    "QuotaRepositoryPort",
    "RefreshRunRow",
    "RequestRepositoryPort",
    "RequestRow",
    "RowFactory",
    "RssFetch",
    "Scope",
    "ScratchPort",
    "ServiceContext",
    "SessionRepositoryPort",
    "SessionRow",
    "SourceGateway",
    "SourceSnapshot",
    "TelemetryRepositoryPort",
    "UnitOfWorkFactoryPort",
    "UnitOfWorkPort",
    "UserRepositoryPort",
    "UserRow",
]
