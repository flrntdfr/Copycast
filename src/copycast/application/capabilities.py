"""The capability registry: one contract for the UI, the HTTP API and MCP.

Every user-facing operation is a service function decorated with
:func:`capability`. Routes and tools are thin wrappers that name the
capability (``x-capability`` / ``meta.capability``); the convergence test
checks that every non-internal capability has exactly one route and every
non-exempt one exactly one tool, both using the identical request model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, TypeVar

from pydantic import BaseModel

F = TypeVar("F", bound=Callable[..., Any])

CAPABILITY_NAMES: Final[frozenset[str]] = frozenset(
    {
        # feeds
        "list_feeds",
        "get_feed",
        "delete_feed",
        "rotate_feed_credentials",
        # items
        "list_items",
        "get_item",
        "archive_item",
        "delete_item",
        # sources
        "probe_source",
        "search_podcasts",
        "search_videos",
        # mirrors
        "create_mirror",
        "update_mirror",
        "set_paused",
        "request_refresh",
        "select_items",
        # inboxes and requests
        "create_inbox",
        "update_inbox",
        "add_request",
        "list_requests",
        "get_request",
        "prune_inbox",
        # jobs
        "list_jobs",
        "get_job",
        "cancel_job",
        # events, about, admin
        "subscribe_events",
        "about",
        "rebuild",
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
        # internal
        "record_download",
        "ensure_default_inbox",
        "resolve_inbox",
        "authenticate_api_key",
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
    }
)
"""Capabilities with a route but deliberately no MCP tool."""

INTERNAL: Final[frozenset[str]] = frozenset(
    {
        "record_download",
        "subscribe_events",
        "ensure_default_inbox",
        "resolve_inbox",
        "authenticate_api_key",
    }
)
"""Capabilities never exposed as tools; only ``subscribe_events`` has a route (SSE)."""

ROUTED_INTERNAL: Final[frozenset[str]] = frozenset({"subscribe_events"})

DESTRUCTIVE: Final[frozenset[str]] = frozenset({"delete_feed", "delete_item", "prune_inbox"})
"""Tools that carry the MCP destructive hint."""


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

    Re-decorating the same function (module re-import) is a no-op; registering
    a different function under an existing name is an error.
    """
    check_name(name)

    def decorate(func: F) -> F:
        existing = CAPABILITIES.get(name)
        if existing is not None and existing.func is not func:
            raise ValueError(f"capability {name!r} is already registered by {existing.func!r}")
        if existing is not None and request is None and response is None and not description:
            return func
        CAPABILITIES[name] = Capability(
            name=name,
            func=func,
            request_model=request,
            response_model=response,
            destructive=name in DESTRUCTIVE,
            description=description or _first_doc_line(func),
        )
        setattr(func, "__capability__", name)  # noqa: B010 - dynamic attribute on a function
        return func

    return decorate


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
    "CAPABILITIES",
    "CAPABILITY_NAMES",
    "DESTRUCTIVE",
    "INTERNAL",
    "ROUTED_INTERNAL",
    "TOOL_EXEMPT",
    "Capability",
    "UnknownCapability",
    "capability",
    "capability_of",
    "check_name",
    "get_capability",
    "routed_capabilities",
    "tool_capabilities",
]
