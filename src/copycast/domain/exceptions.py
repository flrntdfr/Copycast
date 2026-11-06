"""Domain exceptions. Each carries the RFC 9457 problem slug the API maps it to."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


class DomainError(Exception):
    """Base class; ``slug`` selects the problem type, ``extras`` its extension members."""

    slug: str = "internal"
    status: int = 500

    def __init__(self, message: str, **extras: Any) -> None:
        super().__init__(message)
        self.message = message
        self.extras: dict[str, Any] = extras

    def __str__(self) -> str:
        return self.message


class NotFound(DomainError):
    slug = "not-found"
    status = 404

    def __init__(self, kind: str, ident: str | None = None) -> None:
        self.kind = kind
        self.ident = ident
        detail = f"{kind} {ident!r} not found" if ident is not None else f"{kind} not found"
        super().__init__(detail)


class Conflict(DomainError):
    """The request contradicts current state (409)."""

    slug = "conflict"
    status = 409


class ConcurrentUpdate(Conflict):
    """The transaction lost a race (Postgres deadlock or serialization failure) and was rolled back.

    Services that mutate rows a running job may also touch retry the whole unit of work;
    when the retries are exhausted the API answers 409 ``conflict``.
    """

    def __init__(self, message: str = "the request lost a concurrent update; retry") -> None:
        super().__init__(message)


class FeedExists(Conflict):
    """A Mirror for the same normalized Source already exists."""

    slug = "feed-exists"

    def __init__(self, existing_feed_id: str, source_url: str | None = None) -> None:
        self.existing_feed_id = existing_feed_id
        detail = "a Mirror for this Source already exists"
        if source_url:
            detail += f": {source_url}"
        super().__init__(detail, existing_feed_id=existing_feed_id)


class Unauthorized(DomainError):
    """Missing or wrong credentials (401); the API adds the Basic challenge."""

    slug = "unauthorized"
    status = 401

    def __init__(self, message: str = "authentication required") -> None:
        super().__init__(message)


class Forbidden(DomainError):
    """The Principal's role does not allow the action (403); never used to hide a Feed."""

    slug = "forbidden"
    status = 403


class OverQuota(DomainError):
    """The Owner's storage is full: new downloads and uploads are refused (507)."""

    slug = "over-quota"
    status = 507

    def __init__(self, used_bytes: int, quota_bytes: int, message: str | None = None) -> None:
        self.used_bytes = used_bytes
        self.quota_bytes = quota_bytes
        super().__init__(
            message or "over quota: free some space or ask for a larger quota",
            used_bytes=used_bytes,
            quota_bytes=quota_bytes,
        )


LOGIN_FAILED_DETAIL = "wrong username, password or code"


class LoginFailed(Unauthorized):
    """One answer for an unknown user, a wrong password and a missing or wrong code (401)."""

    slug = "login-failed"

    def __init__(self) -> None:
        super().__init__(LOGIN_FAILED_DETAIL)


class LoginLocked(DomainError):
    """Too many failed logins for this username; retry after ``retry_after`` seconds (429)."""

    slug = "login-locked"
    status = 429

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__(
            f"too many attempts; try again in {retry_after} s", retry_after=retry_after
        )


LOGIN_BUSY_RETRY_AFTER = 5


class LoginBusy(DomainError):
    """Every password-hash slot is taken; retry in a few seconds (503)."""

    slug = "login-busy"
    status = 503

    def __init__(self, retry_after: int = LOGIN_BUSY_RETRY_AFTER) -> None:
        self.retry_after = retry_after
        super().__init__(f"busy; try again in {retry_after} s", retry_after=retry_after)


class HttpsRequired(DomainError):
    """A login over plain http on a site whose ``base_url`` is https (400)."""

    slug = "https-required"
    status = 400

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        super().__init__(f"sign in over https: {base_url}")


class Unsupported(DomainError):
    """The Source cannot be mirrored (422 source-unsupported)."""

    slug = "source-unsupported"
    status = 422


class SourceKindChange(Unsupported):
    """Retargeting a Mirror to a Source of another kind is refused."""

    slug = "source-kind-change"

    def __init__(self, current: str, requested: str) -> None:
        self.current = current
        self.requested = requested
        super().__init__(
            f"cannot change a {current} Mirror into a {requested} Mirror; "
            "create a new Mirror instead"
        )


class DestinationNotAllowed(DomainError):
    """A server-side fetch would reach a refused address or scheme (422 destination-not-allowed).

    Raised by the fetch guard before a connection is opened: a loopback, private,
    link-local or otherwise non-public address (``domain.destinations``) that
    ``[security] allow_private_hosts`` does not exempt, or a scheme other than
    http(s). ``target`` is the host (and port), never the full URL, so credentials
    in a URL are not echoed.
    """

    slug = "destination-not-allowed"
    status = 422

    def __init__(self, target: str, reason: str) -> None:
        self.target = target
        self.reason = reason
        super().__init__(f"{target} is not an allowed destination: {reason}")


class InvalidSelection(DomainError):
    """A selection expression could not be parsed or resolved (422 invalid-selection)."""

    slug = "invalid-selection"
    status = 422

    def __init__(self, message: str, unresolved: Sequence[str] = ()) -> None:
        self.unresolved = list(unresolved)
        super().__init__(message, unresolved=self.unresolved)


class Ambiguous(DomainError):
    """A probe returned several candidates; the caller must pick one (422 candidates-ambiguous)."""

    slug = "candidates-ambiguous"
    status = 422

    def __init__(self, candidates: Sequence[Any], message: str | None = None) -> None:
        self.candidates = list(candidates)
        super().__init__(
            message or f"{len(self.candidates)} candidates found; pass candidate_token",
            candidates=[_dump(c) for c in self.candidates],
        )


class EngineUnavailable(DomainError):
    """The engine (yt-dlp / ffmpeg) cannot serve the request (503)."""

    slug = "engine-unavailable"
    status = 503


def _dump(candidate: Any) -> Any:
    dump = getattr(candidate, "model_dump", None)
    return dump(mode="json") if callable(dump) else candidate
