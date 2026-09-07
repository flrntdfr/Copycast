"""MCP tools end to end: search, create_mirror with a selection, archive_episodes, Inbox tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from copycast.adapters.mcp.server import INSTRUCTIONS
from copycast.adapters.mcp.tools import DELETE_FEED_WARNING
from copycast.app import Container
from copycast.worker.runner import Runner
from tests.integration.mcp.conftest import Source, structured
from tests.support.factories import listing
from tests.support.fake_engine import FakeEngine
from tests.support.origin import Origin

pytestmark = pytest.mark.integration

ITUNES_SEARCH = "https://itunes.apple.com/search"


async def test_instructions_and_tool_listing(mcp_client: Client[Any]) -> None:
    assert mcp_client.instructions == INSTRUCTIONS
    assert mcp_client.server_info is not None and mcp_client.server_info.name == "Copycast"
    tools = {tool.name: tool for tool in await mcp_client.list_tools()}
    assert {"search_podcasts", "probe_source", "create_mirror", "archive_episodes"} <= set(tools)
    assert (tools["delete_feed"].meta or {})["capability"] == "delete_feed"
    assert (tools["archive_episodes"].meta or {})["capability"] == "select_items"
    assert (tools["add_to_inbox"].meta or {})["capability"] == "add_request"
    for name in ("delete_feed", "delete_item", "prune_inbox"):
        annotations = tools[name].annotations
        assert annotations is not None and annotations.destructive_hint is True, name
    for name in ("get_feed", "list_items", "get_about", "search_podcasts"):
        annotations = tools[name].annotations
        assert annotations is not None and annotations.read_only_hint is True, name
        assert annotations.destructive_hint is False
    assert "only copy" in (tools["delete_feed"].description or "")
    add = tools["add_to_inbox"].input_schema["properties"]
    assert add["inbox"]["default"] == "Copycast"
    mirror_schema = tools["create_mirror"].input_schema["properties"]["mirror"]
    assert {"source_url", "backfill", "follow"} <= set(mirror_schema["properties"])


@respx.mock
async def test_search_podcasts(mcp_client: Client[Any], fixtures_dir: Path) -> None:
    payload = json.loads((fixtures_dir / "itunes/search.json").read_text(encoding="utf-8"))
    route = respx.get(ITUNES_SEARCH).mock(return_value=httpx.Response(200, json=payload))
    result = await mcp_client.call_tool("search_podcasts", {"query": "Accidental Tech Podcast"})
    page = structured(result)
    assert route.called
    assert page["query"] == "Accidental Tech Podcast"
    assert len(page["results"]) == 2  # the result without a feedUrl is dropped
    assert all(r["feed_url"].startswith("http") for r in page["results"])
    assert {"title", "author", "artwork_url", "itunes_id"} <= set(page["results"][0])


async def test_create_mirror_with_selection_then_archive_episodes(
    mcp_client: Client[Any], origin: Origin, runner: Runner
) -> None:
    url = origin.url_for("/rss/large_2000.xml")
    result = await mcp_client.call_tool(
        "create_mirror",
        {"mirror": {"source_url": url, "backfill": {"selection": "1-42, 180"}}},
    )
    mirror = structured(result)
    assert mirror["feed_url"] == f"http://testserver/feeds/{mirror['id']}.xml"
    assert mirror["follow"] is False and mirror["backfill"]["mode"] == "selection"
    assert mirror["counts"]["wanted"] == 43
    wanted = structured(
        await mcp_client.call_tool(
            "list_items", {"feed_id": mirror["id"], "state": "wanted", "limit": 500}
        )
    )
    assert wanted["total"] == 43
    assert sorted(i["source_number"] for i in wanted["items"]) == [*range(1, 43), 180]

    await runner.run_until_idle()
    archived = structured(
        await mcp_client.call_tool(
            "list_items", {"feed_id": mirror["id"], "state": "archived", "limit": 500}
        )
    )
    assert archived["total"] == 43

    added = structured(
        await mcp_client.call_tool(
            "archive_episodes", {"feed_id": mirror["id"], "selection": {"selection": "181"}}
        )
    )
    assert added["resolved"] and len(added["jobs"]) == 1 and added["dry_run"] is False
    await runner.run_until_idle()
    feed = structured(await mcp_client.call_tool("get_feed", {"feed_id": mirror["id"]}))
    assert feed["counts"]["archived"] == 44 and feed["selection"]["count"] == 44

    item = structured(
        await mcp_client.call_tool(
            "get_item", {"feed_id": mirror["id"], "item_id": archived["items"][0]["id"]}
        )
    )
    assert item["media"]["url"].startswith("http://testserver/feeds/")

    with pytest.raises(ToolError) as duplicate:
        await mcp_client.call_tool("create_mirror", {"mirror": {"source_url": url}})
    assert "[feed-exists]" in str(duplicate.value) and mirror["id"] in str(duplicate.value)

    refreshed = structured(await mcp_client.call_tool("refresh_mirror", {"feed_id": mirror["id"]}))
    assert refreshed["kind"] == "refresh"
    paused = structured(
        await mcp_client.call_tool("set_mirror_paused", {"feed_id": mirror["id"], "paused": True})
    )
    assert paused["paused"] is True
    updated = structured(
        await mcp_client.call_tool(
            "update_mirror", {"feed_id": mirror["id"], "patch": {"follow": True}}
        )
    )
    assert updated["follow"] is True

    with pytest.raises(ToolError) as rejected:
        await mcp_client.call_tool(
            "update_mirror",
            {"feed_id": mirror["id"], "patch": {"engine_options": {"outtmpl": "x"}}},
        )
    assert "outtmpl" in str(rejected.value)


async def test_add_to_inbox_defaults_and_prune(
    mcp_client: Client[Any], engine: FakeEngine, runner: Runner, container: Container
) -> None:
    url = "https://www.youtube.com/watch?v=mcp-video"
    engine.script_listing(url, listing(1, service="YouTube", title="A video"))
    request = structured(await mcp_client.call_tool("add_to_inbox", {"request": {"url": url}}))
    assert request["requested_via"] == "mcp" and request["status"] == "queued"
    default = structured(await mcp_client.call_tool("list_feeds", {"kind": "inbox"}))["feeds"][0]
    assert default["name"] == "Copycast" and request["inbox_id"] == default["id"]

    await runner.run_until_idle()
    items = structured(await mcp_client.call_tool("list_items", {"feed_id": default["id"]}))
    assert items["total"] == 1 and items["items"][0]["state"] == "archived"

    nothing = structured(
        await mcp_client.call_tool(
            "prune_inbox", {"inbox": "Copycast", "prune": {"downloaded": True, "dry_run": True}}
        )
    )
    assert nothing == {"matched": 0, "deleted_count": 0, "bytes_freed": 0, "dry_run": True}
    await container.services.record_download(default["id"], items["items"][0]["id"])
    pruned = structured(
        await mcp_client.call_tool(
            "prune_inbox", {"inbox": "copycast", "prune": {"downloaded": True}}
        )
    )
    assert pruned["deleted_count"] == 1 and pruned["bytes_freed"] > 0

    created = structured(
        await mcp_client.call_tool(
            "create_inbox", {"inbox": {"name": "Later", "autoprune_days": 3}}
        )
    )
    assert created["name"] == "Later" and created["autoprune_days"] == 3
    renamed = structured(
        await mcp_client.call_tool(
            "update_inbox", {"inbox_id": created["id"], "patch": {"name": "Sooner"}}
        )
    )
    assert renamed["name"] == "Sooner"
    routed = structured(
        await mcp_client.call_tool("add_to_inbox", {"request": {"url": url}, "inbox": "sooner"})
    )
    assert routed["inbox_id"] == created["id"]
    with pytest.raises(ToolError) as missing:
        await mcp_client.call_tool("add_to_inbox", {"request": {"url": url}, "inbox": "nowhere"})
    assert "[not-found]" in str(missing.value)


async def test_delete_feed_requires_confirm(
    mcp_client: Client[Any], source: Source, runner: Runner
) -> None:
    url = source.write_rss("mcp-delete", items=[1], artwork=False)
    mirror = structured(
        await mcp_client.call_tool("create_mirror", {"mirror": {"source_url": url}})
    )
    await runner.run_until_idle()

    with pytest.raises(ToolError) as refused:
        await mcp_client.call_tool("delete_feed", {"feed_id": mirror["id"]})
    assert DELETE_FEED_WARNING in str(refused.value)
    assert structured(await mcp_client.call_tool("get_feed", {"feed_id": mirror["id"]}))

    deleted = structured(
        await mcp_client.call_tool("delete_feed", {"feed_id": mirror["id"], "confirm": True})
    )
    assert deleted == {"deleted": True, "feed_id": mirror["id"]}
    with pytest.raises(ToolError) as gone:
        await mcp_client.call_tool("get_feed", {"feed_id": mirror["id"]})
    assert "[not-found]" in str(gone.value)

    default = structured(await mcp_client.call_tool("list_feeds", {"kind": "inbox"}))["feeds"][0]
    with pytest.raises(ToolError) as protected:
        await mcp_client.call_tool("delete_feed", {"feed_id": default["id"], "confirm": True})
    assert "[conflict]" in str(protected.value)


async def test_jobs_and_about(mcp_client: Client[Any], source: Source) -> None:
    url = source.write_rss("mcp-jobs", items=[2, 1], artwork=False)
    mirror = structured(
        await mcp_client.call_tool(
            "create_mirror", {"mirror": {"source_url": url, "backfill": {"selection": "1-2"}}}
        )
    )
    jobs = structured(
        await mcp_client.call_tool(
            "list_jobs", {"feed_id": mirror["id"], "kind": "archive_item", "status": "queued"}
        )
    )
    assert jobs["total"] == 2
    job = structured(await mcp_client.call_tool("get_job", {"job_id": jobs["jobs"][0]["id"]}))
    assert job["status"] == "queued"
    cancelled = structured(await mcp_client.call_tool("cancel_job", {"job_id": job["id"]}))
    assert cancelled["status"] == "cancelled"
    item_id = jobs["jobs"][1]["item_id"]
    deleted = structured(
        await mcp_client.call_tool("delete_item", {"feed_id": mirror["id"], "item_id": item_id})
    )
    assert deleted["deleted"] is True
    about = structured(await mcp_client.call_tool("get_about", {}))
    assert about["engine"]["name"] == "fake-engine" and about["layout_version"] == "1"
    with pytest.raises((ToolError, MCPError)) as bad:
        await mcp_client.call_tool("probe_source", {"url": "   "})
    assert str(bad.value)


async def test_search_videos_tool(mcp_client: Client[Any], engine: FakeEngine) -> None:
    from tests.support.factories import listing_item

    hit = listing_item(
        1,
        title="WWDC 2024 Live",
        source_url="https://www.youtube.com/watch?v=abc123",
        author="The Talk Show",
    )
    engine.script_listing("ytsearch3:gruber wwdc", listing(1, service="YouTube", items=[hit]))
    result = structured(
        await mcp_client.call_tool("search_videos", {"query": "gruber wwdc", "limit": 3})
    )
    assert [r["url"] for r in result["results"]] == ["https://www.youtube.com/watch?v=abc123"]
    tools = {tool.name: tool for tool in await mcp_client.list_tools()}
    assert (tools["search_videos"].meta or {})["capability"] == "search_videos"
    assert "add_to_inbox" in (tools["search_videos"].description or "")
