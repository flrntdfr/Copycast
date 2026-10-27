"""Health endpoints, request ids, cache headers, the request log, the MCP mount, the About route."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import structlog

from copycast.app import Container
from copycast.version import APP_VERSION
from tests.support.paths import (
    ABOUT_PATH,
    HEALTH_LIVE,
    HEALTH_READY,
    MCP_PATH,
    STATUS_PATH,
    api,
)

pytestmark = pytest.mark.integration


async def test_live_and_ready(client: httpx.AsyncClient, container: Container) -> None:
    live = await client.get(HEALTH_LIVE)
    assert live.status_code == 200 and live.json() == {"status": "ok"}
    assert live.headers["cache-control"] == "no-store"

    ready = await client.get(HEALTH_READY)
    assert ready.status_code == 200, ready.text
    report = ready.json()
    assert report["status"] == "ok"
    checks = report["checks"]
    assert set(checks) == {"db", "schema", "storage", "layout", "worker_seen_at"}
    assert all(checks[name]["ok"] for name in ("db", "schema", "storage", "layout"))
    assert checks["storage"]["detail"] == container.settings.storage.url
    # No worker ran in this test: reported, not degrading.
    assert checks["worker_seen_at"] == {"ok": False, "detail": "never"}
    assert checks["layout"]["detail"] == "2"


async def test_ready_degrades_on_layout_mismatch(
    client: httpx.AsyncClient, container: Container
) -> None:
    container.layout.store.put_bytes("LAYOUT_VERSION", "999\n")
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


async def test_requests_are_logged_except_health(client: httpx.AsyncClient) -> None:
    with structlog.testing.capture_logs() as captured:
        about = await client.get(ABOUT_PATH, headers={"X-Request-ID": "log-1"})
        assert about.status_code == 200
        assert (await client.get(HEALTH_READY)).status_code == 200
    lines = [event for event in captured if event["event"] == "http.request"]
    assert [event["path"] for event in lines] == [ABOUT_PATH]
    (line,) = lines
    assert line["method"] == "GET" and line["status"] == 200 and line["completed"] is True
    assert line["request_id"] == "log-1" == about.headers["x-request-id"]


async def test_json_responses_are_no_store(client: httpx.AsyncClient) -> None:
    about = await client.get(ABOUT_PATH)
    assert about.status_code == 200
    assert about.headers["cache-control"] == "no-store"
    body = about.json()
    assert body["version"] == APP_VERSION
    assert body["engine"]["name"] == "fake-engine"
    assert body["layout_version"] == "2"
    assert body["totals"]["feeds"] == 1  # the default Bucket


async def test_default_bucket_exists_after_lifespan(client: httpx.AsyncClient) -> None:
    response = await client.get(api("/buckets"))
    assert response.status_code == 200
    feeds = response.json()["feeds"]
    assert len(feeds) == 1
    bucket = feeds[0]
    assert bucket["kind"] == "bucket" and bucket["name"] == "Copycast"
    assert bucket["feed_url"].endswith(f"/feeds/{bucket['id']}.xml")


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
    assert {"search_podcasts", "create_mirror", "add_to_bucket", "delete_feed"} <= names


async def test_status_without_a_worker(client: httpx.AsyncClient) -> None:
    response = await client.get(STATUS_PATH)
    assert response.status_code == 200, response.text
    report = response.json()
    components = {c["id"]: c for c in report["components"]}
    assert list(components) == [
        "database",
        "worker",
        "scheduler",
        "queue",
        "storage",
        "engine",
        "ffmpeg",
        "crawlers",
    ]
    assert components["database"]["state"] == "ok"
    assert components["worker"]["state"] == "error"
    assert components["scheduler"]["state"] == "unknown"
    assert components["storage"]["state"] == "ok"
    assert components["crawlers"]["summary"] == "No Refresh in the last 24 h"
    assert report["state"] == "error"


async def test_status_with_a_healthy_worker(
    client: httpx.AsyncClient, container: Container
) -> None:
    now = datetime.now(UTC).isoformat()
    engine = (await client.get(ABOUT_PATH)).json()["engine"]["version"]
    async with container.uow_factory() as uow:
        await uow.telemetry.heartbeat(
            "w1",
            {
                "running_jobs": 1,
                "concurrency": 2,
                "app_version": APP_VERSION,
                "started_at": now,
                "scheduler_last_tick_at": now,
                "engine_version": engine,
                "ffmpeg_version": "7.1",
            },
        )
    report = (await client.get(STATUS_PATH)).json()
    components = {c["id"]: c for c in report["components"]}
    assert components["worker"]["state"] == "ok"
    assert components["worker"]["summary"] == "Running 1 job"
    assert components["scheduler"]["state"] == "ok"
    assert components["ffmpeg"] == {
        "id": "ffmpeg",
        "name": "ffmpeg",
        "state": "ok",
        "summary": "7.1",
        "facts": [],
    }
