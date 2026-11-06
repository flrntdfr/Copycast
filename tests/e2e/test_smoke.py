"""httpx smoke against the compose stack (docker-compose.yml + docker-compose.ci.yml).

Covers the plan's e2e list: readiness, sign-in (a session for /api, 401 without a
challenge, one answer for every refused sign-in), the default Local feed, a Mirror created
from the fixtures server that the worker archives for real (yt-dlp + ffmpeg), the public feed
(its own pair) and media (the media key; Range 206 counted once), a Member who cannot see the
Admin's Mirror, the MCP endpoint's ``tools/list`` with an API key, and the About engine
version against the lockfile. The Mirror and the key are deleted at the end so the Playwright
suite that follows finds a clean stack.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

import httpx
import pytest
from packaging.version import Version
from scripts.engine_version import locked_version

from copycast.version import APP_VERSION
from tests.e2e.conftest import ADMIN, ALICE, Stack, problem, wait_until

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


def _feed_pair(feed: dict[str, Any], stack: Stack) -> tuple[str, str]:
    """A Feed's own pair, read from its ``feed_url`` (``scheme://user:pass@host/feeds/<id>.xml``)."""
    feed_url = str(feed["feed_url"])
    url = urlsplit(feed_url)
    assert url.username and url.password, feed_url
    assert f"{url.scheme}://{url.hostname}:{url.port}" == stack.base_url, feed_url
    assert url.path == f"/feeds/{feed['id']}.xml", feed_url
    return unquote(url.username), unquote(url.password)


def _media_path_prefix(feed_id: str) -> str:
    """Enclosures live under the feed's media key while authentication is on (ADR 0020)."""
    return f"/feeds/{feed_id}/k/"


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
    # Archive with Backfill: the smoke waits for every item to be archived (the default is
    # Ephemeral, which archives nothing ahead of time).
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
    # Under /feeds an unknown feed gets the same 401 as a wrong pair (ADR 0022).
    assert http.get(f"/feeds/{feed_id}.xml").status_code == 401


@pytest.fixture(scope="module")
def api_key(http: httpx.Client) -> Iterator[str]:
    """A full-scope API key of the Admin, revoked at the end."""
    response = http.post("/api/keys", json={"name": "e2e smoke", "scope": "full"})
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    yield created["secret"]
    revoked = http.delete(f"/api/keys/{created['key']['id']}")
    assert revoked.status_code == 204, revoked.text


def test_liveness_and_readiness(http: httpx.Client, stack: Stack) -> None:
    assert http.get("/healthz/live").status_code == 200

    ready = http.get("/healthz/ready")
    assert ready.status_code == 200, ready.text
    body = ready.json()
    assert body["status"] == "ok", body
    for name in ("db", "schema", "storage", "layout"):
        assert body["checks"][name]["ok"], (name, body["checks"][name])

    def worker_seen() -> bool:
        checks = http.get("/healthz/ready").json()["checks"]
        seen: bool = checks["worker_seen_at"]["ok"]
        return seen

    wait_until(worker_seen, timeout=stack.timeout, interval=2.0, what="worker heartbeat")


def test_api_needs_a_session(anonymous: httpx.Client) -> None:
    response = anonymous.get("/api/me")
    assert response.status_code == 401, response.text
    # No Basic challenge on /api: the web UI shows its login page instead of a browser prompt.
    assert "www-authenticate" not in response.headers, response.headers
    assert problem(response)["type"] == "urn:copycast:problem:unauthorized"
    # The shell and the login page hold no data and stay open.
    assert anonymous.get("/login").status_code == 200


def test_every_refused_sign_in_looks_the_same(anonymous: httpx.Client) -> None:
    # An unknown name and a wrong password get one answer (unknown names only here, so the
    # configured Users are never locked by this test).
    for username in ("nobody-e2e", "nobody-e2e-2"):
        response = anonymous.post(
            "/auth/login", json={"username": username, "password": "wrong-password"}
        )
        assert response.status_code == 401, response.text
        body = problem(response)
        assert body["type"] == "urn:copycast:problem:login-failed", body
        assert body["detail"] == "wrong username, password or code", body
        assert "set-cookie" not in response.headers


def test_me_is_the_signed_in_admin(http: httpx.Client) -> None:
    response = http.get("/api/me")
    assert response.status_code == 200, response.text
    me = response.json()
    assert me["username"] == ADMIN
    assert me["admin"] is True
    assert me["auth_mode"] == "users"
    assert me["via"] == "session"
    assert me["quota"]["state"] == "ok", me["quota"]


def test_default_local_feed_exists(
    http: httpx.Client, anonymous: httpx.Client, stack: Stack
) -> None:
    response = http.get("/api/buckets")
    assert response.status_code == 200, response.text
    feeds: list[dict[str, Any]] = response.json()["feeds"]
    # Local feeds keep the API's older kind, "bucket". Every User has their own default one,
    # and the Admin sees them all.
    defaults = {
        feed["owner"]: feed for feed in feeds if feed["kind"] == "bucket" and feed["is_default"]
    }
    assert {ADMIN, ALICE, "bob"} <= set(defaults), feeds
    default = defaults[ADMIN]
    assert default["name"] == "Copycast", default
    user, password = _feed_pair(default, stack)

    feed = anonymous.get(f"/feeds/{default['id']}.xml", auth=(user, password))
    assert feed.status_code == 200, feed.text
    assert feed.headers["content-type"].startswith("application/rss+xml")
    refused = anonymous.get(f"/feeds/{default['id']}.xml")
    assert refused.status_code == 401, refused.text
    assert refused.headers["www-authenticate"].startswith("Basic"), refused.headers
    # Its Owner's session opens the feed document too.
    assert http.get(f"/feeds/{default['id']}.xml").status_code == 200


def test_mirror_is_created_and_archived(
    http: httpx.Client, stack: Stack, mirror: dict[str, Any]
) -> None:
    assert mirror["kind"] == "mirror"
    assert mirror["source_kind"] == "rss"
    assert mirror["source_url"] == stack.feed_source_url
    assert mirror["title"] == "Copycast E2E Podcast"
    assert mirror["owner"] == ADMIN
    _feed_pair(mirror, stack)
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
        # The API's URL is the bare one (it takes the pair); the feed publishes the keyed one.
        assert item["media"]["url"].startswith(f"{stack.base_url}/feeds/{mirror['id']}/media/")
        # The web player's own URL, opened with the session (U10).
        assert item["media"]["play_url"] == (
            f"{stack.base_url}/api/feeds/{mirror['id']}/items/{item['id']}/media"
        )
        assert item["duration_seconds"] is not None and item["duration_seconds"] >= 1


def test_mirror_feed_links_point_home(
    anonymous: httpx.Client, stack: Stack, mirror: dict[str, Any]
) -> None:
    pair = _feed_pair(mirror, stack)
    http = anonymous  # a podcast app: the feed's own pair, no session
    response = http.get(f"/feeds/{mirror['id']}.xml", auth=pair)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/rss+xml")
    assert response.headers["etag"].startswith('W/"')

    conditional = http.get(
        f"/feeds/{mirror['id']}.xml",
        auth=pair,
        headers={"If-None-Match": response.headers["etag"]},
    )
    assert conditional.status_code == 304

    root = ElementTree.fromstring(response.content)
    channel = root.find("channel")
    assert channel is not None
    assert channel.findtext("title") == "Copycast E2E Podcast"
    enclosures = [item.find("enclosure") for item in channel.findall("item")]
    assert len(enclosures) == ITEM_COUNT
    media_prefix = stack.base_url + _media_path_prefix(mirror["id"])
    for enclosure in enclosures:
        assert enclosure is not None
        assert enclosure.get("url", "").startswith(media_prefix), enclosure.attrib
        assert "/media/" in enclosure.get("url", ""), enclosure.attrib
        assert enclosure.get("length") not in (None, "", "0"), enclosure.attrib

    artwork = channel.find(f"{{{ITUNES_NS}}}image")
    assert artwork is not None, "channel artwork missing from the rendered feed"
    href = artwork.get("href", "")
    # Public Artwork under its opaque token: neither the feed id nor an item id names it.
    assert href.startswith(f"{stack.base_url}/art/"), href
    assert mirror["id"] not in href, href
    assert channel.findtext(f"{{{ITUNES_NS}}}block") == "Yes"  # directories stay out
    served = http.get(href[len(stack.base_url) :])
    assert served.status_code == 200, served.text
    assert served.headers["content-type"].startswith("image/")


def test_media_range_counts_one_download(
    http: httpx.Client, anonymous: httpx.Client, stack: Stack, mirror: dict[str, Any]
) -> None:
    item = _items(http, mirror["id"], sort="ordinal", order="asc")[0]
    bare_path = "/feeds/" + item["media"]["url"].split("/feeds/", 1)[1]
    assert anonymous.head(bare_path).status_code == 401  # the bare URL takes the pair
    assert anonymous.head(bare_path, auth=_feed_pair(mirror, stack)).status_code == 200
    # The feed's keyed enclosure is its own credential: a player that sends nothing streams it.
    feed = anonymous.get(f"/feeds/{mirror['id']}.xml", auth=_feed_pair(mirror, stack))
    assert feed.status_code == 200, feed.text
    file_name = bare_path.rsplit("/", 1)[1]
    keyed = [
        str(enclosure.get("url"))
        for enclosure in ElementTree.fromstring(feed.content).iter("enclosure")
        if str(enclosure.get("url", "")).endswith(f"/media/{file_name}")
    ]
    assert len(keyed) == 1, keyed
    media_path = keyed[0][len(stack.base_url) :]
    assert media_path.startswith(_media_path_prefix(mirror["id"])), media_path
    before = _item(http, mirror["id"], item["id"])["download_count"]
    player = anonymous

    head = player.head(media_path)
    assert head.status_code == 200, head.text
    assert head.headers["accept-ranges"] == "bytes"
    total = int(head.headers["content-length"])
    assert total == item["media"]["bytes"] > 0

    first = player.get(media_path, headers={"Range": "bytes=0-9"})
    assert first.status_code == 206, first.text
    assert first.headers["content-range"] == f"bytes 0-9/{total}"
    assert len(first.content) == 10
    assert first.headers["content-type"] == item["media"]["mime"]

    def counted_once() -> bool:
        return _item(http, mirror["id"], item["id"])["download_count"] == before + 1

    wait_until(counted_once, timeout=30.0, interval=0.5, what="download count increment")

    later = player.get(media_path, headers={"Range": "bytes=10-19"})
    assert later.status_code == 206, later.text
    assert player.head(media_path).status_code == 200
    unsatisfiable = player.get(media_path, headers={"Range": f"bytes={total + 10}-"})
    assert unsatisfiable.status_code == 416
    assert _item(http, mirror["id"], item["id"])["download_count"] == before + 1


def test_a_member_never_sees_the_admins_mirror(
    alice: httpx.Client, http: httpx.Client, mirror: dict[str, Any]
) -> None:
    # Another User's Feed does not exist for a Member: 404, never 403 (ADR 0028).
    assert alice.get(f"/api/feeds/{mirror['id']}").status_code == 404
    assert alice.get(f"/api/feeds/{mirror['id']}/items").status_code == 404
    listed = alice.get("/api/feeds")
    assert listed.status_code == 200, listed.text
    feeds: list[dict[str, Any]] = listed.json()["feeds"]
    assert mirror["id"] not in {feed["id"] for feed in feeds}, feeds
    assert {feed["owner"] for feed in feeds} == {ALICE}, feeds
    # The Admin sees every User's default Local feed, alice's included.
    everything = http.get("/api/feeds", params={"kind": "bucket"}).json()["feeds"]
    assert ALICE in {feed["owner"] for feed in everything}, everything


def test_mcp_lists_tools(http: httpx.Client, anonymous: httpx.Client, api_key: str) -> None:
    refused = anonymous.post("/mcp", json=_rpc("tools/list", 1), headers=MCP_HEADERS)
    assert refused.status_code == 401, refused.text
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {api_key}"}
    http = anonymous  # an MCP client: the key alone
    response = http.post("/mcp", json=_rpc("tools/list", 1), headers=headers)
    tools = {tool["name"] for tool in _mcp_result(response)["tools"]}
    assert {
        "search_podcasts",
        "probe_source",
        "create_mirror",
        "archive_episodes",
        "add_to_bucket",
        "prune_bucket",
        "delete_feed",
        "get_about",
    } <= tools, tools

    about = http.post("/mcp", json=_rpc("tools/call", 2, name="get_about"), headers=headers)
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
    assert about["layout_version"] == "2"

    engine = about["engine"]
    assert engine["name"] == "yt-dlp"
    # A nightly locks as "X.dev0" but reports "X": compare the base versions.
    assert Version(engine["version"]).base_version == Version(locked_version()).base_version
    assert engine["channel"], engine
    assert about["ffmpeg_version"], about
    assert about["totals"]["feeds"] >= 1
