"""Health endpoints, request ids, cache headers, the MCP mount, the About route."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from copycast.app import Container
from copycast.version import APP_VERSION
from tests.support.paths import ABOUT_PATH, HEALTH_LIVE, HEALTH_READY, MCP_PATH, api

pytestmark = pytest.mark.integration


async def test_live_and_ready(client: httpx.AsyncClient) -> None:
    live = await client.get(HEALTH_LIVE)
    assert live.status_code == 200 and live.json() == {"status": "ok"}
    assert live.headers["cache-control"] == "no-store"

    ready = await client.get(HEALTH_READY)
    assert ready.status_code == 200, ready.text
    report = ready.json()
    assert report["status"] == "ok"
    checks = report["checks"]
    assert set(checks) == {"db", "schema", "data_dir", "layout", "worker_seen_at"}
    assert all(checks[name]["ok"] for name in ("db", "schema", "data_dir", "layout"))
    # No worker ran in this test: reported, not degrading.
    assert checks["worker_seen_at"] == {"ok": False, "detail": "never"}
    assert checks["layout"]["detail"] == "1"


async def test_ready_degrades_on_layout_mismatch(
    client: httpx.AsyncClient, container: Container
) -> None:
    layout_file: Path = container.settings.data_dir / "LAYOUT_VERSION"
    layout_file.write_text("999\n", encoding="utf-8")  # noqa: ASYNC240 - test setup
    ready = await client.get(HEALTH_READY)
    assert ready.status_code == 503
    report = ready.json()
    assert report["status"] == "degraded"
    assert report["checks"]["layout"] == {"ok": False, "detail": "999"}


async def test_ready_reports_worker_heartbeat(
    client: httpx.AsyncClient, container: Container
) -> None:
    async with container.uow_factory() as uow:
        await uow.telemetry.heartbeat("w1", {"running_jobs": 0})
    ready = await client.get(HEALTH_READY)
    assert ready.status_code == 200
    seen = ready.json()["checks"]["worker_seen_at"]
    assert seen["ok"] is True and seen["detail"]


async def test_request_id_is_echoed_or_minted(client: httpx.AsyncClient) -> None:
    echoed = await client.get(ABOUT_PATH, headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["x-request-id"] == "abc-123"
    minted = await client.get(ABOUT_PATH)
    assert minted.headers["x-request-id"] and minted.headers["x-request-id"] != "abc-123"


async def test_json_responses_are_no_store(client: httpx.AsyncClient) -> None:
    about = await client.get(ABOUT_PATH)
    assert about.status_code == 200
    assert about.headers["cache-control"] == "no-store"
    body = about.json()
    assert body["version"] == APP_VERSION
    assert body["engine"]["name"] == "fake-engine"
    assert body["layout_version"] == "1"
    assert body["totals"]["feeds"] == 1  # the default Inbox


async def test_default_inbox_exists_after_lifespan(client: httpx.AsyncClient) -> None:
    response = await client.get(api("/inboxes"))
    assert response.status_code == 200
    feeds = response.json()["feeds"]
    assert len(feeds) == 1
    inbox = feeds[0]
    assert inbox["kind"] == "inbox" and inbox["name"] == "Copycast"
    assert inbox["feed_url"].endswith(f"/feeds/{inbox['id']}.xml")


async def test_mcp_is_mounted_stateless(client: httpx.AsyncClient) -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    response = await client.post(
        MCP_PATH,
        json=payload,
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert response.status_code == 200, response.text
    content_type = response.headers["content-type"]
    if content_type.startswith("text/event-stream"):
        data_lines = [
            line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")
        ]
        message = json.loads(data_lines[-1])
    else:
        message = response.json()
    names = {tool["name"] for tool in message["result"]["tools"]}
    assert {"search_podcasts", "create_mirror", "add_to_inbox", "delete_feed"} <= names
