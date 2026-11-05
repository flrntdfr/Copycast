"""MCP tools that fetch a Source refuse internal destinations: ``[destination-not-allowed]``."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from copycast.settings import SecuritySettings, Settings
from tests.integration.mcp.conftest import Source, structured

pytestmark = pytest.mark.integration


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """Only the name ``localhost`` is exempt: the Origin's bare loopback address is refused."""
    return settings.model_copy(
        update={"security": SecuritySettings(allow_private_hosts=["localhost"])}
    )


def by_name(url: str) -> str:
    return url.replace("://127.0.0.1:", "://localhost:", 1)


async def test_probe_create_and_retarget_tools_refuse_a_loopback_source(
    mcp_client: Client[Any], source: Source
) -> None:
    url = source.write_rss("tool", items=[1], artwork=False)

    with pytest.raises(ToolError) as probed:
        await mcp_client.call_tool("probe_source", {"url": url})
    assert "[destination-not-allowed]" in str(probed.value)
    assert "non-public" in str(probed.value)

    with pytest.raises(ToolError) as created:
        await mcp_client.call_tool("create_mirror", {"mirror": {"source_url": url}})
    assert "[destination-not-allowed]" in str(created.value)

    mirror = structured(
        await mcp_client.call_tool(
            "create_mirror",
            {"mirror": {"source_url": by_name(url), "backfill": {"mode": "selection"}}},
        )
    )
    with pytest.raises(ToolError) as retargeted:
        await mcp_client.call_tool(
            "update_mirror", {"feed_id": mirror["id"], "patch": {"source_url": url}}
        )
    assert "[destination-not-allowed]" in str(retargeted.value)
    assert source.origin.hits("/rss/tool.xml") == 1, "only the exempt name was fetched"
