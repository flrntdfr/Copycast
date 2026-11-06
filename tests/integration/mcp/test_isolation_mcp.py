"""The isolation matrix's MCP twins: Alice's and root's keys on Bob's things.

The fastmcp client speaks Streamable HTTP into the users-mode app, with a Bearer key, so
every tool runs as the key's owner exactly as for an agent. For every tool whose
capability is ``owner_of_feed`` (read from the tools' ``meta``), ``TOOL_SAMPLES`` gives
arguments naming Bob's things: Alice's key and root's key (an Admin's key never sees more
than its owner's own Feeds) get ``[not-found]``. The ``member`` tools' answers carry none
of Bob's ids, titles or Source URL, and so do the resources; ``copycast://feeds/{bob's}``
is a not-found resource error. The test fails when an ``owner_of_feed`` tool has no
sample.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from copycast.adapters.api.app import TaskScopedContext
from copycast.application.capabilities import Access
from tests.conftest import TEST_BASE_URL
from tests.integration.api.test_isolation_matrix import received_upload
from tests.integration.mcp.conftest import structured
from tests.support.paths import MCP_PATH, api
from tests.support.world import World

pytestmark = pytest.mark.integration

Samples = Callable[[World, str], dict[str, Any]]

TOOL_SAMPLES: dict[str, Samples] = {
    "get_feed": lambda w, _: {"feed_id": w.mirror_id},
    "list_feed_objects": lambda w, _: {"feed_id": w.bucket_id},
    "delete_feed": lambda w, _: {"feed_id": w.mirror_id, "confirm": True},
    "clear_feed": lambda w, _: {"feed_id": w.bucket_id, "clear": {"dry_run": True}},
    "list_items": lambda w, _: {"feed_id": w.bucket_id},
    "get_item": lambda w, _: {"feed_id": w.bucket_id, "item_id": w.item_id},
    "archive_episodes": lambda w, _: {"feed_id": w.mirror_id, "selection": {"selection": "1"}},
    "archive_available": lambda w, _: {"feed_id": w.mirror_id},
    "retry_failed": lambda w, _: {"feed_id": w.mirror_id},
    "delete_item": lambda w, _: {"feed_id": w.bucket_id, "item_id": w.item_id},
    "fetch_item_metadata": lambda w, _: {"feed_id": w.bucket_id, "item_id": w.item_id},
    "set_archive_flag": lambda w, _: {
        "feed_id": w.bucket_id,
        "item_id": w.item_id,
        "flag": {"archive_flag": True},
    },
    "refresh_mirror": lambda w, _: {"feed_id": w.mirror_id},
    "set_mirror_paused": lambda w, _: {"feed_id": w.mirror_id, "paused": True},
    "update_mirror": lambda w, _: {"feed_id": w.mirror_id, "patch": {"title": "Mine now"}},
    "update_bucket": lambda w, _: {"bucket_id": w.bucket_id, "patch": {"name": "Mine now"}},
    "add_to_bucket": lambda w, _: {
        "bucket": w.bucket_id,
        "request": {"url": "https://www.youtube.com/watch?v=someoneelse"},
    },
    "prune_bucket": lambda w, _: {
        "bucket": w.bucket_id,
        "prune": {"older_than_days": 36500, "dry_run": True},
    },
    "add_uploads": lambda w, upload: {"bucket": w.bucket_id, "uploads": {"upload_ids": [upload]}},
    "get_job": lambda w, _: {"job_id": w.job_id},
    "cancel_job": lambda w, _: {"job_id": w.job_id},
}
"""Arguments per ``owner_of_feed`` tool: ``(world, the caller's own received upload)``."""

BY_NAME: dict[str, Samples] = {
    "add_to_bucket": lambda w, _: {
        "bucket": w.bucket_title,
        "request": {"url": "https://www.youtube.com/watch?v=someoneelse"},
    },
    "prune_bucket": lambda w, _: {"bucket": w.bucket_title, "prune": {"older_than_days": 1}},
}
"""The tools that take a Local feed by name too: Bob's name finds nothing either."""

MEMBER_CALLS: dict[str, dict[str, Any]] = {
    "list_feeds": {},
    "list_jobs": {"limit": 500},
    "storage_summary": {},
    "get_about": {},
    "get_status": {},
}


@pytest.fixture
def connect(users_app: Any) -> Callable[[str], Any]:
    """An entered fastmcp client of ``users_app`` carrying ``Bearer <secret>``."""

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
        **kwargs: Any,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=users_app),
            base_url=TEST_BASE_URL,
            headers=headers,
            timeout=timeout,
            auth=auth,
            **kwargs,
        )

    @asynccontextmanager
    async def session(secret: str) -> AsyncIterator[Client[Any]]:
        transport = StreamableHttpTransport(
            f"{TEST_BASE_URL}{MCP_PATH}/",
            headers={"Authorization": f"Bearer {secret}"},
            httpx_client_factory=factory,
        )
        client: Client[Any] = Client(transport)
        scope = TaskScopedContext(client)
        await scope.start()
        try:
            yield client
        finally:
            await scope.stop()

    return session


async def key_of(client: httpx.AsyncClient) -> str:
    created = await client.post(api("/keys"), json={"name": "isolation", "scope": "full"})
    assert created.status_code == 201, created.text
    secret: str = created.json()["secret"]
    return secret


async def owned_tools(client: Client[Any]) -> list[str]:
    return [
        tool.name
        for tool in await client.list_tools()
        if (tool.meta or {}).get("access") == Access.owner_of_feed.value
    ]


async def refusal(client: Client[Any], name: str, arguments: dict[str, Any]) -> str:
    """The ``ToolError`` text of a call that must fail (``"no error"`` when it succeeded)."""
    try:
        await client.call_tool(name, arguments)
    except ToolError as exc:
        return str(exc)
    return "no error"


@pytest.mark.parametrize("owner", ["alice", "root"])
async def test_keys_get_not_found_on_every_tool_of_bobs_things(
    world: World,
    connect: Any,
    alice_client: httpx.AsyncClient,
    root_client: httpx.AsyncClient,
    owner: str,
) -> None:
    session = alice_client if owner == "alice" else root_client
    secret = await key_of(session)
    upload_id = await received_upload(session, f"{owner}-mcp")
    failures: list[str] = []
    async with connect(secret) as client:
        tools = await owned_tools(client)
        missing = sorted(set(tools) - set(TOOL_SAMPLES))
        assert not missing, f"add TOOL_SAMPLES for {missing}"
        assert set(TOOL_SAMPLES) <= set(tools)
        cases = [(name, TOOL_SAMPLES[name]) for name in tools]
        cases += list(BY_NAME.items())
        for name, sample in cases:
            arguments = sample(world, upload_id)
            text = await refusal(client, name, arguments)
            # The error may echo what the caller sent, never anything else of Bob's.
            named = [needle for needle in world.leaks(text) if needle not in str(arguments)]
            if "[not-found]" not in text:
                failures.append(f"{name}: {text[:200]}")
            elif named:
                failures.append(f"{name}: the error names {named}")
    assert not failures, "\n".join(failures)


async def test_alices_member_tools_and_resources_never_carry_bobs_things(
    world: World, connect: Any, alice_client: httpx.AsyncClient
) -> None:
    async with connect(await key_of(alice_client)) as client:
        for name, arguments in MEMBER_CALLS.items():
            result = await client.call_tool(name, arguments)
            text = str(result.structured_content)
            assert not world.leaks(text), f"{name} leaks {world.leaks(text)}"
        for uri in ("copycast://feeds", "copycast://about"):
            contents = await client.read_resource(uri)
            text = "".join(str(getattr(part, "text", "")) for part in contents)
            assert text and not world.leaks(text), f"{uri} leaks {world.leaks(text)}"
        with pytest.raises(MCPError, match=r"not found \[not-found\]"):
            await client.read_resource(f"copycast://feeds/{world.mirror_id}")


async def test_roots_key_sees_only_roots_own_feeds(
    world: World, connect: Any, root_client: httpx.AsyncClient
) -> None:
    async with connect(await key_of(root_client)) as client:
        result = await client.call_tool("list_feeds", {})
        feeds = result.structured_content or {}
        assert {feed["owner"] for feed in feeds["feeds"]} == {"root"}
        with pytest.raises(MCPError, match=r"not found \[not-found\]"):
            await client.read_resource(f"copycast://feeds/{world.bucket_id}")


async def test_bobs_key_reaches_his_own_things(world: World, connect: Any) -> None:
    async with connect(world.key_secret) as client:
        feed = await client.call_tool("get_feed", {"feed_id": world.mirror_id})
        assert structured(feed)["owner"] == "bob"
        item = await client.call_tool(
            "get_item", {"feed_id": world.bucket_id, "item_id": world.item_id}
        )
        assert structured(item)["title"] == world.item_title
        contents = await client.read_resource(f"copycast://feeds/{world.bucket_id}")
        assert world.bucket_id in "".join(str(getattr(part, "text", "")) for part in contents)


async def test_every_tool_and_resource_declares_its_access(world: World, connect: Any) -> None:
    async with connect(world.key_secret) as client:
        for tool in await client.list_tools():
            assert (tool.meta or {}).get("access") in {a.value for a in Access}, tool.name
        for resource in [*await client.list_resources(), *await client.list_resource_templates()]:
            assert (resource.meta or {}).get("access") in {a.value for a in Access}, resource
