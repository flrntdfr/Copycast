"""The capability registry: one contract for the UI, the HTTP API and MCP.

Every user-facing operation is a service function decorated with
:func:`capability`. Routes and tools are thin wrappers that name the
capability (``x-capability`` / ``meta.capability``); the convergence test
checks that every non-internal capability has exactly one route and every
non-exempt one exactly one tool, both using the identical request model.

Every capability also declares its :class:`Access`: who may call it. The decorator
enforces the roles (``admin``, ``system``); the scoped repositories enforce the rest
(a Feed you cannot see is NotFound).
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final, TypeVar, cast

from pydantic import BaseModel

from copycast.domain.exceptions import Forbidden

F = TypeVar("F", bound=Callable[..., Any])

CAPABILITY_NAMES: Final[frozenset[str]] = frozenset(
    {
        # feeds
        "list_feeds",
        "get_feed",
        "delete_feed",
        "clear_feed",
        "rotate_feed_credentials",
        "export_opml",
        # storage
        "list_feed_objects",
        "storage_summary",
        # items
        "list_items",
        "get_item",
        "archive_item",
        "delete_item",
        "fetch_item_metadata",
        "set_archive_flag",
        # sources
        "probe_source",
        "search_podcasts",
        "search_videos",
        "list_youtube_playlists",
        # mirrors
        "create_mirror",
        "update_mirror",
        "set_paused",
        "request_refresh",
        "select_items",
        "preview_mirror_update",
        "archive_available",
        "retry_failed",
        # buckets and requests
        "create_bucket",
        "update_bucket",
        "add_request",
        "list_requests",
        "get_request",
        "prune_bucket",
        # uploads and Serial Buckets
        "create_upload",
        "put_upload",
        "get_upload",
        "create_serial_bucket",
        "add_uploads",
        # jobs
        "list_jobs",
        "get_job",
        "cancel_job",
        # events, about, admin
        "subscribe_events",
        "about",
        "get_status",
        "rebuild",
        "purge_episodes",
        # api keys
        "list_api_keys",
        "create_api_key",
        "revoke_api_key",
        # engine cookies
        "get_engine_cookies",
        "set_engine_cookies",
        "delete_engine_cookies",
        # mirror defaults
        "get_mirror_defaults",
        "set_mirror_defaults",
        # users
        "login",
        "logout",
        "get_me",
        # internal
        "record_download",
        "ensure_default_bucket",
        "resolve_bucket",
        "authenticate_api_key",
        "authenticate_session",
    }
)

TOOL_EXEMPT: Final[frozenset[str]] = frozenset(
    {
        "rebuild",
        "archive_item",
        "get_request",
        "list_requests",
        # Operator actions: a key must not mint keys (privilege escalation) and a rotation
        # silently breaks every podcast client subscribed to the feed.
        "rotate_feed_credentials",
        "list_api_keys",
        "create_api_key",
        "revoke_api_key",
        # The cookie file is a browser session: an agent neither reads nor replaces it.
        "get_engine_cookies",
        "set_engine_cookies",
        "delete_engine_cookies",
        # Operator defaults are policy, set on the Settings page.
        "get_mirror_defaults",
        "set_mirror_defaults",
        # The confirmation dialog's dry run; an agent reads the Mirror instead.
        "preview_mirror_update",
        # Wipes every archive at once: an operator's button, never an agent's tool.
        "purge_episodes",
        # A download for a podcast app; an agent has list_feeds.
        "export_opml",
        # The file's bytes go to the upload URL over HTTP; a tool argument cannot carry them.
        "put_upload",
        # Browser sessions: routed at /auth/login, /auth/logout and /api/me.
        "login",
        "logout",
        "get_me",
    }
)
"""Capabilities with a route but deliberately no MCP tool."""

INTERNAL: Final[frozenset[str]] = frozenset(
    {
        "record_download",
        "subscribe_events",
        "ensure_default_bucket",
        "resolve_bucket",
        "authenticate_api_key",
        "authenticate_session",
    }
)
"""Capabilities never exposed as tools; only ``subscribe_events`` has a route (SSE)."""

ROUTED_INTERNAL: Final[frozenset[str]] = frozenset({"subscribe_events"})

DESTRUCTIVE: Final[frozenset[str]] = frozenset(
    {"delete_feed", "clear_feed", "delete_item", "prune_bucket"}
)
"""Tools that carry the MCP destructive hint."""


class Access(StrEnum):
    """Who may call a capability."""

    public = "public"
    """No Principal needed: the call carries its own credential (a password, a token)."""
    member = "member"
    """Any User; acts on and lists their own things only."""
    owner_of_feed = "owner_of_feed"
    """Reaches a Feed, item, Request, job or upload by id: NotFound unless visible."""
    admin = "admin"
    """Forbidden (403) unless the Principal is an Admin."""
    system = "system"
    """Only ``Principal.SYSTEM`` (internal)."""


def _access_table() -> dict[str, Access]:
    table: dict[str, tuple[str, ...]] = {
        Access.public: ("login", "put_upload"),
        Access.member: (
            "list_feeds",
            "export_opml",
            "storage_summary",
            "probe_source",
            "search_podcasts",
            "search_videos",
            "list_youtube_playlists",
            "create_mirror",
            "create_bucket",
            "create_upload",
            "get_upload",
            "create_serial_bucket",
            "list_jobs",
            "subscribe_events",
            "about",
            "get_status",
            "list_api_keys",
            "create_api_key",
            "revoke_api_key",
            "get_engine_cookies",
            "set_engine_cookies",
            "delete_engine_cookies",
            "get_mirror_defaults",
            "set_mirror_defaults",
            "resolve_bucket",
            "logout",
            "get_me",
        ),
        Access.owner_of_feed: (
            "get_feed",
            "delete_feed",
            "clear_feed",
            "rotate_feed_credentials",
            "list_feed_objects",
            "list_items",
            "get_item",
            "archive_item",
            "delete_item",
            "fetch_item_metadata",
            "set_archive_flag",
            "update_mirror",
            "set_paused",
            "request_refresh",
            "select_items",
            "preview_mirror_update",
            "archive_available",
            "retry_failed",
            "update_bucket",
            "add_request",
            "list_requests",
            "get_request",
            "prune_bucket",
            "add_uploads",
            "get_job",
            "cancel_job",
        ),
        Access.admin: ("rebuild", "purge_episodes"),
        Access.system: (
            "record_download",
            "ensure_default_bucket",
            "authenticate_api_key",
            "authenticate_session",
        ),
    }
    return {name: Access(access) for access, names in table.items() for name in names}


ACCESS: Final[Mapping[str, Access]] = MappingProxyType(_access_table())
"""Capability name -> who may call it; exactly ``CAPABILITY_NAMES`` (a test asserts it)."""


def routed_capabilities() -> frozenset[str]:
    """Capabilities that must have exactly one HTTP route."""
    return (CAPABILITY_NAMES - INTERNAL) | ROUTED_INTERNAL


def tool_capabilities() -> frozenset[str]:
    """Capabilities that must have exactly one MCP tool."""
    return CAPABILITY_NAMES - INTERNAL - TOOL_EXEMPT


class UnknownCapability(ValueError):
    """A route, tool or service named a capability absent from ``CAPABILITY_NAMES``."""


@dataclass(frozen=True, slots=True)
class Capability:
    """One registered service function and the models it exchanges."""

    name: str
    func: Callable[..., Any]
    request_model: type[BaseModel] | None = None
    response_model: type[Any] | None = None
    destructive: bool = False
    description: str = ""
    access: Access = Access.member

    @property
    def internal(self) -> bool:
        return self.name in INTERNAL

    @property
    def tool_exempt(self) -> bool:
        return self.name in TOOL_EXEMPT


CAPABILITIES: Final[dict[str, Capability]] = {}
"""Name -> registered capability; filled when ``application.services`` is imported."""


def check_name(name: str) -> str:
    if name not in CAPABILITY_NAMES:
        raise UnknownCapability(f"{name!r} is not a declared capability")
    return name


def capability(
    name: str,
    *,
    request: type[BaseModel] | None = None,
    response: type[Any] | None = None,
    description: str = "",
) -> Callable[[F], F]:
    """Register ``func`` as the implementation of the capability ``name``.

    An ``admin`` capability refuses a Principal that is not an Admin, a ``system`` one
    any Principal but SYSTEM (``Forbidden``): the function is wrapped, its first
    argument being the service context, and the registry stores the wrapper.
    Re-decorating the same function (module re-import) is a no-op; registering
    a different function under an existing name is an error.
    """
    check_name(name)
    access = ACCESS[name]

    def decorate(func: F) -> F:
        existing = CAPABILITIES.get(name)
        if existing is not None and _unwrapped(existing.func) is not _unwrapped(func):
            raise ValueError(f"capability {name!r} is already registered by {existing.func!r}")
        if existing is not None and request is None and response is None and not description:
            return func
        registered = _enforce(name, access, func)
        CAPABILITIES[name] = Capability(
            name=name,
            func=registered,
            request_model=request,
            response_model=response,
            destructive=name in DESTRUCTIVE,
            description=description or _first_doc_line(func),
            access=access,
        )
        setattr(func, "__capability__", name)  # noqa: B010 - dynamic attribute on a function
        if registered is not func:
            setattr(registered, "__capability__", name)  # noqa: B010
        return registered

    return decorate


def _unwrapped(func: Callable[..., Any]) -> Callable[..., Any]:
    return inspect.unwrap(func)


def _enforce[G: Callable[..., Any]](name: str, access: Access, func: G) -> G:
    """``func`` itself, or a wrapper checking the role of ``args[0].principal`` first."""
    if access not in (Access.admin, Access.system):
        return func

    def check(ctx: Any) -> None:
        principal = ctx.principal
        if access is Access.system and not principal.is_system:
            raise Forbidden(f"{name} is internal")
        if access is Access.admin and not principal.admin:
            raise Forbidden(f"{name} needs an Admin")

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def guarded_async(*args: Any, **kwargs: Any) -> Any:
            check(args[0])
            return await func(*args, **kwargs)

        return cast(G, guarded_async)

    @functools.wraps(func)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        check(args[0])
        return func(*args, **kwargs)

    return cast(G, guarded)


def _first_doc_line(func: Callable[..., Any]) -> str:
    doc = (func.__doc__ or "").strip()
    return doc.splitlines()[0] if doc else ""


def capability_of(func: Callable[..., Any]) -> str | None:
    """The capability name a function was registered under, if any."""
    name = getattr(func, "__capability__", None)
    return name if isinstance(name, str) else None


def get_capability(name: str) -> Capability:
    check_name(name)
    try:
        return CAPABILITIES[name]
    except KeyError:
        raise UnknownCapability(f"capability {name!r} has no registered service") from None


__all__ = [
    "ACCESS",
    "CAPABILITIES",
    "CAPABILITY_NAMES",
    "DESTRUCTIVE",
    "INTERNAL",
    "ROUTED_INTERNAL",
    "TOOL_EXEMPT",
    "Access",
    "Capability",
    "UnknownCapability",
    "capability",
    "capability_of",
    "check_name",
    "get_capability",
    "routed_capabilities",
    "tool_capabilities",
]
