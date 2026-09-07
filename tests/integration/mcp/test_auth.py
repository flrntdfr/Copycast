"""API keys on the MCP mount: 401 without a key, tools gated by the key's scope.

The fastmcp client speaks Streamable HTTP over httpx's ASGI transport into the
FastAPI app (whose lifespan runs), so the fastmcp bearer middleware and the
key verifier are exercised exactly as an agent would.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from copycast.adapters.api.app import TaskScopedContext
from copycast.settings import Settings, get_settings
from tests.conftest import TEST_BASE_URL
from tests.integration.mcp.conftest import structured
from tests.support.paths import MCP_PATH, api

pytestmark = pytest.mark.integration

OPERATOR = ("florent", "correct-horse-battery")
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


@pytest.fixture
def settings(data_dir: Path, db: str) -> Settings:
    return get_settings(
        base_url=TEST_BASE_URL,
        data_dir=data_dir,
        database_url=db,
        refresh={"interval_hours": 24, "fetch_cooldown_minutes": 15, "concurrency": 1},
        auth={"username": OPERATOR[0], "password": OPERATOR[1]},
    )


@pytest.fixture
def operator_headers() -> dict[str, str]:
    token = base64.b64encode(f"{OPERATOR[0]}:{OPERATOR[1]}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def mcp_over_http(app: Any) -> Callable[[str | None], Client[Any]]:
    """A fastmcp client for the mounted server, carrying ``Bearer <secret>`` when given."""

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
        **kwargs: Any,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=TEST_BASE_URL,
            headers=headers,
            timeout=timeout,
            auth=auth,
            **kwargs,
        )

    def connect(secret: str | None) -> Client[Any]:
        headers = {"Authorization": f"Bearer {secret}"} if secret else None
        transport = StreamableHttpTransport(
            f"{TEST_BASE_URL}{MCP_PATH}/", headers=headers, httpx_client_factory=factory
        )
        return Client(transport)

    return connect


async def mint(client: httpx.AsyncClient, headers: dict[str, str], scope: str) -> str:
    created = await client.post(
        api("/keys"), json={"name": f"{scope} key", "scope": scope}, headers=headers
    )
    assert created.status_code == 201, created.text
    secret: str = created.json()["secret"]
    return secret


class _Session:
    """Enter and leave the fastmcp client from one task (anyio cancel scopes)."""

    def __init__(self, client: Client[Any]) -> None:
        self.client = client
        self._scope = TaskScopedContext(client)

    async def __aenter__(self) -> Client[Any]:
        await self._scope.start()
        return self.client

    async def __aexit__(self, *exc: object) -> None:
        await self._scope.stop()


async def test_mount_refuses_requests_without_a_key(client: httpx.AsyncClient) -> None:
    response = await client.post(
        MCP_PATH,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=MCP_HEADERS,
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")
    # The operator pair is for people; MCP takes keys only.
    token = base64.b64encode(f"{OPERATOR[0]}:{OPERATOR[1]}".encode()).decode()
    response = await client.post(
        MCP_PATH,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={**MCP_HEADERS, "Authorization": f"Basic {token}"},
    )
    assert response.status_code == 401


async def test_unknown_or_revoked_keys_are_refused(
    client: httpx.AsyncClient,
    operator_headers: dict[str, str],
    mcp_over_http: Callable[[str | None], Client[Any]],
) -> None:
    with pytest.raises((MCPError, RuntimeError)):
        async with _Session(mcp_over_http("cck_" + "x" * 40)) as mcp:
            await mcp.list_tools()

    secret = await mint(client, operator_headers, "read")
    async with _Session(mcp_over_http(secret)) as mcp:
        assert {t.name for t in await mcp.list_tools()} >= {"get_about", "create_mirror"}
    keys = (await client.get(api("/keys"), headers=operator_headers)).json()["keys"]
    assert keys[0]["last_used_at"] is not None
    revoked = await client.delete(api(f"/keys/{keys[0]['id']}"), headers=operator_headers)
    assert revoked.status_code == 204
    with pytest.raises((MCPError, RuntimeError)):
        async with _Session(mcp_over_http(secret)) as mcp:
            await mcp.list_tools()


async def test_tools_are_gated_by_the_key_scope(
    client: httpx.AsyncClient,
    operator_headers: dict[str, str],
    mcp_over_http: Callable[[str | None], Client[Any]],
) -> None:
    read = await mint(client, operator_headers, "read")
    write = await mint(client, operator_headers, "write")
    full = await mint(client, operator_headers, "full")

    async with _Session(mcp_over_http(read)) as mcp:
        about = structured(await mcp.call_tool("get_about"))
        assert about["auth_enabled"] is True
        tools = {t.name: t for t in await mcp.list_tools()}
        assert (tools["get_about"].meta or {})["scope"] == "read"
        assert (tools["create_inbox"].meta or {})["scope"] == "write"
        assert (tools["delete_feed"].meta or {})["scope"] == "full"
        with pytest.raises(ToolError, match="create_inbox needs an API key with scope 'write'"):
            await mcp.call_tool("create_inbox", {"inbox": {"name": "Nope"}})
        # The resource is read-only and open to every key.
        listing = await mcp.read_resource("copycast://feeds")
        assert listing and "Copycast" in str(listing[0])

    async with _Session(mcp_over_http(write)) as mcp:
        inbox = structured(await mcp.call_tool("create_inbox", {"inbox": {"name": "Later"}}))
        assert inbox["name"] == "Later"
        with pytest.raises(ToolError, match="delete_feed needs an API key with scope 'full'"):
            await mcp.call_tool("delete_feed", {"feed_id": inbox["id"], "confirm": True})
        # Retention deletes, so switching it on is a full-key action; renaming is not.
        with pytest.raises(ToolError, match=r"update_inbox \(autoprune_days\) needs .* 'full'"):
            await mcp.call_tool(
                "update_inbox", {"inbox_id": inbox["id"], "patch": {"autoprune_days": 7}}
            )
        with pytest.raises(ToolError, match=r"create_inbox \(autoprune_days\) needs .* 'full'"):
            await mcp.call_tool("create_inbox", {"inbox": {"name": "Pruned", "autoprune_days": 7}})
        # So are the Mirror modes that delete on their own.
        with pytest.raises(ToolError, match=r"update_mirror \(backfill.mode rolling\) needs"):
            await mcp.call_tool(
                "update_mirror",
                {"feed_id": "any", "patch": {"backfill": {"mode": "rolling", "latest_n": 3}}},
            )
        with pytest.raises(ToolError, match=r"create_mirror \(backfill.mode automatic\) needs"):
            await mcp.call_tool(
                "create_mirror",
                {
                    "mirror": {
                        "source_url": "https://x.example/f.xml",
                        "backfill": {"mode": "automatic"},
                    }
                },
            )
        renamed = structured(
            await mcp.call_tool(
                "update_inbox", {"inbox_id": inbox["id"], "patch": {"name": "Soon"}}
            )
        )
        assert renamed["name"] == "Soon" and renamed["autoprune_days"] is None

    async with _Session(mcp_over_http(full)) as mcp:
        pruned = structured(
            await mcp.call_tool(
                "update_inbox", {"inbox_id": inbox["id"], "patch": {"autoprune_days": 7}}
            )
        )
        assert pruned["autoprune_days"] == 7

    async with _Session(mcp_over_http(full)) as mcp:
        deleted = structured(
            await mcp.call_tool("delete_feed", {"feed_id": inbox["id"], "confirm": True})
        )
        assert deleted == {"deleted": True, "feed_id": inbox["id"]}

    # Nothing was left behind by the read key's refused call.
    feeds = (await client.get(api("/inboxes"), headers=operator_headers)).json()["feeds"]
    assert [f["name"] for f in feeds] == ["Copycast"]
