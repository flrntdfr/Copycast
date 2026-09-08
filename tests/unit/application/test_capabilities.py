from __future__ import annotations

from collections.abc import Iterator

import pytest

from copycast.application import capabilities as caps
from copycast.application.models import MirrorCreate, MirrorRead

PLAN_TOOLS = {
    "search_podcasts",
    "search_videos",
    "probe_source",
    "create_mirror",
    "list_feeds",
    "get_feed",
    "list_items",
    "get_item",
    "select_items",
    "archive_available",
    "retry_failed",
    "delete_item",
    "fetch_item_metadata",
    "request_refresh",
    "set_paused",
    "update_mirror",
    "delete_feed",
    "create_inbox",
    "update_inbox",
    "add_request",
    "prune_inbox",
    "get_job",
    "list_jobs",
    "cancel_job",
    "about",
}

OPERATOR_ONLY = {
    "rotate_feed_credentials",
    "list_api_keys",
    "create_api_key",
    "revoke_api_key",
    "get_engine_cookies",
    "set_engine_cookies",
    "delete_engine_cookies",
    "get_mirror_defaults",
    "set_mirror_defaults",
    "preview_mirror_update",
    "purge_episodes",
    "export_opml",
}
"""Routed for the UI, never tools: keys must not mint keys, agents must not rotate feeds
or touch the cookie file (a browser session)."""

PLAN_ROUTES = (
    PLAN_TOOLS
    | {
        "archive_item",
        "list_requests",
        "get_request",
        "subscribe_events",
        "rebuild",
    }
    | OPERATOR_ONLY
)


def test_sets_are_consistent_with_the_plan() -> None:
    assert {"rebuild", "archive_item", "get_request", "list_requests"} | OPERATOR_ONLY == (
        caps.TOOL_EXEMPT
    )
    assert {
        "record_download",
        "subscribe_events",
        "ensure_default_inbox",
        "resolve_inbox",
        "authenticate_api_key",
    } == caps.INTERNAL
    assert caps.TOOL_EXEMPT <= caps.CAPABILITY_NAMES
    assert caps.INTERNAL <= caps.CAPABILITY_NAMES
    assert not caps.TOOL_EXEMPT & caps.INTERNAL
    assert caps.tool_capabilities() == PLAN_TOOLS
    assert caps.routed_capabilities() == PLAN_ROUTES
    assert caps.CAPABILITY_NAMES == PLAN_ROUTES | caps.INTERNAL


@pytest.fixture
def clean_registry() -> Iterator[None]:
    """An empty registry for the test, restored afterwards (the services register on import)."""
    saved = dict(caps.CAPABILITIES)
    caps.CAPABILITIES.clear()
    try:
        yield
    finally:
        caps.CAPABILITIES.clear()
        caps.CAPABILITIES.update(saved)


def test_decorator_registers_and_is_idempotent(clean_registry: None) -> None:
    @caps.capability("create_mirror", request=MirrorCreate, response=MirrorRead)
    def create_mirror(body: MirrorCreate) -> None:
        """Create a Mirror.

        Longer explanation.
        """

    caps.capability("create_mirror")(create_mirror)
    cap = caps.get_capability("create_mirror")
    assert cap.func is create_mirror
    assert cap.request_model is MirrorCreate and cap.response_model is MirrorRead
    assert cap.description == "Create a Mirror."
    assert not cap.destructive and not cap.internal and not cap.tool_exempt
    assert caps.capability_of(create_mirror) == "create_mirror"


def test_decorator_refuses_unknown_names_and_double_registration(clean_registry: None) -> None:
    with pytest.raises(caps.UnknownCapability):
        caps.capability("make_mirror")

    @caps.capability("delete_feed")
    def first() -> None: ...

    with pytest.raises(ValueError, match="already registered"):

        @caps.capability("delete_feed")
        def second() -> None: ...

    assert caps.get_capability("delete_feed").destructive is True


def test_get_capability_before_registration(clean_registry: None) -> None:
    with pytest.raises(caps.UnknownCapability, match="no registered service"):
        caps.get_capability("about")
    with pytest.raises(caps.UnknownCapability):
        caps.check_name("bogus")
