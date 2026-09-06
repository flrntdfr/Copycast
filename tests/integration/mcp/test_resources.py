"""MCP resources and the ``mirror_podcast`` prompt."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import McpError

from copycast.adapters.mcp.prompts import mirror_podcast_text
from copycast.version import APP_VERSION
from tests.integration.mcp.conftest import Source, structured

pytestmark = pytest.mark.integration


def text_of(contents: list[Any]) -> str:
    text = getattr(contents[0], "text", None)
    assert isinstance(text, str)
    return text


async def test_resources(mcp_client: Client[Any], source: Source) -> None:
    resources = {str(r.uri) for r in await mcp_client.list_resources()}
    assert {"copycast://about", "copycast://feeds"} <= resources
    templates = {t.uri_template for t in await mcp_client.list_resource_templates()}
    assert "copycast://feeds/{feed_id}" in templates

    about = json.loads(text_of(await mcp_client.read_resource("copycast://about")))
    assert about["version"] == APP_VERSION and about["engine"]["name"] == "fake-engine"

    url = source.write_rss("mcp-res", items=[1], artwork=False)
    mirror = structured(
        await mcp_client.call_tool("create_mirror", {"mirror": {"source_url": url}})
    )
    feeds = json.loads(text_of(await mcp_client.read_resource("copycast://feeds")))
    assert {f["id"] for f in feeds["feeds"]} >= {mirror["id"]}
    one = json.loads(text_of(await mcp_client.read_resource(f"copycast://feeds/{mirror['id']}")))
    assert one["id"] == mirror["id"] and one["feed_url"] == mirror["feed_url"]

    with pytest.raises(McpError) as missing:
        await mcp_client.read_resource("copycast://feeds/none-such")
    assert "not-found" in str(missing.value)


async def test_prompt(mcp_client: Client[Any]) -> None:
    prompts = {p.name for p in await mcp_client.list_prompts()}
    assert prompts == {"mirror_podcast"}
    result = await mcp_client.get_prompt("mirror_podcast", {"name": "Accidental Tech Podcast"})
    text = text_of([result.messages[0].content])
    assert text == mirror_podcast_text("Accidental Tech Podcast")
    assert "search_podcasts" in text and "create_mirror" in text and "feed_url" in text
