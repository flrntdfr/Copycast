"""httpx smoke against the compose stack (docker-compose.yml + docker-compose.ci.yml).

Covers the plan's e2e list: readiness, the default Inbox, a Mirror created from the
fixtures server that the worker archives for real (yt-dlp + ffmpeg), the public feed and
media (Range 206 counted once), the MCP endpoint's ``tools/list`` and the About engine
version against the lockfile. The Mirror is deleted at the end so the Playwright suite
that follows finds a clean stack.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx
import pytest
from packaging.version import Version
from scripts.engine_version import locked_version

from copycast.version import APP_VERSION
from tests.e2e.conftest import Stack, problem, wait_until

pytestmark = pytest.mark.e2e

ITEM_COUNT = 3  # items in tests/fixtures/rss/e2e_feed.xml
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def _rpc(method: str, request_id: int, **params: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def _mcp_result(response: httpx.Response) -> dict[str, Any]:
    """The JSON-RPC ``result`` of a stateless MCP answer (JSON or SSE-framed)."""
    assert response.status_code == 200, response.text
    if response.headers["content-type"].startswith("text/event-stream"):
        data_lines = [
            line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")
        ]
        assert data_lines, response.text
        message: dict[str, Any] = json.loads(data_lines[-1])
    else:
        message = response.json()
    assert "error" not in message, message
    result: dict[str, Any] = message["result"]
    return result


def _items(http: httpx.Client, feed_id: str, **params: Any) -> list[dict[str, Any]]:
    response = http.get(f"/api/feeds/{feed_id}/items", params=params)
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


def _item(http: httpx.Client, feed_id: str, item_id: str) -> dict[str, Any]:
    response = http.get(f"/api/feeds/{feed_id}/items/{item_id}")
    assert response.status_code == 200, response.text
    item: dict[str, Any] = response.json()
    return item


def _create_or_reuse_mirror(http: httpx.Client, source_url: str) -> dict[str, Any]:
    """POST the Mirror; a leftover from an earlier run answers 409 feed-exists."""
    # Everything: the smoke waits for every item to be archived (the default is Automatic).
    response = http.post(
        "/api/mirrors", json={"source_url": source_url, "backfill": {"mode": "all"}}
    )
    if response.status_code == 201:
        mirror: dict[str, Any] = response.json()
        # Location is built with request.url_for, so it is absolute (RFC 9110 allows both).
        location = urlsplit(response.headers["location"])
        assert location.path == f"/api/feeds/{mirror['id']}", response.headers["location"]
        return mirror
    assert response.status_code == 409, response.text
    body = problem(response)
    assert body["type"] == "urn:copycast:problem:feed-exists", body
    existing = http.get(f"/api/feeds/{body['existing_feed_id']}")
    assert existing.status_code == 200, existing.text
    reused: dict[str, Any] = existing.json()
    return reused


@pytest.fixture(scope="module")
def mirror(http: httpx.Client, stack: Stack) -> Iterator[dict[str, Any]]:
    """A Mirror of the e2e fixture feed with every item archived by the worker."""
    created = _create_or_reuse_mirror(http, stack.feed_source_url)
    feed_id: str = created["id"]

    def all_archived() -> bool:
        archived = _items(http, feed_id, state="archived", limit=50)
        failed = _items(http, feed_id, state="failed", limit=50)
        assert not failed, [(item["title"], item["last_error"]) for item in failed]
        return len(archived) == ITEM_COUNT

    wait_until(all_archived, timeout=stack.timeout, interval=2.0, what="archiving")
    refreshed = http.get(f"/api/feeds/{feed_id}")
    assert refreshed.status_code == 200, refreshed.text
    yield refreshed.json()

    deleted = http.delete(f"/api/feeds/{feed_id}")
    assert deleted.status_code == 204, deleted.text
    assert http.get(f"/api/feeds/{feed_id}").status_code == 404
    assert http.get(f"/feeds/{feed_id}.xml").status_code == 404


def test_liveness_and_readiness(http: httpx.Client, stack: Stack) -> None:
    assert http.get("/healthz/live").status_code == 200

    ready = http.get("/healthz/ready")
    assert ready.status_code == 200, ready.text
    body = ready.json()
    assert body["status"] == "ok", body
    for name in ("db", "schema", "data_dir", "layout"):
        assert body["checks"][name]["ok"], (name, body["checks"][name])

    def worker_seen() -> bool:
        checks = http.get("/healthz/ready").json()["checks"]
        seen: bool = checks["worker_seen_at"]["ok"]
        return seen

    wait_until(worker_seen, timeout=stack.timeout, interval=2.0, what="worker heartbeat")


def test_default_inbox_exists(http: httpx.Client, stack: Stack) -> None:
    response = http.get("/api/inboxes")
    assert response.status_code == 200, response.text
    feeds: list[dict[str, Any]] = response.json()["feeds"]
    inboxes = [feed for feed in feeds if feed["kind"] == "inbox"]
    assert len(inboxes) >= 1, feeds
    default = next((inbox for inbox in inboxes if inbox["name"] == "Copycast"), None)
    assert default is not None, inboxes
    assert default["feed_url"] == f"{stack.base_url}/feeds/{default['id']}.xml"

    feed = http.get(f"/feeds/{default['id']}.xml")
    assert feed.status_code == 200, feed.text
    assert feed.headers["content-type"].startswith("application/rss+xml")


def test_mirror_is_created_and_archived(
    http: httpx.Client, stack: Stack, mirror: dict[str, Any]
) -> None:
    assert mirror["kind"] == "mirror"
    assert mirror["source_kind"] == "rss"
    assert mirror["source_url"] == stack.feed_source_url
    assert mirror["title"] == "Copycast E2E Podcast"
    assert mirror["feed_url"] == f"{stack.base_url}/feeds/{mirror['id']}.xml"
    assert mirror["follow"] is True
    assert mirror["paused"] is False
    assert mirror["episode_count"] == ITEM_COUNT
    assert mirror["storage_bytes"] > 0
    assert mirror["last_refresh_success_at"] is not None
    assert mirror["last_error"] is None

    items = _items(http, mirror["id"], sort="ordinal", order="asc")
    assert [item["ordinal"] for item in items] == [1, 2, 3]
    assert [item["source_number"] for item in items] == [1, 2, 3]
    for item in items:
        assert item["state"] == "archived", item
        assert item["media"] is not None, item
        assert item["media"]["mime"].startswith("audio/"), item["media"]
        assert item["media"]["bytes"] > 0
        assert item["media"]["url"].startswith(f"{stack.base_url}/feeds/{mirror['id']}/media/")
        assert item["duration_seconds"] is not None and item["duration_seconds"] >= 1


def test_mirror_feed_links_point_home(
    http: httpx.Client, stack: Stack, mirror: dict[str, Any]
) -> None:
    response = http.get(f"/feeds/{mirror['id']}.xml")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/rss+xml")
    assert response.headers["etag"].startswith('W/"')

    conditional = http.get(
        f"/feeds/{mirror['id']}.xml", headers={"If-None-Match": response.headers["etag"]}
    )
    assert conditional.status_code == 304

    root = ElementTree.fromstring(response.content)
    channel = root.find("channel")
    assert channel is not None
    assert channel.findtext("title") == "Copycast E2E Podcast"
    enclosures = [item.find("enclosure") for item in channel.findall("item")]
    assert len(enclosures) == ITEM_COUNT
    media_prefix = f"{stack.base_url}/feeds/{mirror['id']}/media/"
    for enclosure in enclosures:
        assert enclosure is not None
        assert enclosure.get("url", "").startswith(media_prefix), enclosure.attrib
        assert enclosure.get("length") not in (None, "", "0"), enclosure.attrib

    artwork = channel.find(f"{{{ITUNES_NS}}}image")
    assert artwork is not None, "channel artwork missing from the rendered feed"
    href = artwork.get("href", "")
    assert href.startswith(f"{stack.base_url}/feeds/{mirror['id']}/assets/"), href
    served = http.get(href[len(stack.base_url) :])
    assert served.status_code == 200, served.text
    assert served.headers["content-type"].startswith("image/")


def test_media_range_counts_one_download(http: httpx.Client, mirror: dict[str, Any]) -> None:
    item = _items(http, mirror["id"], sort="ordinal", order="asc")[0]
    media_path = "/feeds/" + item["media"]["url"].split("/feeds/", 1)[1]
    before = _item(http, mirror["id"], item["id"])["download_count"]

    head = http.head(media_path)
    assert head.status_code == 200, head.text
    assert head.headers["accept-ranges"] == "bytes"
    total = int(head.headers["content-length"])
    assert total == item["media"]["bytes"] > 0

    first = http.get(media_path, headers={"Range": "bytes=0-9"})
    assert first.status_code == 206, first.text
    assert first.headers["content-range"] == f"bytes 0-9/{total}"
    assert len(first.content) == 10
    assert first.headers["content-type"] == item["media"]["mime"]

    def counted_once() -> bool:
        return _item(http, mirror["id"], item["id"])["download_count"] == before + 1

    wait_until(counted_once, timeout=30.0, interval=0.5, what="download count increment")

    later = http.get(media_path, headers={"Range": "bytes=10-19"})
    assert later.status_code == 206, later.text
    assert http.head(media_path).status_code == 200
    unsatisfiable = http.get(media_path, headers={"Range": f"bytes={total + 10}-"})
    assert unsatisfiable.status_code == 416
    assert _item(http, mirror["id"], item["id"])["download_count"] == before + 1


def test_mcp_lists_tools(http: httpx.Client) -> None:
    response = http.post("/mcp", json=_rpc("tools/list", 1), headers=MCP_HEADERS)
    tools = {tool["name"] for tool in _mcp_result(response)["tools"]}
    assert {
        "search_podcasts",
        "probe_source",
        "create_mirror",
        "archive_episodes",
        "add_to_inbox",
        "prune_inbox",
        "delete_feed",
        "get_about",
    } <= tools, tools

    about = http.post("/mcp", json=_rpc("tools/call", 2, name="get_about"), headers=MCP_HEADERS)
    result = _mcp_result(about)
    assert result.get("isError") is not True, result
    structured: dict[str, Any] = result.get("structuredContent") or {}
    if "engine" not in structured and isinstance(structured.get("result"), dict):
        structured = structured["result"]
    engine: dict[str, Any] = structured["engine"]
    assert engine["name"] == "yt-dlp", result


def test_about_reports_the_locked_engine(http: httpx.Client, stack: Stack) -> None:
    response = http.get("/api/about")
    assert response.status_code == 200, response.text
    about = response.json()

    assert about["version"] == APP_VERSION
    assert about["base_url"] == stack.base_url
    assert about["layout_version"] == "1"

    engine = about["engine"]
    assert engine["name"] == "yt-dlp"
    # A nightly locks as "X.dev0" but reports "X": compare the base versions.
    assert Version(engine["version"]).base_version == Version(locked_version()).base_version
    assert engine["channel"], engine
    assert about["ffmpeg_version"], about
    assert about["totals"]["feeds"] >= 1
