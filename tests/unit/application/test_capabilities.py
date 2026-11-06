from __future__ import annotations

from collections.abc import Iterator

import pytest

from copycast.application import capabilities as caps
from copycast.application.models import MirrorCreate, MirrorRead
from copycast.domain.enums import PrincipalVia
from copycast.domain.exceptions import Forbidden
from copycast.domain.users import Principal

PLAN_TOOLS = {
    "search_podcasts",
    "search_videos",
    "list_youtube_playlists",
    "probe_source",
    "create_mirror",
    "list_feeds",
    "get_feed",
    "list_feed_objects",
    "storage_summary",
    "list_items",
    "get_item",
    "select_items",
    "archive_available",
    "retry_failed",
    "delete_item",
    "fetch_item_metadata",
    "set_archive_flag",
    "request_refresh",
    "set_paused",
    "update_mirror",
    "delete_feed",
    "clear_feed",
    "create_bucket",
    "update_bucket",
    "add_request",
    "prune_bucket",
    "create_upload",
    "get_upload",
    "create_serial_bucket",
    "add_uploads",
    "get_job",
    "list_jobs",
    "cancel_job",
    "about",
    "get_status",
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
    "put_upload",
}
"""Routed for the UI, never tools: keys must not mint keys, agents must not rotate feeds
or touch the cookie file (a browser session)."""

SESSION_ONLY = {"login", "logout", "get_me"}
"""Browser sessions: routed at /auth/login, /auth/logout and /api/me, never tools."""

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
    | SESSION_ONLY
)


def test_sets_are_consistent_with_the_plan() -> None:
    assert {
        "rebuild",
        "archive_item",
        "get_request",
        "list_requests",
    } | OPERATOR_ONLY | SESSION_ONLY == caps.TOOL_EXEMPT
    assert {
        "record_download",
        "subscribe_events",
        "ensure_default_bucket",
        "resolve_bucket",
        "authenticate_api_key",
        "authenticate_session",
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


# --------------------------------------------------------------------------- access


class _Ctx:
    def __init__(self, principal: Principal) -> None:
        self.principal = principal


MEMBER = Principal(username="alice", admin=False, via=PrincipalVia.session)
ADMIN = Principal(username="root", admin=True, via=PrincipalVia.session)
OPEN_ADMIN = Principal.open_admin("copycast")


def test_access_table_covers_every_capability_once() -> None:
    assert set(caps.ACCESS) == caps.CAPABILITY_NAMES
    assert {name for name, access in caps.ACCESS.items() if access is caps.Access.admin} == {
        "rebuild",
        "purge_episodes",
    }
    assert {name for name, access in caps.ACCESS.items() if access is caps.Access.system} == {
        "record_download",
        "ensure_default_bucket",
        "authenticate_api_key",
        "authenticate_session",
    }
    assert {name for name, access in caps.ACCESS.items() if access is caps.Access.public} == {
        "login",
        "put_upload",
    }
    with pytest.raises(TypeError):
        caps.ACCESS["about"] = caps.Access.admin  # type: ignore[index]


async def test_admin_capabilities_refuse_members(clean_registry: None) -> None:
    @caps.capability("rebuild")
    async def rebuild(ctx: _Ctx, flag: int = 0) -> int:
        """Rebuild."""
        return flag + 1

    assert caps.get_capability("rebuild").access is caps.Access.admin
    assert caps.get_capability("rebuild").func is rebuild
    assert caps.capability_of(rebuild) == "rebuild"
    with pytest.raises(Forbidden, match="needs an Admin"):
        await rebuild(_Ctx(MEMBER))
    assert await rebuild(_Ctx(ADMIN), flag=1) == 2
    assert await rebuild(_Ctx(OPEN_ADMIN)) == 1
    assert await rebuild(_Ctx(Principal.SYSTEM)) == 1


async def test_system_capabilities_refuse_principals(clean_registry: None) -> None:
    @caps.capability("record_download")
    async def record_download(ctx: _Ctx) -> bool:
        return True

    @caps.capability("ensure_default_bucket")
    def ensure_default_bucket(ctx: _Ctx) -> str:
        return "made"

    for principal in (MEMBER, ADMIN, OPEN_ADMIN):
        with pytest.raises(Forbidden, match="internal"):
            await record_download(_Ctx(principal))
        with pytest.raises(Forbidden, match="internal"):
            ensure_default_bucket(_Ctx(principal))
    assert await record_download(_Ctx(Principal.SYSTEM)) is True
    assert ensure_default_bucket(_Ctx(Principal.SYSTEM)) == "made"
    assert caps.get_capability("ensure_default_bucket").access is caps.Access.system


async def test_member_and_owner_capabilities_are_not_wrapped(clean_registry: None) -> None:
    async def get_feed(ctx: _Ctx) -> str:
        return "feed"

    assert caps.capability("get_feed")(get_feed) is get_feed
    assert caps.get_capability("get_feed").access is caps.Access.owner_of_feed
    assert await get_feed(_Ctx(MEMBER)) == "feed"


def test_re_registering_a_wrapped_capability_is_idempotent(clean_registry: None) -> None:
    async def purge_episodes(ctx: _Ctx) -> None: ...

    wrapped = caps.capability("purge_episodes", description="Purge.")(purge_episodes)
    again = caps.capability("purge_episodes", description="Purge.")(purge_episodes)
    assert wrapped is not purge_episodes and again is not purge_episodes
    assert caps.get_capability("purge_episodes").description == "Purge."
